"""The projection of the current season, as the pipeline asks for it.

Trains the engine on every historical season found in the raw directory,
projects the current list, and folds in the matchdays already played when
there are any. Returns None when the history is too thin to train on, and the
pipeline then keeps its legacy projection.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd

from .engine import EngineSettings, FittedEngine, fit_engine
from .history import load_seasons, season_start
from .inseason import bayes_update, current_season

# Seasons with both a list and statistics needed before the engine is trusted:
# the calibration stage learns from a season and the one before it.
MINIMUM_TRAINING_SEASONS = 3


def project_season(raw: Path, listone: pd.DataFrame, season: str, scoring: dict[str, float], season_days: int,
                   calendar: pd.DataFrame | None = None, settings: EngineSettings | None = None,
                   in_season: bool = True) -> tuple[pd.DataFrame, FittedEngine] | None:
    settings = replace(settings or EngineSettings(), season_days=season_days)
    start = season_start(season.replace("/", "-"))
    past = [frames for label, frames in load_seasons(raw, season_days).items() if season_start(label) < start]
    trainable = [frames for frames in past if frames.stats is not None and frames.listone is not None]
    if len(trainable) < MINIMUM_TRAINING_SEASONS:
        return None
    players = listone.rename(columns={"Qt.I": "QtI"})[["Id", "R", "Nome", "Squadra", "QtI"]].copy()
    players["Id"] = players.Id.astype(int)
    engine = fit_engine(past, scoring, settings)
    prediction = engine.predict(players, [frames for frames in past if frames.stats is not None])
    prediction["giornate_osservate"] = 0.0
    prediction["presenze_osservate"] = 0.0
    played = current_season(raw, season, calendar) if in_season else None
    if played is not None and played.matchdays:
        prediction = bayes_update(prediction, played, engine)
    return prediction, engine
