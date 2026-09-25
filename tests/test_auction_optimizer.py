"""Invariants of the auction optimiser.

These guard the two mistakes that were made once and would come back silently:
pricing the remaining market as if the role mix never changed, and valuing a
roster as though a missed vote were never covered by the bench.
"""

from pathlib import Path

import pytest

from advisor.config import LeagueConfig
from advisor.league_profile import LeagueProfile
from advisor.optimize import (
    filled_counts,
    lineup_value,
    maximum_bid_for,
    open_slots_by_role,
    optimise,
    reprice_remaining_market,
    reset_lineup_cache,
    resolve_players,
    target_for,
)

PROFILE_PATH = Path(__file__).parents[1] / "config" / "default_profile.json"


@pytest.fixture()
def league():
    return LeagueConfig.from_profile(LeagueProfile.load_json(PROFILE_PATH))


def _player(player_id: int, role: str, fvm: float, fantavoto: float, probability: float = 0.9) -> dict:
    return {
        "id": player_id, "nome": f"P{player_id}", "ruolo": role, "squadra": "Test",
        "fvm_original": fvm,
        "mercato": {"prezzo_atteso": 1.0, "fantavoto_medio": fantavoto,
                    "p_gioca_medio": probability, "fp_per_giornata": fantavoto * probability},
    }


def _listing(league: LeagueConfig) -> list[dict]:
    players, next_id = [], 1
    for role, slots in league.slots:
        for index in range(league.participants * slots + 6):
            players.append(_player(next_id, role, fvm=float(200 - index * 3), fantavoto=7.5 - index * .05))
            next_id += 1
    return players


def test_open_slots_count_every_manager_not_only_mine(league):
    filled = filled_counts([_player(1, "P", 10, 6)], [_player(2, "P", 10, 6), _player(3, "D", 10, 6)])
    open_slots = open_slots_by_role(league, filled)
    assert open_slots["P"] == league.participants * 3 - 2
    assert open_slots["D"] == league.participants * 8 - 1


def test_expected_prices_sum_to_the_credits_still_to_be_spent(league):
    """The identity that pins the price scale, with nothing sold yet."""
    players = _listing(league)
    reprice_remaining_market(players, league, 0.0, {})
    buyers = []
    for role, slots in league.slots:
        in_role = sorted((p for p in players if p["ruolo"] == role), key=lambda p: -p["fvm_original"])
        buyers.extend(in_role[:league.participants * slots])
    total = sum(p["mercato"]["prezzo_atteso"] for p in buyers)
    assert total == pytest.approx(league.credit_pool, rel=1e-3)


def test_repricing_follows_the_role_phases(league):
    """Only forwards left: the remaining credits must price forwards alone.

    Splitting the remaining slots on the original role proportions priced the
    last forty-eight forwards at 1100 credits when 800 were left, a 37% error
    landing in the phase with no room to recover.
    """
    players = _listing(league)
    filled = {"P": league.participants * 3, "D": league.participants * 8, "C": league.participants * 8, "A": 0}
    spent = league.credit_pool - 800
    available = [p for p in players if p["ruolo"] == "A"]
    reprice_remaining_market(available, league, spent, filled)
    forwards = sorted(available, key=lambda p: -p["fvm_original"])[:league.participants * 6]
    assert sum(p["mercato"]["prezzo_atteso"] for p in forwards) == pytest.approx(800, rel=1e-3)


def test_bench_coverage_lifts_a_roster_above_the_sum_of_its_starters(league):
    """A missed vote is covered, so charging the starter for it understates the roster."""
    reset_lineup_cache("test-coverage")
    roster = []
    next_id = 1
    for role, slots in league.slots:
        for _ in range(slots):
            roster.append(_player(next_id, role, 10, 6.5, probability=0.8))
            next_id += 1
    covered = lineup_value(roster, league)
    naive = sum(sorted((p["mercato"]["fp_per_giornata"] for p in roster), reverse=True)[:11])
    assert covered > naive


def test_a_fixed_size_bench_makes_the_roles_compete_for_it(league):
    """With a bench of N players of any role, cover is scarce and shared.

    A roster of twenty-five fields eleven, so fourteen players are spare. A
    bench of eight seats only eight of them: a fourth spare defender travels
    only if he is worth more as cover than a second spare forward. Treating
    every spare player as available overstates the roster.
    """
    reset_lineup_cache("test-bench-any")
    roster, next_id = [], 1
    for role, slots in league.slots:
        for index in range(slots):
            roster.append(_player(next_id, role, 10, 7.0 - index * .2, probability=0.75))
            next_id += 1

    unlimited = lineup_value(roster, league)

    import dataclasses

    seated = dataclasses.replace(league, bench_composition="any_role", bench_size=8)
    reset_lineup_cache("test-bench-8")
    limited = lineup_value(roster, seated)
    assert limited < unlimited

    reset_lineup_cache("test-bench-1")
    barely = dataclasses.replace(league, bench_composition="any_role", bench_size=1)
    assert lineup_value(roster, barely) < limited


def test_the_bench_seats_the_most_useful_spares_first(league):
    """The eight who travel are the spares likeliest to be worth a substitution."""
    reset_lineup_cache("test-bench-order")
    import dataclasses

    seated = dataclasses.replace(league, bench_composition="any_role", bench_size=2)
    roster, next_id = [], 1
    for role, slots in league.slots:
        for _ in range(slots):
            roster.append(_player(next_id, role, 10, 6.0, probability=0.7))
            next_id += 1
    baseline = lineup_value(roster, seated)

    # Upgrading a spare who cannot get a seat changes nothing; the same upgrade
    # on one who can must show up in the value.
    roster[-1]["mercato"]["fantavoto_medio"] = 9.5
    reset_lineup_cache("test-bench-order-2")
    assert lineup_value(roster, seated) > baseline


def test_by_role_bench_is_untouched_by_the_new_field(league):
    """Upstream profiles keep the bench they always had."""
    assert league.bench_composition == "by_role"
    assert league.bench_size == len(league.bench_roles)


def test_unspent_credits_are_not_a_result(league):
    """The objective maximises points inside the budget; the target is a floor."""
    reset_lineup_cache("test-objective")
    players = _listing(league)
    reprice_remaining_market(players, league, 0.0, {})
    solution = optimise(players, league, budget=league.starting_credits)
    assert solution.spent > league.starting_credits * 0.9
    assert solution.trace and solution.trace[-1][1] == pytest.approx(solution.points)


def test_target_is_derived_from_the_profile_never_written_down():
    profile = LeagueProfile.load_json(PROFILE_PATH)
    step = profile.virtual_goals.step
    threshold = profile.virtual_goals.threshold
    assert target_for(profile, 0) == threshold
    assert target_for(profile, 2) == threshold + 2 * step


def test_maximum_bid_never_exceeds_the_budget(league):
    reset_lineup_cache("test-ceiling")
    players = _listing(league)
    reprice_remaining_market(players, league, 0.0, {})
    solution = optimise(players, league, budget=league.starting_credits)
    candidate = max((p for p in players if p["id"] not in {r["id"] for r in solution.roster}),
                    key=lambda p: p["mercato"]["fantavoto_medio"])
    row = maximum_bid_for(players, league, solution, candidate, budget=league.starting_credits, probes=4)
    assert 0 <= row["prezzo_massimo"] <= league.starting_credits


def test_a_blank_goalkeeper_hierarchy_is_absent_not_a_number():
    """A goalkeeper in an open battle has no stated hierarchy, and NaN is not JSON.

    pandas hands back NaN for a blank cell even where the column stores None, so
    the value is normalised again on its way into the dataset. Which pandas
    version turns None into NaN varies, which is why this is pinned.
    """
    from advisor.pipeline import _or_none, normalize_goalkeeper_hierarchy

    assert normalize_goalkeeper_hierarchy(float("nan")) is None
    assert normalize_goalkeeper_hierarchy("") is None
    # Idempotent, so applying it on the way out cannot corrupt a real value.
    for stated in ("PRIMO", "SECONDO", "PRIMO/SECONDO"):
        assert normalize_goalkeeper_hierarchy(normalize_goalkeeper_hierarchy(stated)) == stated
    assert _or_none(float("nan")) is None
    assert _or_none("SUPER_TOP") == "SUPER_TOP"


def test_the_payload_guard_names_the_player_and_the_field():
    from advisor.pipeline import _reject_non_finite

    players = [{"id": 7, "nome": "Tal", "ruolo": "C", "squadra": "Roma",
                "proiezione": {"bonus": float("nan")}}]
    with pytest.raises(ValueError, match=r"proiezione\.bonus.*Tal"):
        _reject_non_finite(players, "players")


def test_an_ambiguous_name_is_refused_rather_than_guessed():
    """Eighteen surnames are shared in the real listing; a wrong pick is final."""
    players = [_player(1, "A", 10, 6), _player(2, "A", 10, 6)]
    players[0]["nome"] = players[1]["nome"] = "Martinez"
    with pytest.raises(SystemExit, match="ambiguo"):
        resolve_players(players, "Martinez:10")
    picked, paid = resolve_players(players, "#2:10")
    assert picked[0]["id"] == 2 and paid == 10
