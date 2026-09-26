"""Reproducible season-level Monte Carlo for the configured Classic league."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import LeagueConfig
from .defense import defense_modifier, expected_defense_modifier

@dataclass(frozen=True)
class SimulationResult:
    iterations: int
    teams: dict[str, dict[str, float | list[float]]]
    scenarios: dict[str, dict[str, float | int]]
    diagnostics: dict[str, float]


def _confirmed_inactive(player: dict[str, Any]) -> bool:
    return player.get("confirmed_inactive") is True or player.get("disponibilita", {}).get("confirmed_inactive") is True


def _projection_value(player: dict[str, Any], matchday_indices: list[int]) -> float:
    chances = player.get("p_gioca_per_giornata") or []
    votes = player.get("voto_puro_mean_per_giornata") or []
    bonuses = player.get("bonus_atteso_per_giornata") or []
    if chances:
        days = matchday_indices or list(range(len(chances)))
        return sum(
            float(chances[day]) * (float(votes[day]) + float(bonuses[day]))
            for day in days
            if day < len(chances) and day < len(votes) and day < len(bonuses)
        )
    projection = player.get("proiezione", {})
    days = len(matchday_indices) or 38
    return days * float(projection.get("p_gioca", 0)) * (
        float(projection.get("voto_puro", 0)) + float(projection.get("bonus", 0))
    )


def _sample_day_projection(player: dict[str, Any], day: int) -> tuple[float, float, float]:
    projection = player.get("proiezione", {})
    chances = player.get("p_gioca_per_giornata") or []
    votes = player.get("voto_puro_mean_per_giornata") or []
    bonuses = player.get("bonus_atteso_per_giornata") or []
    probability = float(chances[day]) if day < len(chances) else float(projection.get("p_gioca", 0))
    vote = float(votes[day]) if day < len(votes) else float(projection.get("voto_puro", 0))
    bonus = float(bonuses[day]) if day < len(bonuses) else float(projection.get("bonus", 0))
    return min(1.0, max(0.0, probability)), vote, vote + bonus


def _sample_role_utility(players: list[dict[str, Any]], starters_count: int, bench_count: int, day: int) -> tuple[float, list[tuple[float, float]]]:
    options = sorted(
        ((*_sample_day_projection(player, day), int(player["id"])) for player in players),
        key=lambda item: (item[0] * item[2], -item[3]),
        reverse=True,
    )
    starters = options[:starters_count]
    bench = options[starters_count:starters_count + bench_count]
    utility = sum(probability * score for probability, _, score, _ in starters)
    missing = [1.0]
    for probability, _, _, _ in starters:
        next_missing = [0.0] * (len(missing) + 1)
        for absent, state_probability in enumerate(missing):
            next_missing[absent] += state_probability * probability
            next_missing[absent + 1] += state_probability * (1 - probability)
        missing = next_missing
    for probability, _, score, _ in bench:
        needed = sum(missing[1:])
        utility += needed * probability * score
        next_missing = [0.0] * len(missing)
        for absent, state_probability in enumerate(missing):
            next_missing[absent] += state_probability * (1 - probability)
            next_missing[max(0, absent - 1)] += state_probability * probability
        missing = next_missing
    return utility, [(probability, vote) for probability, vote, _, _ in starters]


def _goalkeeper_rank(player: dict[str, Any]) -> int:
    values = {"PRIMO": 0, "SECONDO": 1, "TERZO": 2}
    hierarchy = player.get("gerarchia_portiere") or player.get("disponibilita", {}).get("gerarchia_portiere") or ""
    ranks = [values[value] for value in str(hierarchy).split("/") if value in values]
    return min(ranks) if ranks else 3


def _sample_goalkeeper_utility(players: list[dict[str, Any]], day: int) -> tuple[float, tuple[float, float] | None]:
    clubs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in players:
        clubs[str(player.get("team_id") or player.get("squadra") or "unknown")].append(player)
    options = []
    for club, club_players in clubs.items():
        remaining = 1.0
        probability = weighted_score = weighted_vote = 0.0
        groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for player in club_players:
            groups[_goalkeeper_rank(player)].append(player)
        for rank in sorted(groups):
            projections = [_sample_day_projection(player, day) for player in groups[rank]]
            raw_probability = sum(item[0] for item in projections)
            used = min(remaining, raw_probability)
            if used > 0 and raw_probability > 0:
                weighted_vote += used * sum(item[0] * item[1] for item in projections) / raw_probability
                weighted_score += used * sum(item[0] * item[2] for item in projections) / raw_probability
                probability += used
                remaining -= used
        options.append((weighted_score / probability if probability else 0, probability, weighted_vote / probability if probability else 0, club))
    options.sort(key=lambda item: (item[0], item[1], item[3]), reverse=True)
    unavailable = 1.0
    utility = 0.0
    for score, probability, _, _ in options:
        utility += unavailable * probability * score
        unavailable *= 1 - probability
    primary = (options[0][1], options[0][2]) if options else None
    return utility, primary


def _sample_roster_utility(roster: list[dict[str, Any]], days: list[int], league: LeagueConfig) -> float:
    by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in roster:
        by_role[player["ruolo"]].append(player)
    bench_counts = Counter(league.bench_roles) if league.switch_mode != "None" else Counter()
    total = 0.0
    for day in days:
        goalkeeper_utility, goalkeeper = _sample_goalkeeper_utility(by_role["P"], day)
        best = 0.0
        for formation in league.allowed_formations:
            counts = _formation_counts(formation)
            role_results = {
                role: _sample_role_utility(by_role[role], counts[role], bench_counts[role], day)
                for role in ("D", "C", "A")
            }
            value = sum(result[0] for result in role_results.values())
            if league.defense_modifier_enabled:
                value += expected_defense_modifier(
                    goalkeeper,
                    role_results["D"][1],
                    league.defense_table,
                    league.defense_tiers,
                    league.defense_required_defenders,
                )
            best = max(best, value)
        total += goalkeeper_utility + best
    return total


def _projection_matchday_indices(payload: dict[str, Any]) -> list[int]:
    calendar = _require_league_calendar(payload)
    if isinstance(calendar, dict):
        matchdays = {int(day["serie_a_matchday"]) - 1 for day in calendar["matchdays"]}
    else:
        matchdays = {int(fixture["serie_a_matchday"]) - 1 for fixture in calendar}
    return sorted(day for day in matchdays if day >= 0)


def make_sample_rosters(payload: dict[str, Any], league: LeagueConfig = LeagueConfig()) -> dict[str, list[int]]:
    """Build balanced, non-overlapping rosters using the configured slots."""
    names = _calendar_teams(payload)
    if len(names) != league.participants:
        raise ValueError(f"Expected {league.participants} fantasy teams in the league calendar")
    players = payload["players"]
    player_ids = [int(player["id"]) for player in players]
    if len(set(player_ids)) != len(player_ids):
        raise ValueError("Player IDs must be unique to create sample rosters")
    matchday_indices = _projection_matchday_indices(payload)
    rosters = {name: [] for name in names}
    roster_players: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
    for role_index, (role, slots) in enumerate(league.roster_slots.items()):
        candidates = sorted(
            (p for p in players if p["ruolo"] == role and not _confirmed_inactive(p)),
            key=lambda p: (-_projection_value(p, matchday_indices), -float(p.get("fvm_scaled", 0)), int(p["id"])),
        )
        required = len(names) * slots
        if len(candidates) < required:
            raise ValueError(f"Not enough {role} players to create sample rosters")
        # Each role is distributed in snake rounds so every sample team receives
        # a comparable blend of high and medium projections.
        offset = role_index * len(names) // max(1, len(league.roster_slots))
        base_order = names[offset:] + names[:offset]
        for slot_round in range(slots):
            order = base_order if slot_round % 2 == 0 else list(reversed(base_order))
            for owner in order:
                if role == "P":
                    owned_goalkeepers = roster_players[owner]
                    baseline = sum(
                        _sample_goalkeeper_utility(owned_goalkeepers, day)[0]
                        for day in matchday_indices
                    )
                    player = max(
                        candidates,
                        key=lambda item: (
                            round(sum(
                                _sample_goalkeeper_utility([*owned_goalkeepers, item], day)[0]
                                for day in matchday_indices
                            ) - baseline, 12),
                            _projection_value(item, matchday_indices),
                            float(item.get("fvm_scaled", 0)),
                            -_goalkeeper_rank(item),
                            -int(item["id"]),
                        ),
                    )
                else:
                    player = candidates[0]
                rosters[owner].append(player["id"])
                roster_players[owner].append(player)
                candidates.remove(player)
    return rosters


class RosterValidationError(ValueError):
    """A caller-supplied roster cannot be simulated with the active dataset."""


def normalize_rosters(payload: dict[str, Any], rosters: Any, league: LeagueConfig) -> dict[str, list[int]]:
    """Validate rosters and return stable calendar-ordered player assignments."""
    if not isinstance(rosters, dict):
        raise RosterValidationError("Rosters must be an object keyed by calendar team name")
    names = _calendar_teams(payload)
    if len(names) != league.participants:
        raise RosterValidationError(f"Expected {league.participants} fantasy teams in the league calendar")
    missing, unexpected = sorted(set(names) - set(rosters)), sorted(set(rosters) - set(names))
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing {missing}")
        if unexpected:
            details.append(f"unexpected {unexpected}")
        raise RosterValidationError(f"Rosters must contain exactly the calendar teams: {', '.join(details)}")
    player_rows = payload.get("players")
    if not isinstance(player_rows, list):
        raise RosterValidationError("Dataset players are unavailable")
    players: dict[int, dict[str, Any]] = {}
    for player in player_rows:
        player_id = player.get("id") if isinstance(player, dict) else None
        if isinstance(player_id, bool) or not isinstance(player_id, int) or player_id in players:
            raise RosterValidationError("Dataset player IDs must be unique integers")
        players[player_id] = player

    assigned: set[int] = set()
    normalized = {}
    expected_size = sum(league.roster_slots.values())
    for team in names:
        roster = rosters[team]
        if not isinstance(roster, list):
            raise RosterValidationError(f"{team}: roster must be a list of player IDs")
        if any(isinstance(player_id, bool) or not isinstance(player_id, int) for player_id in roster):
            raise RosterValidationError(f"{team}: player IDs must be integers")
        if len(roster) != expected_size:
            raise RosterValidationError(f"{team}: expected {expected_size} players, received {len(roster)}")
        unknown = next((player_id for player_id in roster if player_id not in players), None)
        if unknown is not None:
            raise RosterValidationError(f"{team}: unknown player ID {unknown}")
        if len(set(roster)) != len(roster) or assigned.intersection(roster):
            raise RosterValidationError(f"{team}: duplicate player within or across rosters")
        assigned.update(roster)
        for role, count in league.roster_slots.items():
            if sum(players[player_id]["ruolo"] == role for player_id in roster) != count:
                received = sum(players[player_id]["ruolo"] == role for player_id in roster)
                raise RosterValidationError(f"{team}: expected {count} {role} players, received {received}")
        normalized[team] = sorted(roster)
    return normalized


def _formation_counts(formation: str) -> dict[str, int]:
    defenders, midfielders, attackers = (int(value) for value in formation.split("-"))
    return {"P": 1, "D": defenders, "C": midfielders, "A": attackers}


def _choose_lineup(available: list[dict[str, Any]], league: LeagueConfig) -> list[dict[str, Any]]:
    """Choose the highest expected legal XI; used both pre-lineup and for Basic replacements."""
    by_role: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in available:
        by_role[player["ruolo"]].append(player)
    for role in by_role:
        by_role[role].sort(key=lambda player: player["selection_value"], reverse=True)
    best: list[dict[str, Any]] = []
    best_value = -float("inf")
    for formation in league.allowed_formations:
        counts = _formation_counts(formation)
        if any(len(by_role[role]) < count for role, count in counts.items()):
            continue
        lineup = [player for role, count in counts.items() for player in by_role[role][:count]]
        value = sum(player["selection_value"] for player in lineup)
        if league.defense_modifier_enabled:
            keeper = next((
                (player["availability"], player["pure_vote"])
                for player in lineup
                if player["ruolo"] == "P"
            ), None)
            defenders = [
                (player["availability"], player["pure_vote"])
                for player in lineup
                if player["ruolo"] == "D"
            ]
            value += expected_defense_modifier(
                keeper,
                defenders,
                league.defense_table,
                league.defense_tiers,
                league.defense_required_defenders,
            )
        if value > best_value:
            best, best_value = lineup, value
    return best


def _season_levels(players: list[dict[str, Any]], rng: np.random.Generator) -> dict[int, tuple[float, float, float]]:
    """One season's worth of luck per player: how far his level sits from the projection.

    A projection is a mean, and a player's true season strays from it by a
    measured amount (`proiezione.incertezza`, see MODEL.md): the vote level,
    the bonus level and the share of matchdays he plays all move together for
    a whole season. Drawing them once per iteration, instead of treating every
    matchday as independent, is what makes the spread of season scores
    honest. Players without the measured spread keep the old behaviour, and
    draw nothing, so seeded results of older datasets are unchanged.
    """
    levels = {}
    for player in players:
        spread = player.get("proiezione", {}).get("incertezza")
        if not spread:
            continue
        vote_sd = float(spread.get("sd_livello_voto", 0))
        bonus_sd = float(np.sqrt(max(float(spread.get("sd_livello_fantavoto", 0)) ** 2 - vote_sd ** 2, 0)))
        chances = player.get("p_gioca_per_giornata") or [player.get("proiezione", {}).get("p_gioca", 0)]
        mean = float(np.clip(np.mean(chances), 1e-3, 1 - 1e-3))
        kappa = float(spread.get("concentrazione_presenza", 0))
        share = float(rng.beta(mean * kappa, (1 - mean) * kappa)) / mean if kappa > 0 else 1.0
        levels[int(player["id"])] = (float(rng.normal(0, vote_sd)), float(rng.normal(0, bonus_sd)), share)
    return levels


def _draw_outcome(player: dict[str, Any], day_index: int, rng: np.random.Generator, team_factor: float, level: tuple[float, float, float] | None = None) -> dict[str, Any]:
    vote_shift, bonus_shift, share = level or (0.0, 0.0, 1.0)
    probability = min(1.0, player["p_gioca_per_giornata"][day_index] * share)
    outcome = {"id": player["id"], "ruolo": player["ruolo"], "selection_value": probability * (player["voto_puro_mean_per_giornata"][day_index] + player["bonus_atteso_per_giornata"][day_index]), "plays": bool(rng.random() < probability)}
    if not outcome["plays"]:
        return outcome
    pure_vote = float(np.clip(rng.normal(player["voto_puro_mean_per_giornata"][day_index] + team_factor + vote_shift, player["voto_puro_std_per_giornata"][day_index]), 4, 10))
    outcome["bonus_shift"] = bonus_shift
    rates = player.get("event_rates", {})
    goals = rng.poisson(max(0, rates.get("gol", 0)))
    assists = rng.poisson(max(0, rates.get("assist", 0)))
    yellow = rng.poisson(max(0, rates.get("ammonizioni", 0)))
    red = rng.poisson(max(0, rates.get("espulsioni", 0)))
    own_goals = rng.poisson(max(0, rates.get("autogol", 0)))
    conceded = rng.poisson(max(0, rates.get("gol_subiti", 0))) if player["ruolo"] == "P" else 0
    # Drawn only when the dataset carries them, so older datasets keep their seeded draws.
    saved = rng.poisson(max(0, rates["rigori_parati"])) if "rigori_parati" in rates else 0
    missed = rng.poisson(max(0, rates["rigori_sbagliati"])) if "rigori_sbagliati" in rates else 0
    outcome["penalties"] = (saved, missed)
    outcome.update({"pure": pure_vote, "events": (goals, assists, yellow, red, own_goals, conceded), "fantavote": pure_vote + goals * 3 + assists - yellow * .5 - red - own_goals * 2 - conceded, "selection_value": pure_vote + player["bonus_atteso_per_giornata"][day_index]})
    return outcome


def _series_a_factors(payload: dict[str, Any], serie_day: int, rng: np.random.Generator) -> dict[str, float]:
    factors: dict[str, float] = {}
    for fixture in payload["calendario_serie_a"]:
        if int(fixture["matchday"]) != serie_day:
            continue
        quality = rng.normal(0, .16)
        factors[fixture["home_team"]] = quality
        factors[fixture["away_team"]] = -quality
    return factors


def _team_score(roster: list[int], players: dict[int, dict[str, Any]], day_index: int, factors: dict[str, float], rng: np.random.Generator, league: LeagueConfig, levels: dict[int, tuple[float, float, float]] | None = None) -> tuple[float, list[dict[str, Any]]]:
    pre_lineup = []
    for player_id in roster:
        player = players[player_id]
        probability = player["p_gioca_per_giornata"][day_index]
        pre_lineup.append({
            "id": player_id,
            "ruolo": player["ruolo"],
            "availability": probability,
            "pure_vote": player["voto_puro_mean_per_giornata"][day_index],
            "selection_value": probability * (player["voto_puro_mean_per_giornata"][day_index] + player["bonus_atteso_per_giornata"][day_index]),
        })
    starters = _choose_lineup(pre_lineup, league)
    starter_ids = {player["id"] for player in starters}
    bench_pool = [player for player in pre_lineup if player["id"] not in starter_ids]
    bench_limits = Counter(league.bench_roles)
    bench = [
        player
        for role, limit in bench_limits.items()
        for player in sorted(
            (candidate for candidate in bench_pool if candidate["ruolo"] == role),
            key=lambda candidate: candidate["selection_value"],
            reverse=True,
        )[:limit]
    ]
    levels = levels or {}
    drawn = {}
    for player_id in roster:
        level = levels.get(int(player_id))
        arguments = (players[player_id], day_index, rng, factors.get(players[player_id]["squadra"], 0))
        drawn[player_id] = _draw_outcome(*arguments, level) if level is not None else _draw_outcome(*arguments)
    for outcome in drawn.values():
        if "events" in outcome:
            goals, assists, yellow, red, own_goals, conceded = outcome["events"]
            saved, missed = outcome.get("penalties", (0, 0))
            clean_sheet = league.scoring_clean_sheet if outcome["ruolo"] == "P" and conceded == 0 else 0.0
            outcome["fantavote"] = outcome["pure"] + goals * league.scoring_goal + assists * league.scoring_assist + yellow * league.scoring_yellow_card + red * league.scoring_red_card + own_goals * league.scoring_own_goal + conceded * league.scoring_goalkeeper_conceded_goal + saved * league.scoring_penalty_saved + missed * league.scoring_penalty_missed + clean_sheet + outcome.get("bonus_shift", 0.0)
    playing_starters = [drawn[player["id"]] for player in starters if drawn[player["id"]]["plays"]]
    replacements = []
    if league.switch_mode == "None":
        bench = []
    for role, limit in bench_limits.items():
        if len(replacements) >= league.max_substitutions:
            break
        missing = sum(player["ruolo"] == role for player in starters) - sum(player["ruolo"] == role for player in playing_starters)
        available = [
            drawn[player["id"]]
            for player in bench
            if player["ruolo"] == role and drawn[player["id"]]["plays"]
        ]
        replacements.extend(available[:min(missing, limit, league.max_substitutions - len(replacements))])
    final_lineup = playing_starters + replacements
    partial_lineup = final_lineup
    if len(final_lineup) != 11:
        if league.incomplete_lineup_policy in {"zero_score", "forfeit"}:
            return league.incomplete_lineup_score, final_lineup
        # allow_partial intentionally preserves points from players who received a vote.
        return sum(player["fantavote"] for player in partial_lineup), partial_lineup
    score = sum(player["fantavote"] for player in final_lineup)
    keeper = next((player["pure"] for player in final_lineup if player["ruolo"] == "P"), None)
    defender_votes = [player["pure"] for player in final_lineup if player["ruolo"] == "D"]
    if league.defense_modifier_enabled:
        score += defense_modifier(keeper, defender_votes, league.defense_table, league.defense_tiers, league.defense_required_defenders)
    return score, final_lineup


def _goals(score: float, league: LeagueConfig) -> int:
    return 0 if score < league.score_threshold else 1 + int((score - league.score_threshold) // league.points_per_virtual_goal)


def _require_league_calendar(payload: dict[str, Any]) -> Any:
    """Season simulation needs fixtures; fail with a readable message when absent."""
    calendar = payload.get("calendario_lega")
    if not calendar:
        raise ValueError("The league calendar is required to simulate a season. Upload calendario_lega and regenerate the dataset.")
    return calendar


def _calendar_teams(payload: dict[str, Any]) -> list[str]:
    calendar = _require_league_calendar(payload)
    if isinstance(calendar, dict):
        return list(calendar["teams"])
    return sorted({fixture["home_team"] for fixture in calendar} | {fixture["away_team"] for fixture in calendar})


def _calendar_matchdays(payload: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    calendar = _require_league_calendar(payload)
    if isinstance(calendar, dict):
        return {day["number"]: [{"home_team": fixture["home"], "away_team": fixture["away"], "serie_a_matchday": day["serie_a_matchday"]} for fixture in day["fixtures"]] for day in calendar["matchdays"]}
    fixtures: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for fixture in calendar:
        fixtures[int(fixture["league_matchday"])].append(fixture)
    return fixtures


def _standing_keys(
    names: list[str],
    points: dict[str, int],
    goals_for: dict[str, int],
    goals_against: dict[str, int],
    season_scores: dict[str, float],
    direct_points: dict[str, dict[str, int]],
    tie_breakers: tuple[str, ...],
) -> dict[str, tuple[int | float, ...]]:
    """Build points-first keys; head-to-head only counts matches inside each points tie group."""
    head_to_head = {
        name: sum(score for opponent, score in direct_points[name].items() if points[opponent] == points[name])
        for name in names
    }
    values = {
        "goal_difference": {name: goals_for[name] - goals_against[name] for name in names},
        "head_to_head": head_to_head,
        "season_fantasy_score": season_scores,
    }
    return {
        name: (points[name], *(values[rule][name] for rule in tie_breakers))
        for name in names
    }


def simulate_season(payload: dict[str, Any], rosters: dict[str, list[int]], iterations: int = 2000, seed: int = 202627, league: LeagueConfig | None = None) -> SimulationResult:
    """Simulate the configured calendar and return rank, payout, score and points distributions."""
    league = league or LeagueConfig()
    rosters = normalize_rosters(payload, rosters, league)
    players = {player["id"]: player for player in payload["players"]}
    fixtures_by_day = _calendar_matchdays(payload)
    expected_days = set(range(1, len(fixtures_by_day) + 1))
    if set(fixtures_by_day) != expected_days:
        raise ValueError("League calendar matchdays must be consecutive starting at 1")
    rng = np.random.default_rng(seed)
    names = _calendar_teams(payload)
    ranks = {name: np.zeros(league.participants, dtype=int) for name in names}
    utilities = {name: [] for name in names}
    points_outcomes = {name: [] for name in names}
    score_outcomes = {name: [] for name in names}
    scenarios = {name: {"best_score": -float("inf"), "worst_score": float("inf")} for name in names}
    for _ in range(iterations):
        points = {name: 0 for name in names}
        goals_for = {name: 0 for name in names}
        goals_against = {name: 0 for name in names}
        direct_points = {name: {opponent: 0 for opponent in names if opponent != name} for name in names}
        season_scores = {name: 0.0 for name in names}
        levels = _season_levels([players[player_id] for roster in rosters.values() for player_id in roster], rng)
        for league_day in sorted(fixtures_by_day):
            serie_day = int(fixtures_by_day[league_day][0]["serie_a_matchday"])
            factors = _series_a_factors(payload, serie_day, rng)
            scores, lineups = {}, {}
            for name in names:
                scores[name], lineups[name] = _team_score(rosters[name], players, serie_day - 1, factors, rng, league, levels)
            for fixture in fixtures_by_day[league_day]:
                home, away = fixture["home_team"], fixture["away_team"]
                home_goals, away_goals = _goals(scores[home], league), _goals(scores[away], league)
                goals_for[home] += home_goals; goals_against[home] += away_goals
                goals_for[away] += away_goals; goals_against[away] += home_goals
                if home_goals > away_goals:
                    home_points, away_points = league.win_points, league.loss_points
                elif away_goals > home_goals:
                    home_points, away_points = league.loss_points, league.win_points
                else:
                    home_points = away_points = league.draw_points
                points[home] += home_points; points[away] += away_points
                direct_points[home][away] += home_points; direct_points[away][home] += away_points
            for name in names:
                season_scores[name] += scores[name]
        standing_keys = _standing_keys(names, points, goals_for, goals_against, season_scores, direct_points, league.tie_breakers)
        ordered = sorted(names, key=standing_keys.__getitem__, reverse=True)
        previous_key = None
        shared_rank = 0
        for rank, name in enumerate(ordered):
            standing_key = standing_keys[name]
            if league.exact_tie_policy == "shared_rank" and standing_key == previous_key:
                effective_rank = shared_rank
            else:
                effective_rank = rank
                shared_rank = rank
            previous_key = standing_key
            ranks[name][effective_rank] += 1
            utilities[name].append(league.net_utilities_eur[effective_rank] if effective_rank < len(league.payouts) else -league.entry_fee_eur)
            points_outcomes[name].append(points[name])
            score_outcomes[name].append(season_scores[name])
            if season_scores[name] > scenarios[name]["best_score"]:
                scenarios[name].update({"best_score": round(season_scores[name], 2), "best_points": points[name], "best_rank": effective_rank + 1})
            if season_scores[name] < scenarios[name]["worst_score"]:
                scenarios[name].update({"worst_score": round(season_scores[name], 2), "worst_points": points[name], "worst_rank": effective_rank + 1})
    summary = {}
    for name in names:
        summary[name] = {"rank_probabilities": (ranks[name] / iterations).round(4).tolist(), "top3_probability": round(float(ranks[name][:3].sum() / iterations), 4), "expected_utility": round(float(np.mean(utilities[name])), 2), "expected_points": round(float(np.mean(points_outcomes[name])), 2), "expected_score": round(float(np.mean(score_outcomes[name])), 2), "score_p05": round(float(np.quantile(score_outcomes[name], .05)), 2), "score_p95": round(float(np.quantile(score_outcomes[name], .95)), 2)}
    return SimulationResult(iterations=iterations, teams=summary, scenarios=scenarios, diagnostics={"matchdays": len(fixtures_by_day), "seed": seed})
