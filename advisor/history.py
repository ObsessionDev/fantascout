"""All historical seasons as one panel, for the projection engine and the backtest.

The raw workbooks hold season totals only: one row per player and season, with
rated appearances (`Pv`), mean vote (`Mv`), fantasy mean (`Fm`) and event
counts. There is no per-matchday vote, no minutes and no historical starter
list, so everything here works at the level of a season.

Two facts about the files decide what may be used before a season starts:

- The historical player lists were downloaded at the end of their season. `Qt.A`
  and `FVM` are end-of-season values and would leak the outcome; `Qt.I`, the
  opening quotation, is the only market signal that existed on day one.
- The list of a season is `Tutti` plus `Ceduti`: a player sold in January was
  still there in August. The club is the end-of-season one, a small and
  acknowledged leak for players who moved in January.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

STAT_FIELDS = ("Pv", "Mv", "Fm", "Gf", "Gs", "Rp", "Rc", "R+", "R-", "Ass", "Amm", "Esp", "Au")
ROLES = ("P", "D", "C", "A")
_SEASON_FILE = re.compile(r"(?P<kind>statistiche|listone)_(?P<start>\d{4})_(?P<end>\d{2})\.xlsx$")


def season_label(start: int) -> str:
    return f"{start}-{(start + 1) % 100:02d}"


def season_start(label: str) -> int:
    return int(re.match(r"\d{4}", label).group(0))


@dataclass
class SeasonFrames:
    label: str
    stats: pd.DataFrame | None = None
    listone: pd.DataFrame | None = None
    # Matchdays each club had played when the stats were taken. A finished
    # season is the configured length; a season in progress is shorter.
    matchdays: dict[str, int] = field(default_factory=dict)


def _read_sheet(path: Path, sheet: str) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=sheet, header=1)


def read_listone(path: Path) -> pd.DataFrame:
    """Everyone on the list of that season, sold players included, with the opening price."""
    workbook = pd.ExcelFile(path)
    frames = [_read_sheet(path, "Tutti").assign(ceduto=False)]
    if "Ceduti" in workbook.sheet_names:
        sold = _read_sheet(path, "Ceduti")
        if "Id" in sold and len(sold):
            frames.append(sold.assign(ceduto=True))
    listone = pd.concat(frames, ignore_index=True)
    listone = listone.dropna(subset=["Id"]).drop_duplicates("Id", keep="first")
    listone["Id"] = listone.Id.astype(int)
    return listone.rename(columns={"Qt.I": "QtI", "Qt.A": "QtA"})[["Id", "R", "Nome", "Squadra", "QtI", "QtA", "ceduto"]]


def read_stats(path: Path) -> pd.DataFrame:
    stats = _read_sheet(path, "Tutti").dropna(subset=["Id"])
    stats["Id"] = stats.Id.astype(int)
    for column in STAT_FIELDS:
        stats[column] = pd.to_numeric(stats[column], errors="coerce").fillna(0.0)
    return stats[["Id", "R", "Nome", "Squadra", *STAT_FIELDS]].drop_duplicates("Id")


def load_seasons(raw: Path, season_days: int = 38) -> dict[str, SeasonFrames]:
    """Every `statistiche_*` and `listone_*` workbook in `raw`, keyed by season label."""
    seasons: dict[str, SeasonFrames] = {}
    for path in sorted(Path(raw).glob("*.xlsx")):
        match = _SEASON_FILE.search(path.name)
        if not match:
            continue
        label = season_label(int(match["start"]))
        frames = seasons.setdefault(label, SeasonFrames(label))
        if match["kind"] == "statistiche":
            frames.stats = read_stats(path)
            frames.matchdays = {team: season_days for team in frames.stats.Squadra.unique()}
        else:
            frames.listone = read_listone(path)
    return dict(sorted(seasons.items(), key=lambda item: season_start(item[0])))


def team_strength(stats: pd.DataFrame, matchdays: dict[str, int] | None = None) -> pd.DataFrame:
    """Goals scored and conceded per matchday by each club, from the season totals.

    Scored is the sum of the players' goals; conceded is the sum over the
    club's goalkeepers. Own goals scored for a club are not attributed to it,
    so the scale is slightly low; it is used only relative to other clubs.
    """
    scored = stats.groupby("Squadra").Gf.sum()
    conceded = stats[stats.R == "P"].groupby("Squadra").Gs.sum().reindex(scored.index).fillna(0.0)
    days = pd.Series({team: (matchdays or {}).get(team, 38) for team in scored.index}, dtype=float)
    days = days.where(days > 0, np.nan)
    return pd.DataFrame({"gf": scored / days, "gs": conceded / days}).fillna(0.0)
