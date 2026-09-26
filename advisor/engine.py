"""Season-level projection engine: shrunk history, market and club, learned on past seasons.

For every player on the list of a target season the engine predicts, per
matchday, the probability of receiving a vote, the mean pure vote, and the
expected count of every scoring event per rated appearance. It works in two
stages, and every coefficient is fitted from the seasons that precede the
target, never from the target itself:

1. History, shrunk. The player's past seasons are pooled with a per-season
   discount (`decay`), then pulled towards the role mean with the strength of a
   fixed number of pseudo-observations. A player with no history sits on the
   role mean. This is the empirical-Bayes posterior mean of a beta-binomial
   (presence), normal (vote) or gamma-Poisson (events) model.
2. Calibration. A generalised linear model per role — logistic for presence,
   weighted least squares for the vote, Poisson with exposure for each event —
   takes the shrunk history together with the opening price, the club's goals
   of last season, whether the club was promoted and whether the player
   changed club. Trained on every earlier season, it is where a newcomer's
   projection comes from: it has learned what past newcomers with that price
   in that club went on to do.

The same fit leaves residuals, and from them the engine estimates by the
method of moments how far a player's true level strays from the prediction
(`tau2`) and how noisy one matchday is (`sigma2`). The ratio is the weight of
the pre-season projection in pseudo-matchdays, which is what the in-season
update in `advisor.inseason` needs to combine it with the matchdays played.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable

import numpy as np
import pandas as pd

from .history import ROLES, SeasonFrames, season_start, team_strength

# Scoring events counted per rated appearance, with the profile scoring key
# that prices each one.
EVENTS: dict[str, str] = {
    "Gf": "goal",
    "Ass": "assist",
    "Amm": "yellow_card",
    "Esp": "red_card",
    "Au": "own_goal",
    "Gs": "goalkeeper_conceded_goal",
    "Rp": "penalty_saved",
    "R-": "penalty_missed",
}
# Events that only a goalkeeper can record in the fantasy scoring.
GOALKEEPER_EVENTS = {"Gs", "Rp"}

# Kept because each one improved the backtest; "moved" (changed club) and
# "interactions" (price x amount of history) are implemented, measured and
# left out. See MODEL.md.
FEATURES_ALL = ("history", "price", "club", "recent")


@dataclass(frozen=True)
class EngineSettings:
    decay: float = 0.45
    vote_prior_games: float = 12.0
    rate_prior_games: float = 20.0
    presence_prior_seasons: float = 1.0
    ridge: float = 10.0
    # "none": shrunk history only. "new": regression for players without a
    # rated history, shrunk history for the rest. "all": regression for all.
    regress: str = "all"
    features: tuple[str, ...] = FEATURES_ALL
    penalties: bool = True
    max_history_seasons: int = 6
    # Matchdays of a full Serie A season; a season in progress counts as the
    # fraction it has played. Set from the profile in production.
    season_days: int = 38
    # Minimum rated appearances of a training row for the vote regression.
    min_votes_train: int = 1


@dataclass
class Uncertainty:
    """Method-of-moments spread of the prediction errors, per role."""
    vote_tau2: dict[str, float] = field(default_factory=dict)
    vote_sigma2: dict[str, float] = field(default_factory=dict)
    fv_tau2: dict[str, float] = field(default_factory=dict)
    fv_sigma2: dict[str, float] = field(default_factory=dict)
    rate_var: dict[str, dict[str, float]] = field(default_factory=dict)
    presence_concentration: dict[str, float] = field(default_factory=dict)


def _decayed(ages: np.ndarray, decay: float) -> np.ndarray:
    return np.power(decay, ages)


def history_summary(ids: Iterable[int], past: list[SeasonFrames], settings: EngineSettings) -> pd.DataFrame:
    """Discounted sufficient statistics of every player's past seasons.

    Ages count back from the most recent past season (age 0). A season in
    progress is simply the youngest one with fewer matchdays.
    """
    ids = pd.Index(sorted(set(int(i) for i in ids)), name="Id")
    columns = ["n", "s_mv", "n_seas", "s_pres", "seasons", "last_pres", "last_votes"] + [f"s_{e}" for e in EVENTS]
    out = pd.DataFrame(0.0, index=ids, columns=columns)
    out["last_pres"] = np.nan
    out["last_team"] = None
    usable = [frames for frames in past if frames.stats is not None][-settings.max_history_seasons:]
    for age, frames in enumerate(reversed(usable)):
        weight = settings.decay ** age
        stats = frames.stats.set_index("Id").reindex(ids)
        present = stats.Pv.notna()
        pv = stats.Pv.fillna(0.0)
        days = stats.Squadra.map(frames.matchdays).astype(float).fillna(0.0)
        valid = present & (days > 0)
        out.loc[valid, "n"] += weight * pv[valid]
        out.loc[valid, "s_mv"] += weight * (pv * stats.Mv.fillna(0.0))[valid]
        for event in EVENTS:
            out.loc[valid, f"s_{event}"] += weight * stats[event].fillna(0.0)[valid]
        fraction = (days / settings.season_days).clip(upper=1.0)
        out.loc[valid, "n_seas"] += weight * fraction[valid]
        out.loc[valid, "s_pres"] += weight * (pv / days * fraction)[valid]
        out.loc[valid, "seasons"] += 1
        if age == 0:
            out.loc[valid, "last_pres"] = (pv / days)[valid]
            out.loc[valid, "last_votes"] = pv[valid]
            out.loc[valid, "last_team"] = stats.Squadra[valid]
    return out


@dataclass
class RoleMeans:
    vote: dict[str, float]
    rate: dict[str, dict[str, float]]
    presence: dict[str, float]


def role_means(past: list[SeasonFrames]) -> RoleMeans:
    stats = pd.concat([frames.stats.assign(_days=frames.stats.Squadra.map(frames.matchdays).fillna(38).astype(float))
                       for frames in past if frames.stats is not None], ignore_index=True)
    vote, rate, presence = {}, {}, {}
    for role in ROLES:
        group = stats[stats.R == role]
        rated = group[group.Pv > 0]
        votes = float(rated.Pv.sum())
        vote[role] = float((rated.Pv * rated.Mv).sum() / votes) if votes else 6.0
        rate[role] = {event: float(group[event].sum() / votes) if votes else 0.0 for event in EVENTS}
        days = float(group._days.sum())
        presence[role] = float(group.Pv.sum() / days) if days else 0.3
    return RoleMeans(vote, rate, presence)


def _club_table(past: list[SeasonFrames]) -> tuple[pd.DataFrame, pd.Series]:
    """Last season's goals per matchday by club, and the mean of clubs just promoted."""
    usable = [frames for frames in past if frames.stats is not None]
    last = usable[-1]
    table = team_strength(last.stats, last.matchdays)
    # A promoted club has no Serie A goals of last season; its stand-in is how
    # promoted clubs did in their first season, measured on the loaded past.
    promoted_rows = []
    for previous, current in zip(usable, usable[1:]):
        new_clubs = set(current.stats.Squadra) - set(previous.stats.Squadra)
        strength = team_strength(current.stats, current.matchdays)
        promoted_rows.append(strength.loc[strength.index.intersection(sorted(new_clubs))])
    promoted = pd.concat(promoted_rows) if promoted_rows else pd.DataFrame()
    fallback = promoted.mean() if len(promoted) else table.quantile(0.2)
    return table, fallback


def design(players: pd.DataFrame, past: list[SeasonFrames], settings: EngineSettings, means: RoleMeans) -> pd.DataFrame:
    """One row per player of the target season: shrunk history and covariates."""
    summary = history_summary(players.Id, past, settings)
    frame = players.set_index("Id")[["R", "Squadra", "QtI"]].join(summary)
    role = frame.R
    prior_vote = role.map(means.vote)
    frame["h_mv"] = (frame.s_mv + settings.vote_prior_games * prior_vote) / (frame.n + settings.vote_prior_games)
    for event in EVENTS:
        prior_rate = role.map(lambda r: means.rate[r][event])
        frame[f"h_{event}"] = (frame[f"s_{event}"] + settings.rate_prior_games * prior_rate) / (frame.n + settings.rate_prior_games)
    prior_presence = role.map(means.presence)
    frame["h_pres"] = (frame.s_pres + settings.presence_prior_seasons * prior_presence) / (frame.n_seas + settings.presence_prior_seasons)
    frame["new"] = (frame.n <= 0).astype(float)
    frame["log_price"] = np.log(frame.QtI.clip(lower=1).astype(float))
    clubs, promoted = _club_table(past)
    known = frame.Squadra.isin(clubs.index)
    frame["promoted"] = (~known).astype(float)
    frame["club_gf"] = frame.Squadra.map(clubs.gf).where(known, promoted.get("gf", 0.0)).astype(float)
    frame["club_gs"] = frame.Squadra.map(clubs.gs).where(known, promoted.get("gs", 0.0)).astype(float)
    frame["moved"] = (frame.last_team.notna() & (frame.last_team != frame.Squadra)).astype(float)
    frame["log_n"] = np.log1p(frame.n)
    return frame


def _columns(settings: EngineSettings, base: str) -> list[str]:
    columns = []
    if "history" in settings.features:
        columns += [base, "log_n"]
    columns += ["new"]
    if "price" in settings.features:
        columns += ["log_price", "new_x_price"]
    if "club" in settings.features:
        columns += ["club_gf", "club_gs", "promoted"]
    if "moved" in settings.features:
        columns += ["moved"]
    if "recent" in settings.features and base == "logit_h_pres":
        columns += ["logit_last_pres", "absent_last"]
    if "interactions" in settings.features and "price" in settings.features and "history" in settings.features:
        columns += ["price_x_log_n"]
    return columns


def _matrix(frame: pd.DataFrame, columns: list[str], base_transform=None) -> np.ndarray:
    data = frame.copy()
    data["new_x_price"] = data["new"] * data["log_price"]
    if "log_n" in data:
        data["price_x_log_n"] = data["log_price"] * data["log_n"]
    return np.column_stack([data[c].astype(float).to_numpy() for c in columns])


@dataclass
class _Scaler:
    mean: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, x: np.ndarray) -> "_Scaler":
        scale = x.std(axis=0)
        return cls(x.mean(axis=0), np.where(scale > 0, scale, 1.0))

    def __call__(self, x: np.ndarray) -> np.ndarray:
        return np.column_stack([np.ones(len(x)), (x - self.mean) / self.scale])


def _penalty(width: int, ridge: float) -> np.ndarray:
    penalty = np.eye(width) * ridge
    penalty[0, 0] = 0.0
    return penalty


def fit_wls(x: np.ndarray, y: np.ndarray, w: np.ndarray, ridge: float) -> np.ndarray:
    xtw = x.T * w
    return np.linalg.solve(xtw @ x + _penalty(x.shape[1], ridge), xtw @ y)


def fit_poisson(x: np.ndarray, counts: np.ndarray, exposure: np.ndarray, ridge: float, iterations: int = 50) -> np.ndarray:
    beta = np.zeros(x.shape[1])
    rate = counts.sum() / max(exposure.sum(), 1e-9)
    beta[0] = np.log(max(rate, 1e-6))
    for _ in range(iterations):
        eta = np.clip(x @ beta, -20, 5)
        mu = exposure * np.exp(eta)
        gradient = x.T @ (counts - mu) - _penalty(x.shape[1], ridge) @ beta
        hessian = (x.T * mu) @ x + _penalty(x.shape[1], ridge)
        step = np.linalg.solve(hessian, gradient)
        beta += step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


def fit_binomial(x: np.ndarray, successes: np.ndarray, trials: np.ndarray, ridge: float, iterations: int = 50) -> np.ndarray:
    beta = np.zeros(x.shape[1])
    for _ in range(iterations):
        p = 1 / (1 + np.exp(-np.clip(x @ beta, -30, 30)))
        gradient = x.T @ (successes - trials * p) - _penalty(x.shape[1], ridge) @ beta
        hessian = (x.T * (trials * p * (1 - p))) @ x + _penalty(x.shape[1], ridge)
        step = np.linalg.solve(hessian, gradient)
        beta += step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


def _logit(p: pd.Series) -> pd.Series:
    p = p.clip(1e-3, 1 - 1e-3)
    return np.log(p / (1 - p))


@dataclass
class TrainingSet:
    rows: pd.DataFrame  # design columns plus outcome columns, one row per player and season


def training_rows(seasons: list[SeasonFrames], settings: EngineSettings, means: RoleMeans) -> pd.DataFrame:
    """Design rows for every season with a list, a stats file and at least one earlier stats file."""
    rows = []
    for index, frames in enumerate(seasons):
        past = [s for s in seasons[:index] if s.stats is not None]
        if not past or frames.stats is None or frames.listone is None:
            continue
        players = frames.listone
        frame = design(players, past, settings, means)
        outcome = frames.stats.set_index("Id").reindex(frame.index)
        frame["y_pv"] = outcome.Pv.fillna(0.0)
        frame["y_days"] = frame.Squadra.map(frames.matchdays).fillna(38).astype(float)
        frame["y_mv"] = outcome.Mv
        for event in EVENTS:
            frame[f"y_{event}"] = outcome[event].fillna(0.0)
        frame["y_fm"] = outcome.Fm
        frame["season"] = frames.label
        rows.append(frame)
    return pd.concat(rows) if rows else pd.DataFrame()


@dataclass
class FittedEngine:
    settings: EngineSettings
    means: RoleMeans
    scoring: dict[str, float]
    models: dict[tuple[str, str], tuple[list[str], _Scaler, np.ndarray]]
    uncertainty: Uncertainty

    def predict(self, players: pd.DataFrame, past: list[SeasonFrames]) -> pd.DataFrame:
        frame = design(players, past, self.settings, self.means)
        return _predict_frame(self, frame)


def _base_column(target: str) -> str:
    return {"pres": "logit_h_pres", "mv": "h_mv"}.get(target, f"log_h_{target}")


def _augment(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["logit_h_pres"] = _logit(frame.h_pres)
    frame["absent_last"] = frame.last_pres.isna().astype(float)
    frame["logit_last_pres"] = _logit(frame.last_pres.fillna(frame.h_pres))
    for event in EVENTS:
        frame[f"log_h_{event}"] = np.log(frame[f"h_{event}"].clip(lower=1e-4))
    return frame


def _events_for(role: str) -> list[str]:
    return [e for e in EVENTS if role == "P" or e not in GOALKEEPER_EVENTS]


def fit_engine(seasons: list[SeasonFrames], scoring: dict[str, float], settings: EngineSettings = EngineSettings()) -> FittedEngine:
    """Fit every per-role model on the seasons given; they must all precede the target."""
    past = [s for s in seasons if s.stats is not None]
    means = role_means(past)
    models: dict[tuple[str, str], tuple[list[str], _Scaler, np.ndarray]] = {}
    engine = FittedEngine(settings, means, scoring, models, Uncertainty())
    if settings.regress == "none":
        return engine
    rows = training_rows(seasons, settings, means)
    if rows.empty:
        return engine
    rows = _augment(rows)
    if settings.regress == "new":
        rows = rows[rows.new > 0]
    for role in ROLES:
        group = rows[rows.R == role]
        if len(group) < 30:
            continue
        # Presence: binomial on matchdays with a vote out of matchdays played.
        columns = _columns(settings, _base_column("pres"))
        x_raw = _matrix(group, columns)
        scaler = _Scaler.fit(x_raw)
        beta = fit_binomial(scaler(x_raw), group.y_pv.to_numpy(float), group.y_days.to_numpy(float), settings.ridge)
        models[(role, "pres")] = (columns, scaler, beta)
        rated = group[group.y_pv >= settings.min_votes_train]
        if len(rated) < 30:
            continue
        columns = _columns(settings, _base_column("mv"))
        x_raw = _matrix(rated, columns)
        scaler = _Scaler.fit(x_raw)
        beta = fit_wls(scaler(x_raw), rated.y_mv.to_numpy(float), rated.y_pv.to_numpy(float), settings.ridge)
        models[(role, "mv")] = (columns, scaler, beta)
        for event in _events_for(role):
            columns = _columns(settings, _base_column(event))
            x_raw = _matrix(rated, columns)
            scaler = _Scaler.fit(x_raw)
            beta = fit_poisson(scaler(x_raw), rated[f"y_{event}"].to_numpy(float), rated.y_pv.to_numpy(float), settings.ridge)
            models[(role, event)] = (columns, scaler, beta)
    engine.uncertainty = estimate_uncertainty(engine, rows)
    return engine


def _apply(engine: FittedEngine, frame: pd.DataFrame, role: str, target: str) -> np.ndarray | None:
    model = engine.models.get((role, target))
    if model is None:
        return None
    columns, scaler, beta = model
    eta = scaler(_matrix(frame, columns)) @ beta
    if target == "pres":
        return 1 / (1 + np.exp(-eta))
    if target == "mv":
        return eta
    return np.exp(np.clip(eta, -20, 5))


def _predict_frame(engine: FittedEngine, frame: pd.DataFrame) -> pd.DataFrame:
    frame = _augment(frame)
    settings = engine.settings
    index = frame.index
    frame = frame.reset_index(drop=True)
    out = pd.DataFrame(index=frame.index)
    out["R"] = frame.R
    out["Squadra"] = frame.Squadra
    out["new"] = frame.new
    out["p_play"] = frame.h_pres
    out["mv"] = frame.h_mv
    for event in EVENTS:
        out[f"rate_{event}"] = frame[f"h_{event}"]
    targets = [("pres", "p_play"), ("mv", "mv")] + [(e, f"rate_{e}") for e in EVENTS]
    for role in ROLES:
        rows = np.flatnonzero((frame.R == role).to_numpy())
        if settings.regress == "none" or not len(rows):
            continue
        subset = frame.iloc[rows]
        use = np.ones(len(rows), dtype=bool) if settings.regress == "all" else (subset.new > 0).to_numpy()
        for target, column in targets:
            predicted = _apply(engine, subset, role, target)
            if predicted is None:
                continue
            position = out.columns.get_loc(column)
            values = out.iloc[rows, position].to_numpy(float, copy=True)
            values[use] = predicted[use]
            out.iloc[rows, position] = values
    not_keeper = out.R != "P"
    for event in GOALKEEPER_EVENTS:
        out.loc[not_keeper, f"rate_{event}"] = 0.0
    out["p_play"] = out.p_play.clip(0.01, 0.99)
    out["bonus"] = sum(out[f"rate_{e}"] * _score(engine.scoring, e, settings) for e in EVENTS)
    out["fv"] = out.mv + out.bonus
    unc = engine.uncertainty
    out["fv_tau2"] = out.R.map(unc.fv_tau2).astype(float)
    out["fv_sigma2"] = out.R.map(unc.fv_sigma2).astype(float)
    out["mv_tau2"] = out.R.map(unc.vote_tau2).astype(float)
    out["mv_sigma2"] = out.R.map(unc.vote_sigma2).astype(float)
    out["presence_concentration"] = out.R.map(unc.presence_concentration).astype(float)
    out.index = index
    return out


def _score(scoring: dict[str, float], event: str, settings: EngineSettings) -> float:
    key = EVENTS[event]
    if key in ("penalty_saved", "penalty_missed") and not settings.penalties:
        return 0.0
    return float(scoring.get(key, 0.0))


def _moments(residual2: np.ndarray, inverse_n: np.ndarray, weight: np.ndarray) -> tuple[float, float]:
    """Fit E[r^2] = tau2 + sigma2 / n by weighted least squares, both non-negative."""
    x = np.column_stack([np.ones_like(inverse_n), inverse_n])
    beta = fit_wls(x, residual2, weight, 0.0)
    tau2, sigma2 = float(beta[0]), float(beta[1])
    if sigma2 <= 0:
        sigma2 = float(np.average(residual2 / np.maximum(inverse_n, 1e-9), weights=weight)) * 0.5
    return max(tau2, 1e-4), max(sigma2, 1e-3)


def estimate_uncertainty(engine: FittedEngine, rows: pd.DataFrame) -> Uncertainty:
    """Spread of true levels around the prediction and matchday noise, from training residuals.

    The residuals are in-sample for the calibration stage, which makes the
    spread a little optimistic; the backtest measures the coverage it gives on
    seasons it has not seen.
    """
    unc = Uncertainty()
    predicted = _predict_frame(engine, rows.drop(columns=[c for c in rows.columns if c.startswith(("logit_h_", "log_h_"))]))
    for role in ROLES:
        mask = (rows.R == role).to_numpy()
        group, prediction = rows[mask], predicted[mask]
        rated = (group.y_pv >= 3).to_numpy()
        if rated.sum() >= 30:
            n = group.y_pv.to_numpy(float)[rated]
            residual = group.y_mv.to_numpy(float)[rated] - prediction.mv.to_numpy(float)[rated]
            unc.vote_tau2[role], unc.vote_sigma2[role] = _moments(residual ** 2, 1 / n, n)
            residual = group.y_fm.to_numpy(float)[rated] - prediction.fv.to_numpy(float)[rated]
            unc.fv_tau2[role], unc.fv_sigma2[role] = _moments(residual ** 2, 1 / n, n)
            unc.rate_var[role] = {}
            for event in _events_for(role):
                mu = prediction[f"rate_{event}"].to_numpy(float)[rated]
                counts = group[f"y_{event}"].to_numpy(float)[rated]
                excess = (counts - n * mu) ** 2 - n * mu
                unc.rate_var[role][event] = float(max(0.0, (excess * n ** 2).sum() / max((n ** 4).sum(), 1e-9)))
        trials = group.y_days.to_numpy(float)
        p = prediction.p_play.to_numpy(float)
        observed = ((group.y_pv.to_numpy(float) - trials * p) ** 2).sum()
        binomial = (trials * p * (1 - p)).sum()
        extra = (trials * p * (1 - p) * (trials - 1)).sum()
        # E[(y - Gp)^2] = Gp(1-p) * (1 + (G-1)/(kappa+1)) for a beta-binomial.
        ratio = (observed - binomial) / extra if extra > 0 else 0.0
        unc.presence_concentration[role] = float(1 / ratio - 1) if ratio > 1e-6 else 1e6
    return unc
