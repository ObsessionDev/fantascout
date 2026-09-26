"""The season in progress: matchday vote files, their import, and the Bayesian update.

Every matchday played has to move the projections, and it has to do so without
anyone rewriting the model. The pre-season projection of the engine is a prior
with a known weight, and the matchdays played are data; the update between the
two is conjugate, so it is exact, instant and needs no refit:

- presence: beta prior with the concentration measured on past seasons, plus
  the matchdays in which the player took a vote out of those his club played;
- pure vote: normal prior whose weight, in matchdays, is the matchday noise
  over the spread of true levels (`sigma2 / tau2`), plus the votes taken;
- events: gamma prior with the spread measured on past seasons, plus the
  events recorded over the rated appearances.

Two inputs are accepted, and either is enough:

1. Matchday files in `data/updates/voti/<stagione>/giornata_NN.csv`, in the
   canonical format below. `python -m advisor.aggiorna --importa` writes them
   from a canonical CSV or from a workbook in the layout of the Fantacalcio
   "voti" download (unverified, see DATA_SOURCES.md).
2. A season-to-date statistics workbook, `data/raw/statistiche_<stagione>.xlsx`,
   in the same format as the historical ones. Totals are sufficient statistics
   for the update, so this works as well as the matchday files, minus the
   per-matchday check and the recency weighting.

Canonical matchday file: one row per player who took the field, a blank
`voto` for a player without a vote (senza voto). A player who is absent from
the file did not play.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .engine import EVENTS, GOALKEEPER_EVENTS, FittedEngine, _score
from .history import SeasonFrames, read_stats

CANONICAL_COLUMNS = (
    "giornata", "id_fantacalcio", "nome", "squadra", "ruolo", "voto",
    "gol", "assist", "ammonizioni", "espulsioni", "autogol", "gol_subiti",
    "rigori_parati", "rigori_sbagliati", "rigori_segnati",
)
EVENT_COLUMNS = {
    "gol": "Gf", "assist": "Ass", "ammonizioni": "Amm", "espulsioni": "Esp", "autogol": "Au",
    "gol_subiti": "Gs", "rigori_parati": "Rp", "rigori_sbagliati": "R-", "rigori_segnati": "R+",
}
_MATCHDAY_FILE = re.compile(r"giornata_(\d+)\.csv$")


def votes_dir(raw: Path, season: str) -> Path:
    """Where the canonical matchday files of a season live, next to `data/raw`."""
    return Path(raw).parent / "updates" / "voti" / season.replace("/", "-")


def season_stats_path(raw: Path, season: str) -> Path:
    start = int(re.match(r"\d{4}", season).group(0))
    return Path(raw) / f"statistiche_{start}_{(start + 1) % 100:02d}.xlsx"


# --------------------------------------------------------------------------
# Canonical files
# --------------------------------------------------------------------------

def validate_matchday(frame: pd.DataFrame, source: str = "voti") -> pd.DataFrame:
    missing = [column for column in CANONICAL_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"{source}: mancano le colonne {missing}")
    frame = frame[list(CANONICAL_COLUMNS)].copy()
    frame["giornata"] = pd.to_numeric(frame.giornata, errors="raise").astype(int)
    if frame.giornata.nunique() != 1 or not frame.giornata.between(1, 38).all():
        raise ValueError(f"{source}: un file contiene una sola giornata, fra 1 e 38")
    frame["id_fantacalcio"] = pd.to_numeric(frame.id_fantacalcio, errors="raise").astype(int)
    if frame.id_fantacalcio.duplicated().any():
        raise ValueError(f"{source}: un giocatore compare due volte")
    frame["voto"] = pd.to_numeric(frame.voto, errors="coerce")
    if not frame.voto.dropna().between(1, 10).all():
        raise ValueError(f"{source}: voto fuori scala")
    for column in EVENT_COLUMNS:
        values = pd.to_numeric(frame[column].fillna(0), errors="raise")
        if (values < 0).any():
            raise ValueError(f"{source}: {column} negativo")
        frame[column] = values.astype(int)
    frame["ruolo"] = frame.ruolo.astype(str).str.strip().str.upper()
    if not frame.ruolo.isin(["P", "D", "C", "A"]).all():
        raise ValueError(f"{source}: ruolo diverso da P, D, C, A")
    return frame


def read_matchday(path: Path) -> pd.DataFrame:
    return validate_matchday(pd.read_csv(path), str(path))


def write_matchday(frame: pd.DataFrame, directory: Path) -> Path:
    frame = validate_matchday(frame)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"giornata_{int(frame.giornata.iloc[0]):02d}.csv"
    frame.sort_values(["squadra", "ruolo", "nome"]).to_csv(path, index=False, encoding="utf-8")
    return path


def read_matchdays(directory: Path) -> list[pd.DataFrame]:
    if not directory.is_dir():
        return []
    files = sorted((int(m.group(1)), p) for p in directory.glob("giornata_*.csv") if (m := _MATCHDAY_FILE.search(p.name)))
    return [read_matchday(path) for _, path in files]


# --------------------------------------------------------------------------
# Importer for the Fantacalcio "voti" workbook layout
# --------------------------------------------------------------------------

_XLSX_HEADERS = {"cod.": "id_fantacalcio", "ruolo": "ruolo", "nome": "nome", "voto": "voto", "gf": "gol",
                 "gs": "gol_subiti", "rp": "rigori_parati", "rs": "rigori_sbagliati", "rf": "rigori_segnati",
                 "au": "autogol", "amm": "ammonizioni", "esp": "espulsioni", "ass": "assist"}


def import_votes_workbook(path: Path, matchday: int, sheet: str | int = 0) -> pd.DataFrame:
    """Read a workbook laid out as the Fantacalcio "voti" download.

    The layout is a block per club: a row with the club name, a header row that
    starts with `Cod.`, then one row per player. `ALL` rows (the coach) are
    skipped. A vote with an asterisk, or `s.v.`, is a player without a vote.
    The layout is written from the published description of the download and
    has not been checked against a real file yet.
    """
    raw = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)
    rows, club, columns = [], None, None
    for values in raw.itertuples(index=False):
        cells = [None if (isinstance(v, float) and math.isnan(v)) else v for v in values]
        text = [str(v).strip() if v is not None else "" for v in cells]
        lowered = [t.lower() for t in text]
        if "cod." in lowered and "voto" in lowered:
            columns = {i: _XLSX_HEADERS[name] for i, name in enumerate(lowered) if name in _XLSX_HEADERS}
            continue
        filled = [t for t in text if t]
        if len(filled) == 1 and not filled[0].replace(".", "").isdigit():
            club, columns = filled[0].title(), None
            continue
        if columns is None or club is None or not filled:
            continue
        record = {name: cells[i] for i, name in columns.items()}
        if str(record.get("ruolo", "")).strip().upper() not in {"P", "D", "C", "A"}:
            continue
        vote = str(record.get("voto", "")).strip()
        record["voto"] = None if (not vote or "*" in vote or vote.lower().startswith("s")) else float(vote.replace(",", "."))
        record.update({"giornata": matchday, "squadra": club})
        rows.append(record)
    if not rows:
        raise ValueError(f"{path}: nessun blocco di voti riconosciuto (atteso un'intestazione con 'Cod.' e 'Voto')")
    frame = pd.DataFrame(rows)
    for column in CANONICAL_COLUMNS:
        if column not in frame:
            frame[column] = 0
    return validate_matchday(frame, str(path))


# --------------------------------------------------------------------------
# Season to date
# --------------------------------------------------------------------------

def aggregate_matchdays(frames: list[pd.DataFrame], half_life: float | None = None) -> tuple[pd.DataFrame, dict[str, float]]:
    """Season-to-date totals in the statistics format, and matchdays played per club.

    With `half_life` (in matchdays) older matchdays weigh less, both as data and
    as trials, which turns the totals into a recency-weighted form. It is off by
    default: its value can only be measured once matchday files exist.
    """
    if not frames:
        return pd.DataFrame(), {}
    latest = max(int(frame.giornata.iloc[0]) for frame in frames)
    weighted, played = [], {}
    for frame in frames:
        day = int(frame.giornata.iloc[0])
        weight = 1.0 if half_life is None else 0.5 ** ((latest - day) / half_life)
        for club in frame.squadra.unique():
            played[club] = played.get(club, 0.0) + weight
        rated = frame.voto.notna().astype(float)
        part = pd.DataFrame({"Id": frame.id_fantacalcio, "R": frame.ruolo, "Nome": frame.nome, "Squadra": frame.squadra,
                             "Pv": weight * rated, "_votes": weight * frame.voto.fillna(0.0)})
        for column, stat in EVENT_COLUMNS.items():
            part[stat] = weight * frame[column]
        weighted.append(part)
    stacked = pd.concat(weighted)
    last = stacked.groupby("Id")[["R", "Nome", "Squadra"]].last()
    sums = stacked.groupby("Id")[["Pv", "_votes", *EVENT_COLUMNS.values()]].sum()
    stats = last.join(sums).reset_index()
    stats["Mv"] = np.where(stats.Pv > 0, stats._votes / stats.Pv.where(stats.Pv > 0, 1), 0.0)
    stats["Rc"] = stats["R+"] + stats["R-"]
    stats["Fm"] = 0.0
    return stats.drop(columns=["_votes"]), played


def _played_from_calendar(calendar: pd.DataFrame | None) -> dict[str, float]:
    if calendar is None or "played" not in calendar:
        return {}
    played = calendar[calendar.played.astype(str).str.lower().isin(["true", "1", "yes", "si"])]
    counts: dict[str, float] = {}
    for club in pd.concat([played.home_team, played.away_team]):
        counts[club] = counts.get(club, 0.0) + 1
    return counts


def current_season(raw: Path, season: str, calendar: pd.DataFrame | None = None, half_life: float | None = None) -> SeasonFrames | None:
    """The data of the season in progress, from matchday files or a season-to-date workbook."""
    frames = read_matchdays(votes_dir(raw, season))
    if frames:
        stats, played = aggregate_matchdays(frames, half_life)
        return SeasonFrames(season.replace("/", "-"), stats, None, played)
    path = season_stats_path(raw, season)
    if not path.exists():
        return None
    stats = read_stats(path)
    played = _played_from_calendar(calendar)
    if not played:
        # Without a calendar, a club has played at least as many matchdays as
        # its most-used player has votes: a lower bound, exact when someone
        # took a vote every time.
        played = stats.groupby("Squadra").Pv.max().astype(float).to_dict()
    return SeasonFrames(season.replace("/", "-"), stats, None, played)


# --------------------------------------------------------------------------
# Conjugate update
# --------------------------------------------------------------------------

def bayes_update(prediction: pd.DataFrame, current: SeasonFrames, engine: FittedEngine) -> pd.DataFrame:
    """Posterior projection given the matchdays played so far.

    `prediction` is the engine's pre-season output, indexed by player ID. The
    matchdays a club has played are the trials for its players; a player
    absent from the season-to-date data took no vote in them.
    """
    out = prediction.copy()
    stats = current.stats.set_index("Id") if current.stats is not None and len(current.stats) else pd.DataFrame()
    teams = out["Squadra"] if "Squadra" in out else pd.Series(None, index=out.index)
    trials = teams.map(current.matchdays).astype(float).fillna(0.0)
    observed = stats.reindex(out.index) if len(stats) else pd.DataFrame(index=out.index)
    votes = observed.get("Pv", pd.Series(0.0, index=out.index)).fillna(0.0).clip(upper=trials.where(trials > 0, np.inf))
    kappa = out.presence_concentration.astype(float)
    out["p_play"] = ((out.p_play * kappa + votes) / (kappa + trials)).clip(0.01, 0.99)
    weight = (out.mv_sigma2 / out.mv_tau2).astype(float)
    mean_vote = observed.get("Mv", pd.Series(np.nan, index=out.index))
    rated = votes > 0
    out.loc[rated, "mv"] = (weight * out.mv + votes * mean_vote.fillna(0.0))[rated] / (weight + votes)[rated]
    shrink = 1 / (1 + votes / weight)
    out["mv_tau2"] = out.mv_tau2 * shrink
    out["fv_tau2"] = out.fv_tau2 * shrink
    for event in EVENTS:
        column = f"rate_{event}"
        counts = observed.get(event, pd.Series(0.0, index=out.index)).fillna(0.0)
        variance = out.R.map(lambda role: engine.uncertainty.rate_var.get(role, {}).get(event, 0.0)).astype(float)
        mu = out[column].astype(float)
        informative = (variance > 0) & (mu > 0) & rated
        alpha = mu ** 2 / variance.where(informative, 1.0)
        beta = mu / variance.where(informative, 1.0)
        out.loc[informative, column] = ((alpha + counts) / (beta + votes))[informative]
        if event in GOALKEEPER_EVENTS:
            out.loc[out.R != "P", column] = 0.0
    out["bonus"] = sum(out[f"rate_{e}"] * _score(engine.scoring, e, engine.settings) for e in EVENTS)
    out["fv"] = out.mv + out.bonus
    out["giornate_osservate"] = trials
    out["presenze_osservate"] = votes
    return out


@dataclass
class MatchdayCheck:
    matchdays: int
    presence_logloss: float
    fantavoto_mae: float


def matchday_backtest(prediction: pd.DataFrame, frames: list[pd.DataFrame], engine: FittedEngine, scoring: dict[str, float]) -> MatchdayCheck:
    """Matchday-by-matchday check: each matchday predicted from the ones before it only.

    This is the check the season totals cannot give. It runs as soon as a
    season of matchday files exists; until then it is exercised by the tests.
    """
    losses, errors = [], []
    ordered = sorted(frames, key=lambda frame: int(frame.giornata.iloc[0]))
    for index, frame in enumerate(ordered):
        before = ordered[:index]
        stats, played = aggregate_matchdays(before) if before else (pd.DataFrame(), {})
        posterior = bayes_update(prediction, SeasonFrames("corrente", stats, None, played), engine) if before else prediction
        clubs = set(frame.squadra)
        pool = posterior[posterior.Squadra.isin(clubs)]
        took_vote = pd.Series(0.0, index=pool.index)
        rated = frame[frame.voto.notna()].set_index("id_fantacalcio")
        took_vote[took_vote.index.isin(rated.index)] = 1.0
        p = pool.p_play.clip(1e-4, 1 - 1e-4)
        losses.extend(-(took_vote * np.log(p) + (1 - took_vote) * np.log(1 - p)))
        common = rated.index.intersection(pool.index)
        fantavoto = rated.loc[common, "voto"] + sum(rated.loc[common, column] * scoring.get(key, 0.0) for column, key in (
            ("gol", "goal"), ("assist", "assist"), ("ammonizioni", "yellow_card"), ("espulsioni", "red_card"), ("autogol", "own_goal"),
            ("gol_subiti", "goalkeeper_conceded_goal"), ("rigori_parati", "penalty_saved"), ("rigori_sbagliati", "penalty_missed")))
        errors.extend((fantavoto - pool.loc[common, "fv"]).abs())
    return MatchdayCheck(len(ordered), float(np.mean(losses)) if losses else float("nan"), float(np.mean(errors)) if errors else float("nan"))
