"""Season-by-season backtest of the projection models, with no information from the future.

For a target season T a model sees the statistics of the seasons before T and
the list of season T with its opening prices (`Qt.I`), which is what an
auction in August knows. It predicts, for every player on that list, the
probability of a vote per matchday and the fantasy vote per rated appearance.
The outcome is season T's statistics.

The raw data are season totals, so the check is season by season; a matchday
check needs per-matchday votes (`advisor.inseason`).

Usage:
    python -m advisor.backtest --raw-dir data/raw --profile config/default_profile.json
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .engine import EVENTS, EngineSettings, fit_engine
from .history import ROLES, SeasonFrames, load_seasons
from .league_profile import LeagueProfile

# The `Fm` column of the statistics files is computed with the standard
# Fantacalcio scoring, penalties included and no clean-sheet bonus. The backtest
# predicts that column, so it scores with the same definition, whatever the
# league's own rules are.
FM_SCORING = {"goal": 3.0, "assist": 1.0, "yellow_card": -0.5, "red_card": -1.0, "own_goal": -2.0,
              "goalkeeper_conceded_goal": -1.0, "penalty_saved": 3.0, "penalty_missed": -3.0, "clean_sheet": 0.0}
Z80 = 1.2815515655446004
Z50 = 0.6744897501960817


@dataclass
class Context:
    target: str
    past: list[SeasonFrames]
    players: pd.DataFrame
    scoring: dict[str, float]


Model = Callable[[Context], pd.DataFrame]


def contexts(seasons: dict[str, SeasonFrames], scoring: dict[str, float], minimum_past: int = 3) -> list[tuple[Context, pd.DataFrame]]:
    """Every season with a list, a stats file and enough earlier stats, with its outcome."""
    ordered = list(seasons.values())
    result = []
    for index, frames in enumerate(ordered):
        past = [s for s in ordered[:index] if s.stats is not None]
        if len(past) < minimum_past or frames.stats is None or frames.listone is None:
            continue
        players = frames.listone.copy()
        outcome = frames.stats.set_index("Id").reindex(players.Id)
        actual = pd.DataFrame({
            "R": players.set_index("Id").R,
            "QtI": players.set_index("Id").QtI.astype(float),
            "Pv": outcome.Pv.fillna(0.0),
            "days": players.set_index("Id").Squadra.map(frames.matchdays).fillna(38).astype(float),
            "Mv": outcome.Mv,
            "Fm": outcome.Fm,
        })
        result.append((Context(frames.label, past, players, scoring), actual))
    return result


# --------------------------------------------------------------------------
# Baseline: the projection of advisor.pipeline as it stood before the review.
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class BaselineCalibration:
    """Stand-ins for inputs the historical files lack, fitted on today's files.

    The baseline reads FVM, which the historical lists hold only as an
    end-of-season value, and the 1-10 club ratings of `squadre.csv`, which
    exist only for the current season. The opening price is mapped onto the
    FVM scale, and last season's goals onto the rating scale, by fits on the
    current list and `squadre.csv`.
    """
    fvm_intercept: float
    fvm_slope: float
    att_slope: float
    att_intercept: float
    dif_slope: float
    dif_intercept: float
    promoted_att: float
    promoted_dif: float
    fix_rate_scale: bool = False
    penalties: bool = False

    @classmethod
    def from_raw(cls, raw: Path, listone_label: str | None = None) -> "BaselineCalibration":
        lists = sorted(Path(raw).glob("listone_*.xlsx"))
        current = pd.read_excel(lists[-1], sheet_name="Tutti", header=1)
        current = current[(current.FVM > 0) & (current["Qt.I"] > 0)]
        slope, intercept = np.polyfit(np.log(current["Qt.I"]), np.log(current.FVM), 1)
        teams = pd.read_csv(Path(raw) / "squadre.csv")
        top = teams[teams.promossa == 0]
        att = np.polyfit(top.gf_prec / 38, top.rating_att, 1)
        dif = np.polyfit(top.gs_prec / 38, top.rating_dif, 1)
        promoted = teams[teams.promossa == 1]
        return cls(float(intercept), float(slope), float(att[0]), float(att[1]), float(dif[0]), float(dif[1]),
                   float(promoted.rating_att.mean()), float(promoted.rating_dif.mean()))


def _club_ratings(past: list[SeasonFrames], calibration: BaselineCalibration) -> pd.DataFrame:
    from .history import team_strength
    table = team_strength(past[-1].stats, past[-1].matchdays)
    return pd.DataFrame({
        "rating_att": np.clip(calibration.att_slope * table.gf + calibration.att_intercept, 1, 10),
        "rating_dif": np.clip(calibration.dif_slope * table.gs + calibration.dif_intercept, 1, 10),
    })


def baseline_model(calibration: BaselineCalibration, weights: tuple[float, ...] = (0.6, 0.3, 0.1), min_samples: int = 12) -> Model:
    """Replica of the pre-review pipeline projection, without the inputs that only exist today.

    Left out, for every model alike: the editorial starter status, the
    penalty-taker uplift and the European rotation discount, which have no
    history. Kept: fixed 60/30/10 weights over the last three seasons, the
    75-minute rate normalisation, the FVM regression for players without
    history and the formula for their presence.
    """
    def predict(ctx: Context) -> pd.DataFrame:
        histories = ctx.past[-len(weights):]
        players = ctx.players.set_index("Id")
        fvm = np.exp(calibration.fvm_intercept + calibration.fvm_slope * np.log(players.QtI.clip(lower=1).astype(float)))
        ratings = _club_ratings(ctx.past, calibration)
        att = players.Squadra.map(ratings.rating_att).fillna(calibration.promoted_att)
        dif = players.Squadra.map(ratings.rating_dif).fillna(calibration.promoted_dif)
        pv_sum = pd.Series(0.0, index=players.index)
        pv_w = pd.Series(0.0, index=players.index)
        mv_sum = pd.Series(0.0, index=players.index)
        rated_w = pd.Series(0.0, index=players.index)
        rates = {e: pd.Series(0.0, index=players.index) for e in EVENTS}
        scale = 1.0 if calibration.fix_rate_scale else 75 / 90
        for frames, weight in zip(reversed(histories), weights):
            stats = frames.stats.set_index("Id").reindex(players.index)
            present = stats.Pv.notna()
            pv_sum += (weight * stats.Pv).where(present, 0.0)
            pv_w += present * weight
            rated = present & (stats.Pv > 0)
            mv_sum += (weight * stats.Mv).where(rated, 0.0)
            rated_w += rated * weight
            for event in EVENTS:
                rates[event] += (weight * stats[event] / stats.Pv / scale).where(rated, 0.0)
        historical_p = (pv_sum / pv_w / 38).where(pv_w > 0)
        cold_p = 0.25 + 0.35 * np.minimum(fvm / 100, 1) + 0.04 * (att + dif - 10)
        p_play = historical_p.fillna(cold_p).clip(0.05, 0.95)
        mv = (mv_sum / rated_w).where(rated_w > 0, 6.0 + (att - 5.5) * 0.045)
        has_history = rated_w > 0
        out = pd.DataFrame({"R": players.R, "p_play": p_play, "mv": mv, "new": (~has_history).astype(float)})
        for event in EVENTS:
            observed = (rates[event] / rated_w).where(has_history)
            # Per-role regression on log(1 + FVM) among players with history.
            imputed = pd.Series(np.nan, index=players.index)
            for role in ROLES:
                in_role = players.R == role
                fit = in_role & has_history
                x = np.log1p(fvm[fit])
                y = observed[fit]
                if fit.sum() < min_samples or np.ptp(x) == 0 or np.ptp(y) == 0:
                    imputed[in_role] = float(y.median()) if fit.any() else 0.0
                    continue
                slope, intercept = np.polyfit(x, y, 1)
                imputed[in_role] = np.maximum(0.0, slope * np.log1p(fvm[in_role]) + intercept)
            out[f"rate_{event}"] = observed.fillna(imputed)
        for event in ("Gs",):
            out.loc[out.R != "P", f"rate_{event}"] = 0.0
        # The baseline scores goals, assists, cards, own goals and, for
        # goalkeepers, goals conceded; saved and missed penalties are ignored.
        keys = {"Gf": "goal", "Ass": "assist", "Amm": "yellow_card", "Esp": "red_card", "Au": "own_goal", "Gs": "goalkeeper_conceded_goal"}
        if calibration.penalties:
            keys.update({"Rp": "penalty_saved", "R-": "penalty_missed"})
            out.loc[out.R != "P", "rate_Rp"] = 0.0
        out["bonus"] = sum(out[f"rate_{e}"] * ctx.scoring.get(k, 0.0) for e, k in keys.items())
        out["fv"] = out.mv + out.bonus
        return out
    return predict


def engine_model(settings: EngineSettings = EngineSettings()) -> Model:
    def predict(ctx: Context) -> pd.DataFrame:
        engine = fit_engine(ctx.past, ctx.scoring, settings)
        return engine.predict(ctx.players, ctx.past)
    return predict


def steps(calibration: BaselineCalibration, season_days: int = 38, settings: EngineSettings | None = None) -> dict[str, Model]:
    """The review, one change at a time, each step adding to the one before."""
    tuned = settings or EngineSettings(season_days=season_days)
    return {
        "0 base": baseline_model(calibration),
        "1 tassi per voto": baseline_model(replace(calibration, fix_rate_scale=True)),
        "2 rigori nel bonus": baseline_model(replace(calibration, fix_rate_scale=True, penalties=True)),
        "3 storico ristretto": engine_model(replace(tuned, regress="none")),
        "4 avvio a freddo": engine_model(replace(tuned, regress="new")),
        "5 prezzo per tutti": engine_model(replace(tuned, regress="all", features=("history", "price"))),
        "6 forza squadra": engine_model(replace(tuned, regress="all", features=("history", "price", "club"))),
        "7 presenza recente": engine_model(replace(tuned, regress="all", features=("history", "price", "club", "recent"))),
    }


# Measured and rejected: each is the final engine with one change.
REJECTED = {
    "cambio squadra": ("moved",),
    "interazioni prezzo x storico": ("interactions",),
}


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def _spearman(a: pd.Series, b: pd.Series) -> float:
    if len(a) < 3:
        return float("nan")
    return float(a.rank().corr(b.rank()))


def _residual_on_price(values: pd.Series, price: pd.Series) -> pd.Series:
    x = np.log(price.clip(lower=1).astype(float))
    if np.ptp(x) == 0:
        return values - values.mean()
    slope, intercept = np.polyfit(x, values.astype(float), 1)
    return values - (slope * x + intercept)


def evaluate(prediction: pd.DataFrame, actual: pd.DataFrame, slots: dict[str, int], participants: int, subset: pd.Series | None = None) -> dict[str, float]:
    """Metrics of one model on one season, optionally restricted to a subset of players."""
    frame = actual.join(prediction[[c for c in prediction.columns if c != "R"]], how="inner")
    if subset is not None:
        frame = frame[subset.reindex(frame.index).fillna(False).astype(bool)]
    result: dict[str, float] = {"giocatori": float(len(frame))}
    if frame.empty:
        return result
    days = frame.days
    f = frame.Pv / days
    p = frame.p_play.clip(1e-4, 1 - 1e-4)
    result["presenza_logloss"] = float((-(f * np.log(p) + (1 - f) * np.log(1 - p))).mean())
    result["presenza_brier"] = float((p ** 2 - 2 * p * f + f).mean())
    bins = pd.qcut(p.rank(method="first"), 10, labels=False) if len(p) >= 20 else pd.Series(0, index=p.index)
    grouped = pd.DataFrame({"p": p, "f": f, "b": bins}).groupby("b")
    result["presenza_ece"] = float((grouped.size() / len(p) * (grouped.p.mean() - grouped.f.mean()).abs()).sum())
    rated = frame[frame.Pv > 0]
    if len(rated):
        w = rated.Pv
        result["fv_mae"] = float(np.average((rated.Fm - rated.fv).abs(), weights=w))
        result["fv_rmse"] = float(math.sqrt(np.average((rated.Fm - rated.fv) ** 2, weights=w)))
        result["fv_bias"] = float(np.average(rated.fv - rated.Fm, weights=w))
        result["mv_mae"] = float(np.average((rated.Mv - rated.mv).abs(), weights=w))
        result["bonus_mae"] = float(np.average(((rated.Fm - rated.Mv) - rated.bonus).abs(), weights=w))
    points = days * frame.p_play * frame.fv
    actual_points = (frame.Pv * frame.Fm).fillna(0.0)
    result["punti_mae"] = float((points - actual_points).abs().mean())
    result["punti_rmse"] = float(math.sqrt(((points - actual_points) ** 2).mean()))
    # Decisions: within each role, the players an auction actually prices.
    spearman, capture, edge, weights = [], [], [], []
    for role, count in slots.items():
        in_role = frame[frame.R == role]
        pool = in_role.nlargest(min(len(in_role), 2 * participants * count), "QtI")
        if len(pool) < 10:
            continue
        predicted_points = (pool.days * pool.p_play * pool.fv)
        realised = (pool.Pv * pool.Fm).fillna(0.0)
        spearman.append(_spearman(predicted_points, realised))
        k = min(len(pool), participants * count)
        chosen = realised[predicted_points.nlargest(k).index].sum()
        best = realised.nlargest(k).sum()
        capture.append(chosen / best if best > 0 else float("nan"))
        edge.append(_spearman(_residual_on_price(predicted_points, pool.QtI), _residual_on_price(realised, pool.QtI)))
        weights.append(count)
    if weights:
        result["decisione_spearman"] = float(np.average(spearman, weights=weights))
        result["decisione_topk"] = float(np.nanmean(np.array(capture)))
        result["decisione_vantaggio"] = float(np.average(edge, weights=weights))
    if "fv_tau2" in frame and frame.fv_tau2.notna().any():
        regular = frame[(frame.Pv >= 10) & frame.fv_tau2.notna()]
        if len(regular):
            z = (regular.Fm - regular.fv) / np.sqrt(regular.fv_tau2 + regular.fv_sigma2 / regular.Pv)
            result["copertura80_fv"] = float((z.abs() < Z80).mean())
            result["copertura50_fv"] = float((z.abs() < Z50).mean())
        if "presence_concentration" in frame and frame.presence_concentration.notna().any():
            kappa = frame.presence_concentration
            variance = days * p * (1 - p) * (1 + (days - 1) / (kappa + 1))
            z = (frame.Pv - days * p) / np.sqrt(variance)
            result["copertura80_presenze"] = float((z.abs() < Z80).mean())
    return result


def _has_rated_history(ctx: Context) -> pd.Series:
    """True for players with a rated appearance in any season before the target."""
    rated = set()
    for frames in ctx.past:
        rated |= set(frames.stats.loc[frames.stats.Pv > 0, "Id"])
    ids = ctx.players.Id
    return pd.Series(ids.isin(rated).to_numpy(), index=ids.to_numpy())


def run(raw: Path, models: dict[str, Model], scoring: dict[str, float], slots: dict[str, int], participants: int,
        seasons: dict[str, SeasonFrames] | None = None, only: list[str] | None = None) -> pd.DataFrame:
    """Metrics per model, season and group (tutti, nuovi, con_storico)."""
    seasons = seasons or load_seasons(raw)
    rows = []
    for ctx, actual in contexts(seasons, scoring):
        if only and ctx.target not in only:
            continue
        for name, model in models.items():
            prediction = model(ctx)
            new = ~_has_rated_history(ctx)
            for group, subset in (("tutti", None), ("nuovi", new), ("con_storico", ~new)):
                metrics = evaluate(prediction, actual, slots, participants, subset)
                rows.append({"modello": name, "stagione": ctx.target, "gruppo": group, **metrics})
    return pd.DataFrame(rows)


def summarise(results: pd.DataFrame, seasons: list[str]) -> pd.DataFrame:
    chosen = results[results.stagione.isin(seasons)]
    return chosen.drop(columns=["stagione"]).groupby(["gruppo", "modello"], sort=False).mean().round(4)


TUNING_SEASONS = ("2018-19", "2019-20", "2020-21")
TEST_SEASONS = ("2021-22", "2022-23", "2023-24", "2024-25", "2025-26")


def scoring_from_profile(profile: LeagueProfile) -> dict[str, float]:
    return {key: float(value) for key, value in profile.scoring.__dict__.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backtest stagione per stagione dei modelli di proiezione.")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--profile", type=Path, default=Path("config/default_profile.json"))
    parser.add_argument("--seasons", choices=("tuning", "test", "all"), default="test")
    parser.add_argument("--passi", action="store_true", help="Confronta la revisione un cambiamento alla volta.")
    parser.add_argument("--json", type=Path, help="Scrive tutte le metriche per stagione in questo file.")
    args = parser.parse_args(argv)
    profile = LeagueProfile.load_json(args.profile)
    scoring = FM_SCORING
    slots = {role: getattr(profile.roster_slots, role) for role in ROLES}
    participants = len(profile.participants.team_names)
    calibration = BaselineCalibration.from_raw(args.raw_dir)
    models = steps(calibration, profile.season.serie_a_matchdays) if args.passi else {
        "base": baseline_model(calibration),
        "motore": engine_model(EngineSettings(season_days=profile.season.serie_a_matchdays)),
    }
    results = run(args.raw_dir, models, scoring, slots, participants)
    chosen = {"tuning": TUNING_SEASONS, "test": TEST_SEASONS, "all": tuple(results.stagione.unique())}[args.seasons]
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(summarise(results, list(chosen)).to_string())
    if args.json:
        args.json.write_text(results.to_json(orient="records", force_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
