"""Projection engine and season-by-season backtest, on synthetic seasons."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from advisor.backtest import (FM_SCORING, BaselineCalibration, Context, baseline_model, contexts, evaluate,
                              engine_model, run)
from advisor.engine import EngineSettings, fit_engine, history_summary
from advisor.history import SeasonFrames, team_strength
from advisor.pipeline import weighted_history, weighted_rate_per_appearance

ROLES = ["P"] * 30 + ["D"] * 60 + ["C"] * 60 + ["A"] * 50
TEAMS = [f"Club{i}" for i in range(10)]


def synthetic_seasons(count: int = 6, seed: int = 7) -> list[SeasonFrames]:
    """Players whose presence and vote depend on a hidden quality the opening price reflects."""
    rng = np.random.default_rng(seed)
    quality = rng.normal(0, 1, 400)
    seasons = []
    for index in range(count):
        # Each season a third of the list is new, so cold starts exist.
        ids = np.arange(index * 70, index * 70 + len(ROLES))
        q = quality[ids % 400]
        price = np.clip(np.round(np.exp(1.6 + 0.6 * q + rng.normal(0, .2, len(ids)))), 1, 40)
        p = 1 / (1 + np.exp(-(q - 0.2)))
        pv = rng.binomial(38, p)
        mv = np.where(pv > 0, 6 + 0.15 * q + rng.normal(0, 0.6, len(ids)) / np.sqrt(np.maximum(pv, 1)), 0)
        roles = np.array(ROLES)
        goal_rate = np.where(roles == "A", .35, np.where(roles == "C", .1, .04)) * np.exp(0.3 * q)
        stats = pd.DataFrame({
            "Id": ids, "R": roles, "Nome": [f"G{i}" for i in ids], "Squadra": [TEAMS[i % 10] for i in ids],
            "Pv": pv.astype(float), "Mv": mv, "Gf": rng.poisson(goal_rate * pv).astype(float),
            "Gs": np.where(roles == "P", rng.poisson(1.3 * pv), 0).astype(float),
            "Rp": 0.0, "Rc": 0.0, "R+": 0.0, "R-": 0.0, "Ass": rng.poisson(0.08 * pv).astype(float),
            "Amm": rng.poisson(0.15 * pv).astype(float), "Esp": 0.0, "Au": 0.0,
        })
        stats["Fm"] = np.where(pv > 0, stats.Mv + (3 * stats.Gf + stats.Ass - .5 * stats.Amm - stats.Gs) / np.maximum(pv, 1), 0)
        listone = pd.DataFrame({"Id": ids, "R": roles, "Nome": stats.Nome, "Squadra": stats.Squadra, "QtI": price, "QtA": price, "ceduto": False})
        frames = SeasonFrames(f"{2015 + index}-{(16 + index) % 100:02d}", stats, listone, {team: 38 for team in TEAMS})
        seasons.append(frames)
    return seasons


def test_history_summary_discounts_older_seasons_and_marks_newcomers():
    seasons = synthetic_seasons(3)
    settings = EngineSettings(decay=0.5)
    player = int(seasons[1].stats.Id.iloc[100])  # present in seasons 0 and 1
    summary = history_summary([player, 10_000], seasons[:2], settings)
    s0 = seasons[0].stats.set_index("Id").loc[player]
    s1 = seasons[1].stats.set_index("Id").loc[player]
    assert summary.loc[player, "n"] == pytest.approx(s1.Pv + 0.5 * s0.Pv)
    assert summary.loc[player, "last_votes"] == s1.Pv
    assert summary.loc[10_000, "n"] == 0 and summary.loc[10_000, "seasons"] == 0


def test_engine_learns_cold_start_from_price():
    seasons = synthetic_seasons()
    engine = fit_engine(seasons[:-1], FM_SCORING)
    target = seasons[-1]
    prediction = engine.predict(target.listone, seasons[:-1])
    new = prediction[prediction.new > 0].join(target.listone.set_index("Id").QtI)
    assert len(new) > 20
    # A newcomer's presence comes from what past newcomers at that price did.
    assert new.p_play.corr(new.QtI) > 0.5
    assert new.p_play.nunique() > 5


def test_engine_uses_only_the_seasons_it_is_given():
    seasons = synthetic_seasons()
    first = fit_engine(seasons[:-1], FM_SCORING).predict(seasons[-1].listone, seasons[:-1])
    altered = [*seasons[:-1], SeasonFrames(seasons[-1].label, seasons[-1].stats.assign(Pv=0.0), seasons[-1].listone, seasons[-1].matchdays)]
    second = fit_engine(altered[:-1], FM_SCORING).predict(altered[-1].listone, altered[:-1])
    pd.testing.assert_frame_equal(first, second)


def test_engine_uncertainty_is_positive_and_per_role():
    seasons = synthetic_seasons()
    engine = fit_engine(seasons[:-1], FM_SCORING)
    for role in "PDCA":
        assert engine.uncertainty.fv_tau2[role] > 0
        assert engine.uncertainty.fv_sigma2[role] > 0
        assert engine.uncertainty.presence_concentration[role] > 0


def test_evaluate_rewards_the_true_outcome():
    seasons = synthetic_seasons()
    by_label = {s.label: s for s in seasons}
    (ctx, actual), *_ = contexts(by_label, FM_SCORING)
    perfect = pd.DataFrame({"R": actual.R, "p_play": (actual.Pv / 38).clip(0.001, 0.999), "mv": actual.Mv.fillna(6),
                            "bonus": (actual.Fm - actual.Mv).fillna(0), "fv": actual.Fm.fillna(6)})
    flat = perfect.assign(p_play=0.5, fv=6.0, mv=6.0, bonus=0.0)
    slots = {"P": 3, "D": 8, "C": 8, "A": 6}
    good, bad = evaluate(perfect, actual, slots, 4), evaluate(flat, actual, slots, 4)
    assert good["fv_mae"] == pytest.approx(0.0)
    assert good["presenza_logloss"] < bad["presenza_logloss"]
    assert good["decisione_spearman"] == pytest.approx(1.0)
    assert good["decisione_topk"] == pytest.approx(1.0)


def test_baseline_matches_the_pipeline_history_functions():
    seasons = synthetic_seasons(5)
    calibration = BaselineCalibration(0.0, 1.0, 1.0, 5.0, 1.0, 5.0, 5.0, 5.0)
    ctx = Context(seasons[-1].label, seasons[:-1], seasons[-1].listone, FM_SCORING)
    prediction = baseline_model(calibration)(ctx)
    histories = [s.stats for s in seasons[-4:-1]]
    for player in [int(i) for i in seasons[-1].listone.Id[:40]]:
        history_p = weighted_history(player, histories, "Pv", np.nan)
        if not np.isnan(history_p):
            assert prediction.loc[player, "p_play"] == pytest.approx(np.clip(history_p / 38, .05, .95))
        if prediction.loc[player, "new"] == 0:
            assert prediction.loc[player, "mv"] == pytest.approx(weighted_history(player, histories, "Mv", 6.0))
            assert prediction.loc[player, "rate_Gf"] == pytest.approx(weighted_rate_per_appearance(player, histories, "Gf"))


def test_run_reports_every_group_for_every_model():
    seasons = {s.label: s for s in synthetic_seasons()}
    calibration = BaselineCalibration(0.0, 1.0, 1.0, 5.0, 1.0, 5.0, 5.0, 5.0)
    results = run(Path("."), {"base": baseline_model(calibration), "motore": engine_model()}, FM_SCORING,
                  {"P": 3, "D": 8, "C": 8, "A": 6}, 4, seasons)
    assert set(results.gruppo) == {"tutti", "nuovi", "con_storico"}
    assert set(results.modello) == {"base", "motore"}
    assert results[results.gruppo == "tutti"].presenza_logloss.notna().all()


def test_team_strength_is_goals_per_matchday():
    stats = pd.DataFrame({"Squadra": ["A", "A", "B"], "R": ["P", "A", "P"], "Gf": [0, 38, 19], "Gs": [19, 0, 38]})
    table = team_strength(stats, {"A": 38, "B": 38})
    assert table.loc["A", "gf"] == 1.0 and table.loc["A", "gs"] == 0.5
