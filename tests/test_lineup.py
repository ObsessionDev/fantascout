"""The matchday lineup: a legal XI, an ordered bench, availability honoured.

Uses `config/default_profile.json` — the one public profile — with a made-up
roster, never real league data.
"""

from pathlib import Path

import pytest

from advisor.config import LeagueConfig
from advisor.league_profile import LeagueProfile
from advisor.lineup import (
    LineupError,
    build_lineup,
    load_roster,
    matchday_snapshot,
    resolve_doubtful,
    resolve_unavailable,
)

PROFILE_PATH = Path(__file__).parents[1] / "config" / "default_profile.json"
DAY = 0  # giornata 1


@pytest.fixture()
def league():
    return LeagueConfig.from_profile(LeagueProfile.load_json(PROFILE_PATH))


def _player(player_id: int, role: str, fantavoto: float, probability: float = 0.9, days: int = 3) -> dict:
    return {
        "id": player_id, "nome": f"P{player_id}", "ruolo": role, "squadra": "Test",
        "p_gioca_per_giornata": [probability] * days,
        "voto_puro_mean_per_giornata": [fantavoto] * days,
        "bonus_atteso_per_giornata": [0.0] * days,
        "voto_puro_std_per_giornata": [0.7] * days,
        "proiezione": {"p_gioca": probability, "fantavoto": fantavoto},
    }


def _roster(league: LeagueConfig, probability: float = 0.9) -> list[dict]:
    roster, next_id = [], 1
    for role, slots in league.slots:
        for index in range(slots):
            roster.append(_player(next_id, role, fantavoto=7.5 - index * 0.1, probability=probability))
            next_id += 1
    return roster


def test_the_lineup_is_legal_and_uses_the_whole_roster(league):
    roster = _roster(league)
    report = build_lineup(roster, league, DAY)
    assert len(report.titolari) == 11
    assert report.formazione in league.allowed_formations
    starter_ids = {row["id"] for row in report.titolari}
    bench_ids = {row["id"] for row in report.panchina}
    assert not starter_ids & bench_ids
    # The default profile's bench is per-role with no fixed size: everyone not
    # starting has somewhere to be, so nobody sits out of the giornata entirely.
    assert len(starter_ids) + len(bench_ids) == 25
    assert report.fuori_lista == []
    assert report.indisponibili == []


def test_the_bench_is_ordered_by_expected_value_within_role(league):
    roster = _roster(league)
    report = build_lineup(roster, league, DAY)
    defenders_bench = [row for row in report.panchina if row["ruolo"] == "D"]
    assert defenders_bench == sorted(defenders_bench, key=lambda row: row["fantavoto_atteso"], reverse=True)


def test_an_unavailable_starter_is_excluded_and_replaced(league):
    roster = _roster(league)
    baseline = build_lineup(roster, league, DAY)
    best_goalkeeper = next(row for row in baseline.titolari if row["ruolo"] == "P")

    report = build_lineup(roster, league, DAY, unavailable={best_goalkeeper["id"]})
    assert report.indisponibili == [
        {"id": best_goalkeeper["id"], "nome": best_goalkeeper["nome"], "ruolo": "P", "squadra": "Test"}
    ]
    starter_ids = {row["id"] for row in report.titolari}
    bench_ids = {row["id"] for row in report.panchina}
    assert best_goalkeeper["id"] not in starter_ids
    assert best_goalkeeper["id"] not in bench_ids
    new_goalkeeper = next(row for row in report.titolari if row["ruolo"] == "P")
    assert new_goalkeeper["id"] != best_goalkeeper["id"]


def test_a_doubtful_player_keeps_his_role_but_reports_the_given_probability(league):
    roster = _roster(league)
    baseline = build_lineup(roster, league, DAY)
    starter = baseline.titolari[0]

    report = build_lineup(roster, league, DAY, doubtful={starter["id"]: 0.35})
    updated = next(row for row in report.titolari if row["id"] == starter["id"])
    assert updated["dubbio"] is True
    assert updated["p_gioca"] == pytest.approx(0.35)
    # A lower chance of playing is worth less to the plan, all else equal.
    assert report.valore_atteso < baseline.valore_atteso


def test_no_available_goalkeeper_is_reported_clearly(league):
    roster = _roster(league)
    goalkeeper_ids = {p["id"] for p in roster if p["ruolo"] == "P"}
    with pytest.raises(LineupError, match="portiere"):
        build_lineup(roster, league, DAY, unavailable=goalkeeper_ids)


def test_too_many_unavailable_defenders_is_reported_clearly(league):
    roster = _roster(league)
    defenders = [p["id"] for p in roster if p["ruolo"] == "D"]
    # Every allowed formation in the default profile needs at least 3 defenders.
    unavailable = set(defenders[:-2])
    with pytest.raises(LineupError, match="D"):
        build_lineup(roster, league, DAY, unavailable=unavailable)


def test_matchday_snapshot_falls_back_to_the_season_projection_past_the_array(league):
    player = _player(1, "A", fantavoto=6.0, probability=0.7, days=2)
    snapshot = matchday_snapshot(player, day_index=5)
    assert snapshot["mercato"]["p_gioca_medio"] == pytest.approx(0.7)
    assert snapshot["mercato"]["fantavoto_medio"] == pytest.approx(6.0)


def test_load_roster_accepts_the_dashboard_export_format(tmp_path, league):
    players = _roster(league)
    players_by_id = {p["id"]: p for p in players}
    export = {"version": 1, "squadra": "La mia squadra",
              "giocatori": [{"id": p["id"], "nome": p["nome"]} for p in players[:5]]}
    path = tmp_path / "rosa.json"
    path.write_text(__import__("json").dumps(export), encoding="utf-8")
    roster = load_roster(path, players_by_id)
    assert [p["id"] for p in roster] == [entry["id"] for entry in export["giocatori"]]


def test_load_roster_rejects_a_player_missing_from_the_dataset(tmp_path):
    path = tmp_path / "rosa.json"
    path.write_text('{"giocatori": [{"id": 999}]}', encoding="utf-8")
    with pytest.raises(SystemExit, match="999"):
        load_roster(path, {})


def test_resolve_unavailable_and_doubtful_match_by_name_or_id(league):
    roster = _roster(league)[:3]
    ids = resolve_unavailable(roster, f"{roster[0]['nome']}, #{roster[1]['id']}")
    assert ids == {roster[0]["id"], roster[1]["id"]}

    doubtful = resolve_doubtful(roster, f"{roster[2]['nome']}:0.4")
    assert doubtful == {roster[2]["id"]: 0.4}

    with pytest.raises(SystemExit, match="range"):
        resolve_doubtful(roster, f"{roster[2]['nome']}:1.4")
