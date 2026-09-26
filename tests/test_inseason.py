"""Matchday votes during the season: files, import, conjugate update, pipeline."""
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from advisor.backtest import FM_SCORING
from advisor.engine import fit_engine
from advisor.history import SeasonFrames
from advisor.inseason import (aggregate_matchdays, bayes_update, current_season, import_votes_workbook,
                              matchday_backtest, read_matchdays, validate_matchday, votes_dir, write_matchday)
from advisor.league_profile import LeagueProfile
from advisor.pipeline import build_projections
from advisor.simulation import _season_levels
from tests.test_engine_backtest import synthetic_seasons

ROOT = Path(__file__).parents[1]


def matchday(day: int, rows: list[tuple]) -> pd.DataFrame:
    """rows: (id, nome, squadra, ruolo, voto, gol)."""
    return pd.DataFrame([
        {"giornata": day, "id_fantacalcio": i, "nome": n, "squadra": s, "ruolo": r, "voto": v, "gol": g, "assist": 0,
         "ammonizioni": 0, "espulsioni": 0, "autogol": 0, "gol_subiti": 0, "rigori_parati": 0,
         "rigori_sbagliati": 0, "rigori_segnati": 0}
        for i, n, s, r, v, g in rows
    ])


def test_matchday_files_round_trip_and_reject_bad_rows(tmp_path):
    frame = matchday(3, [(1, "Uno", "Club0", "A", 6.5, 1), (2, "Due", "Club0", "D", None, 0)])
    path = write_matchday(frame, tmp_path)
    assert path.name == "giornata_03.csv"
    [read] = read_matchdays(tmp_path)
    assert read.voto.isna().sum() == 1 and read.gol.sum() == 1
    with pytest.raises(ValueError, match="due volte"):
        validate_matchday(pd.concat([frame, frame]))
    with pytest.raises(ValueError, match="ruolo"):
        validate_matchday(frame.assign(ruolo="X"))
    with pytest.raises(ValueError, match="colonne"):
        validate_matchday(frame.drop(columns=["gol"]))


def test_aggregate_counts_votes_and_matchdays_per_club():
    frames = [matchday(1, [(1, "Uno", "Club0", "A", 6.0, 1), (2, "Due", "Club1", "D", 6.0, 0)]),
              matchday(2, [(1, "Uno", "Club0", "A", 7.0, 0), (3, "Tre", "Club0", "C", None, 0)])]
    stats, played = aggregate_matchdays(frames)
    row = stats.set_index("Id").loc[1]
    assert row.Pv == 2 and row.Mv == pytest.approx(6.5) and row.Gf == 1
    assert stats.set_index("Id").loc[3].Pv == 0
    assert played == {"Club0": 2, "Club1": 1}
    weighted, played = aggregate_matchdays(frames, half_life=1.0)
    assert played["Club0"] == pytest.approx(1.5)
    assert weighted.set_index("Id").loc[1].Mv == pytest.approx((0.5 * 6 + 7) / 1.5)


def test_bayes_update_moves_towards_the_matchdays_played():
    seasons = synthetic_seasons()
    engine = fit_engine(seasons[:-1], FM_SCORING)
    target = seasons[-1]
    prior = engine.predict(target.listone, seasons[:-1])
    ids = list(prior.index[:3])
    rows = []
    for day in range(1, 11):
        rows.append(matchday(day, [(ids[0], "Sempre", prior.loc[ids[0], "Squadra"], prior.loc[ids[0], "R"], 7.5, 0)]))
    stats, played = aggregate_matchdays(rows)
    posterior = bayes_update(prior, SeasonFrames("corrente", stats, None, played), engine)
    always = ids[0]
    assert posterior.loc[always, "p_play"] > prior.loc[always, "p_play"]
    assert prior.loc[always, "mv"] < posterior.loc[always, "mv"] < 7.5
    assert posterior.loc[always, "mv_tau2"] < prior.loc[always, "mv_tau2"]
    teammates = posterior[(posterior.Squadra == prior.loc[always, "Squadra"]) & (posterior.index != always)]
    # Ten matchdays without a vote pull a teammate's presence down.
    assert (teammates.p_play < prior.loc[teammates.index, "p_play"]).all()
    elsewhere = posterior[posterior.Squadra != prior.loc[always, "Squadra"]]
    pd.testing.assert_series_equal(elsewhere.p_play, prior.loc[elsewhere.index, "p_play"])


def test_matchday_backtest_scores_every_matchday():
    seasons = synthetic_seasons()
    engine = fit_engine(seasons[:-1], FM_SCORING)
    prior = engine.predict(seasons[-1].listone, seasons[:-1])
    rng = np.random.default_rng(1)
    frames = []
    for day in range(1, 5):
        chosen = prior.sample(80, random_state=day)
        frames.append(matchday(day, [(i, "x", row.Squadra, row.R, float(np.round(rng.normal(6, .5), 1)), 0) for i, row in chosen.iterrows()]))
    check = matchday_backtest(prior, frames, engine, FM_SCORING)
    assert check.matchdays == 4
    assert 0 < check.presence_logloss < 2 and 0 < check.fantavoto_mae < 3


def test_import_votes_workbook_reads_club_blocks(tmp_path):
    rows = [["Voti Fantacalcio - Giornata 5"], ["ATALANTA"],
            ["Cod.", "Ruolo", "Nome", "Voto", "Gf", "Gs", "Rp", "Rs", "Rf", "Au", "Amm", "Esp", "Ass"],
            [4431, "P", "Carnesecchi", 6.5, 0, 1, 0, 0, 0, 0, 0, 0, 0],
            [6485, "D", "Kristensen", "6*", 0, 0, 0, 0, 0, 0, 1, 0, 0],
            [9999, "ALL", "Allenatore", 6, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            [], ["INTER"],
            ["Cod.", "Ruolo", "Nome", "Voto", "Gf", "Gs", "Rp", "Rs", "Rf", "Au", "Amm", "Esp", "Ass"],
            [2764, "A", "Martinez L.", 8, 2, 0, 0, 0, 1, 0, 0, 0, 1]]
    path = tmp_path / "voti.xlsx"
    pd.DataFrame(rows).to_excel(path, header=False, index=False)
    frame = import_votes_workbook(path, 5).set_index("id_fantacalcio")
    assert list(frame.index) == [4431, 6485, 2764]
    assert frame.loc[4431, "squadra"] == "Atalanta" and frame.loc[4431, "gol_subiti"] == 1
    assert pd.isna(frame.loc[6485, "voto"]) and frame.loc[6485, "ammonizioni"] == 1
    assert frame.loc[2764, "gol"] == 2 and frame.loc[2764, "rigori_segnati"] == 1 and frame.loc[2764, "assist"] == 1


def test_current_season_prefers_matchday_files_and_falls_back_to_stats(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    assert current_season(raw, "2026/27") is None
    write_matchday(matchday(1, [(1, "Uno", "Club0", "A", 6.0, 0)]), votes_dir(raw, "2026/27"))
    season = current_season(raw, "2026/27")
    assert season.matchdays == {"Club0": 1}


def test_pipeline_folds_in_the_matchdays_played(tmp_path):
    raw = tmp_path / "data" / "raw"
    shutil.copytree(ROOT / "data/raw", raw)
    source = json.loads((ROOT / "config/default_profile.json").read_text(encoding="utf-8"))
    for item in source["current_sources"] + source["history_sources"]:
        item["path"] = str(raw / Path(item["path"]).name)
    profile = LeagueProfile.from_dict(source)
    listone = pd.read_excel(raw / "listone_2026_27.xlsx", sheet_name="Tutti", header=1)
    club = listone[listone.Squadra == "Inter"]
    striker = club[club.R == "A"].iloc[0]
    before = build_projections(raw=raw, output=tmp_path / "prima", profile=profile)
    rows = [(int(striker.Id), striker.Nome, "Inter", "A", 8.0, 2)]
    for day in (1, 2, 3):
        write_matchday(matchday(day, rows), votes_dir(raw, profile.season.season))
    after = build_projections(raw=raw, output=tmp_path / "dopo", profile=profile)
    pick = lambda payload, pid: next(p for p in payload["players"] if p["id"] == pid)
    assert after["model_version"] == "2.0"
    moved, still = pick(after, int(striker.Id)), pick(before, int(striker.Id))
    assert moved["proiezione"]["giornate_osservate"] == 3 and still["proiezione"]["giornate_osservate"] == 0
    assert moved["proiezione"]["fantavoto"] > still["proiezione"]["fantavoto"]
    assert after["meta"]["profile"]["dataset_input_hash"] != before["meta"]["profile"]["dataset_input_hash"]


def test_season_levels_draw_only_for_measured_players():
    rng = np.random.default_rng(3)
    plain = {"id": 1, "p_gioca_per_giornata": [0.7] * 5, "proiezione": {"p_gioca": 0.7}}
    measured = {"id": 2, "p_gioca_per_giornata": [0.7] * 5, "proiezione": {"p_gioca": 0.7, "incertezza": {
        "sd_livello_voto": 0.2, "sd_livello_fantavoto": 0.5, "concentrazione_presenza": 2.0}}}
    levels = [_season_levels([plain, measured], rng) for _ in range(400)]
    assert all(1 not in level for level in levels)
    votes = np.array([level[2][0] for level in levels])
    shares = np.array([level[2][2] for level in levels])
    assert 0.15 < votes.std() < 0.25
    assert shares.mean() == pytest.approx(1.0, abs=0.08) and shares.std() > 0.3
