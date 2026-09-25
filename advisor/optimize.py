"""Target-driven roster construction.

Upstream answers "given this roster, what is it worth?". The auction needs the
inverse: "given a target in fantasy points, what is the cheapest roster that
reaches it?", and, once the auction is under way, "given what I already own,
what is the best use of the credits I have left?".

Two structural facts drive the whole module:

* Only the fielded eleven score. A roster of twenty-five contains fourteen
  players whose job is to cover absences, so credits spent above the minimum
  bid on those slots buy almost nothing. The optimiser therefore fills every
  slot at the minimum bid first and then spends the remaining credits on
  upgrades, best value per credit first.
* The target is a slider, not a constant. It is always read as
  ``virtual_goals.threshold + k * virtual_goals.step`` from the active profile.

Nothing here hard-codes a league parameter: credits, slots, formations, minimum
bid and reserve all come from the profile.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import LeagueConfig
from .league_profile import LeagueProfile

ROLES = ("P", "D", "C", "A")


def _points(player: dict) -> float:
    return float(player.get("mercato", {}).get("fp_per_giornata", 0.0))


def _price(player: dict, league: LeagueConfig) -> float:
    return max(float(player.get("mercato", {}).get("prezzo_atteso", league.minimum_bid)), league.minimum_bid)


def parse_formations(league: LeagueConfig) -> list[tuple[int, int, int]]:
    """Allowed outfield shapes as (defenders, midfielders, forwards)."""
    shapes = []
    for formation in league.allowed_formations:
        parts = tuple(int(piece) for piece in str(formation).split("-"))
        if len(parts) == 3 and sum(parts) == 10:
            shapes.append(parts)
    return shapes or [(4, 4, 2)]


def _fantavoto(player: dict) -> float:
    """Points scored when the player is rated, as opposed to over all matchdays."""
    return float(player.get("mercato", {}).get("fantavoto_medio", player.get("proiezione", {}).get("fantavoto", 0.0)))


def _probability(player: dict) -> float:
    return float(player.get("mercato", {}).get("p_gioca_medio", player.get("proiezione", {}).get("p_gioca", 0.0)))


def _coverage(bench: list[dict]) -> float:
    """Expected points from the first bench player of a role who is actually rated.

    Each candidate is tried in turn, so the term is the probability-weighted
    value of the substitution, not the value of the best bench player.
    """
    remaining, value = 1.0, 0.0
    for player in bench:
        probability = _probability(player)
        value += remaining * probability * _fantavoto(player)
        remaining *= 1 - probability
    return value


_LINEUP_CACHE: dict[tuple, float] = {}
_LINEUP_CACHE_KEY: object = None
_LINEUP_CACHE_LIMIT = 300_000


def reset_lineup_cache(dataset_key: object = None) -> None:
    """Drop the memo when the underlying projections change.

    The cached value depends on every player's fantavoto and availability, not
    just on which players are in the roster, so a cache carried across datasets
    or profiles would answer for the wrong league.
    """
    global _LINEUP_CACHE_KEY
    _LINEUP_CACHE.clear()
    _LINEUP_CACHE_KEY = dataset_key


def ensure_lineup_cache(dataset_key: object) -> None:
    """Keep the memo across calls that share a dataset, drop it when it changes.

    A long-lived server answers many questions about the same auction, and the
    rosters it evaluates overlap almost entirely between them. Clearing on every
    request would throw that away and roughly double the time to price a player,
    which is the one number the auction is waiting on.
    """
    if dataset_key != _LINEUP_CACHE_KEY:
        reset_lineup_cache(dataset_key)


@dataclass
class LineupPlan:
    """The legal formation, XI and ordered bench behind `lineup_value`'s score.

    The auction only ever needed the number, so it stayed inside the function.
    A single matchday needs the selection itself — who starts, who is next in
    at each position — which is why this exists alongside it rather than in
    place of it.
    """
    value: float = 0.0
    formation: tuple[int, int, int] | None = None
    starters: dict[str, list[dict]] = field(default_factory=dict)
    bench: dict[str, list[dict]] = field(default_factory=dict)
    unused: list[dict] = field(default_factory=list)


def best_lineup(roster: list[dict], league: LeagueConfig) -> LineupPlan:
    """The formation, starters and bench order that maximise `lineup_value`.

    Same model as `lineup_value` — coverage, the substitution cap, the
    incomplete-lineup score — kept in one place so the two never drift apart.
    `formation` is `None` when no allowed shape can be filled at all, which a
    caller building an actual lineup must treat as "no legal lineup exists",
    not as a zero-value one.
    """
    by_role = {role: sorted((p for p in roster if p["ruolo"] == role), key=_fantavoto, reverse=True) for role in ROLES}
    plan = LineupPlan(unused=list(roster))
    if not by_role["P"]:
        return plan
    substitutions_allowed = league.max_substitutions if league.switch_mode != "None" else 0
    for defenders, midfielders, forwards in parse_formations(league):
        need = {"P": 1, "D": defenders, "C": midfielders, "A": forwards}
        if any(len(by_role[role]) < count for role, count in need.items()):
            continue
        starters, spare = {}, []
        for role, count in need.items():
            starters[role] = by_role[role][:count]
            spare.extend(by_role[role][count:])
        # A bench of a fixed size, whatever the roles, is a scarce resource the
        # roles compete for: a fourth spare defender only travels if he is worth
        # more as cover than a second spare forward. A bench composed per role
        # has no such competition, so every spare player of that role is
        # available and the sort below is a no-op.
        if league.bench_composition == "any_role":
            spare.sort(key=lambda p: _probability(p) * _fantavoto(p), reverse=True)
            spare = spare[:league.bench_size]
        coverage_by_role = {
            role: _coverage([p for p in spare if p["ruolo"] == role]) for role in need
        }
        absences = sum(1 - _probability(p) for group in starters.values() for p in group)
        # An absence beyond the cap scores whatever an incomplete lineup scores.
        covered = min(1.0, substitutions_allowed / absences) if absences > 0 else 1.0
        total = 0.0
        for role, group in starters.items():
            for player in group:
                probability = _probability(player)
                missing = 1 - probability
                total += probability * _fantavoto(player)
                total += missing * covered * coverage_by_role[role]
                total += missing * (1 - covered) * league.incomplete_lineup_score
        if plan.formation is None or total > plan.value:
            bench_by_role = {role: [p for p in spare if p["ruolo"] == role] for role in ROLES}
            used_ids = {p["id"] for group in starters.values() for p in group} | {p["id"] for p in spare}
            plan = LineupPlan(value=total, formation=(defenders, midfielders, forwards),
                              starters=starters, bench=bench_by_role,
                              unused=[p for p in roster if p["id"] not in used_ids])
    return plan


def lineup_value(roster: list[dict], league: LeagueConfig) -> float:
    """Expected points of the best legal eleven, substitutions included.

    Charging a starter for the matchdays he misses, as the listing measure does,
    understates a roster by roughly a seventh: the expected number of absences
    in an eleven is well below the substitution cap, so most missed votes are
    covered by the bench. The value of a slot is therefore the starter when he
    plays plus the bench behind him when he does not, with the total number of
    substitutions capped as the profile dictates.
    """
    # Only who is in the roster matters here, never what was paid, so the value
    # is memoised on the set of players. The search re-evaluates the same
    # rosters constantly while bisecting a price.
    key = frozenset(p["id"] for p in roster)
    cached = _LINEUP_CACHE.get(key)
    if cached is not None:
        return cached
    value = best_lineup(roster, league).value
    if len(_LINEUP_CACHE) >= _LINEUP_CACHE_LIMIT:
        _LINEUP_CACHE.clear()
    _LINEUP_CACHE[key] = value
    return value


@dataclass
class Solution:
    roster: list[dict] = field(default_factory=list)
    spent: float = 0.0
    points: float = 0.0
    budget: float = 0.0
    shadow_price: float = 0.0
    # (spesa cumulata, valore) dopo ogni upgrade accettato: la funzione valore
    # del piano, che il greedy produce comunque mentre lavora.
    trace: list[tuple[float, float]] = field(default_factory=list)

    @property
    def credits_left(self) -> float:
        return self.budget - self.spent

    def value_at(self, budget: float) -> float:
        """Plan value reachable with this budget, read off the greedy's own path."""
        best = 0.0
        for spend, value in self.trace:
            if spend <= budget + 1e-9:
                best = value
            else:
                break
        return best


def _candidate_pool(in_role: list[dict], best: int = 45, cheap: int = 25) -> list[dict]:
    """Players who can plausibly end up in a roster.

    Only two kinds matter: the ones good enough to be worth upgrading to, and
    the ones cheap enough to fill a covering slot. Everyone in between is
    dominated, so carrying them only slows the search.
    """
    by_value = sorted(in_role, key=_fantavoto, reverse=True)[:best]
    by_price = sorted(in_role, key=lambda p: (p.get("mercato", {}).get("prezzo_atteso", 0), -_fantavoto(p)))[:cheap]
    seen, pool = set(), []
    for player in [*by_value, *by_price]:
        if player["id"] not in seen:
            seen.add(player["id"])
            pool.append(player)
    return pool


def _cheapest_fill(pool: dict[str, list[dict]], owned: list[dict], league: LeagueConfig) -> list[dict] | None:
    """Complete the roster with the least expensive players available."""
    roster = list(owned)
    counts = {role: sum(1 for p in roster if p["ruolo"] == role) for role in ROLES}
    for role, slots in league.slots:
        missing = slots - counts.get(role, 0)
        if missing <= 0:
            continue
        taken = {p["id"] for p in roster}
        candidates = [p for p in pool[role] if p["id"] not in taken]
        candidates.sort(key=lambda p: (_price(p, league), -_points(p)))
        if len(candidates) < missing:
            return None
        roster.extend(candidates[:missing])
    return roster


def optimise(players: list[dict], league: LeagueConfig, owned: list[dict] | None = None,
             budget: float | None = None, target: float | None = None,
             stop_at_target: bool = False) -> Solution:
    """Most points the remaining credits can buy.

    Unspent credits are worth nothing, so the objective is to maximise points
    within the budget, not to reach a target as cheaply as possible. `target` is
    a floor to report on; pass `stop_at_target` only when the question really is
    "what is the least this costs", as the cost curve asks.

    Greedy on gain per credit over upgrades, which is exact enough here because
    the eleven slots are near-independent once the roster is filled: an upgrade
    at one slot barely changes what an upgrade at another is worth.

    `shadow_price` is the gain per credit of the last upgrade accepted, that is
    the exchange rate between credits and points at the optimum. It is what
    turns a projection into a maximum bid.
    """
    owned = list(owned or [])
    budget = float(league.starting_credits if budget is None else budget)
    owned_ids = {p["id"] for p in owned}
    pool = {role: _candidate_pool([p for p in players if p["ruolo"] == role and p["id"] not in owned_ids])
            for role in ROLES}

    roster = _cheapest_fill(pool, owned, league)
    if roster is None:
        return Solution(budget=budget)
    spent = sum(_price(p, league) for p in roster if p["id"] not in owned_ids)

    upgradable = {p["id"] for p in roster if p["id"] not in owned_ids}
    shadow = 0.0
    trace = [(spent, lineup_value(roster, league))]
    while True:
        current_value = lineup_value(roster, league)
        best = None
        for role in ROLES:
            in_role = sorted((p for p in roster if p["ruolo"] == role), key=_fantavoto)
            replaceable = [p for p in in_role if p["id"] in upgradable]
            if not replaceable:
                continue
            worst = replaceable[0]
            in_roster = {p["id"] for p in roster}
            for candidate in pool[role]:
                if candidate["id"] in in_roster:
                    continue
                extra = _price(candidate, league) - _price(worst, league)
                if extra <= 0 or spent + extra > budget:
                    continue
                trial = [p for p in roster if p["id"] != worst["id"]] + [candidate]
                gain = lineup_value(trial, league) - current_value
                if gain <= 0:
                    continue
                ratio = gain / extra
                if best is None or ratio > best[0]:
                    best = (ratio, worst, candidate, extra)
        if best is None:
            break
        ratio, worst, candidate, extra = best
        roster = [p for p in roster if p["id"] != worst["id"]] + [candidate]
        upgradable.discard(worst["id"])
        upgradable.add(candidate["id"])
        spent += extra
        shadow = ratio
        trace.append((spent, lineup_value(roster, league)))
        if stop_at_target and target is not None and lineup_value(roster, league) >= target:
            break
    return Solution(roster=roster, spent=spent, points=lineup_value(roster, league),
                    budget=budget, shadow_price=shadow, trace=trace)


def _shortlist(players: list[dict], league: LeagueConfig, solution: Solution,
               owned_ids: set, size: int) -> list[dict]:
    """Cheap screen: who could improve the plan at all, before pricing them exactly."""
    roster_ids = {p["id"] for p in solution.roster}
    base = lineup_value(solution.roster, league)
    scored = []
    for role in ROLES:
        in_role = sorted((p for p in solution.roster if p["ruolo"] == role), key=_fantavoto)
        replaceable = [p for p in in_role if p["id"] not in owned_ids]
        if not replaceable:
            continue
        worst = replaceable[0]
        rest = [p for p in solution.roster if p["id"] != worst["id"]]
        for candidate in players:
            if candidate["ruolo"] != role or candidate["id"] in roster_ids or candidate["id"] in owned_ids:
                continue
            gain = lineup_value(rest + [candidate], league) - base
            if gain > 0:
                scored.append((gain, candidate))
    scored.sort(key=lambda item: -item[0])
    return [candidate for _, candidate in scored[:size]]


def marginal_credit_value(solution: Solution, window: float = 0.35) -> float:
    """How many points a credit buys at the margin, in the state we are in.

    Not the last upgrade's ratio, which is a single noisy step, but the slope of
    the plan's value over the last stretch of spending. That stretch is where a
    credit spent now would actually go.
    """
    trace = solution.trace
    if len(trace) < 2 or solution.spent <= 0:
        return 0.0
    floor = solution.spent - window * (solution.spent - trace[0][0])
    start = trace[0]
    for point in trace:
        if point[0] <= floor:
            start = point
        else:
            break
    end = trace[-1]
    credits = end[0] - start[0]
    return (end[1] - start[1]) / credits if credits > 0 else 0.0


def maximum_bid_for(players: list[dict], league: LeagueConfig, solution: Solution,
                    candidate: dict, owned: list[dict] | None = None,
                    budget: float | None = None, probes: int = 9) -> dict:
    """Exact maximum bid for the one player currently under the hammer.

    This is the auction-time entry point. Pricing a whole role in advance costs
    a solve per candidate and is far slower than the auction needs; pricing the
    single player who has just been called costs about a second, which is
    faster than typing his name. Nothing is precomputed and nothing goes stale.

    Nine probes because the cost is almost entirely the first solve: the trial
    rosters overlap it, so a warm memo makes each probe about 35 ms. Measured,
    the residual error falls from 21.9 credits with the seed alone, to 2.2 at
    seven probes, to 0.2 at nine, for seventy extra milliseconds. Past nine
    nothing moves, because what is left is the greedy's own approximation and
    no amount of bisection reaches through it.
    """
    owned = list(owned or [])
    budget = float(solution.budget if budget is None else budget)
    with_candidate = optimise(players, league, owned=[*owned, candidate], budget=budget)
    # What he is worth is the points he adds; the ceiling is that divided by
    # what a credit buys at the margin. Early in the auction the plan cannot
    # spend the budget usefully, so a credit buys almost nothing and the
    # division amplifies a hair's difference in points into tens of credits.
    # The gain is the stable quantity and travels alongside, because a ceiling
    # read without it invites paying real credits for nothing.
    gain = with_candidate.points - solution.points
    row = {"giocatore": candidate, "prezzo_atteso": _price(candidate, league),
           "prezzo_massimo": 0.0, "margine": 0.0,
           "guadagno": round(max(0.0, gain), 4),
           "piano_saturo": solution.credits_left <= league.minimum_bid * 2}
    if with_candidate.value_at(budget) < solution.points:
        row["margine"] = round(-row["prezzo_atteso"], 1)
        return row
    low = 0.0
    for spend, value in with_candidate.trace:
        if value >= solution.points:
            low = max(low, budget - spend)
    high = budget
    for _ in range(probes):
        mid = (low + high) / 2
        if optimise(players, league, owned=[*owned, candidate], budget=budget - mid).points >= solution.points:
            low = mid
        else:
            high = mid
    row["prezzo_massimo"] = round(low, 1)
    row["margine"] = round(low - row["prezzo_atteso"], 1)
    return row


def maximum_bids(players: list[dict], league: LeagueConfig, solution: Solution,
                 owned: list[dict] | None = None, budget: float | None = None,
                 limit: int = 15, probes: int = 6) -> list[dict]:
    """The most each candidate is worth right now, in credits.

    Defined against the plan, not against a single swap: the maximum bid is the
    highest price at which owning the player, and then spending what is left as
    well as possible, still beats the plan that does not include him. Paying a
    credit more than this makes the roster worse, however good the player is —
    which is exactly the question a live auction asks.

    Found by bisection, because the plan's value falls monotonically as the
    budget shrinks. Restricted to a shortlist, since each probe re-solves.
    """
    owned = list(owned or [])
    owned_ids = {p["id"] for p in owned}
    budget = float(solution.budget if budget is None else budget)
    baseline = solution.points
    rows = []
    for candidate in _shortlist(players, league, solution, owned_ids, limit):
        # The first solve does double duty. Its trace gives a valid lower bound
        # on the maximum bid for free — truncating the plan at a smaller spend
        # is one way to afford the candidate, just not the best way, since a
        # tighter budget would have bought different upgrades. Seeding the
        # bisection with that bound instead of with zero removes most of the
        # search: measured on this listing the bound is already exact for two
        # candidates in three, and never above the true value.
        with_candidate = optimise(players, league, owned=[*owned, candidate], budget=budget)
        if with_candidate.value_at(budget) < baseline:
            continue
        low = 0.0
        for spend, value in with_candidate.trace:
            if value >= baseline:
                low = max(low, budget - spend)
        high = budget
        for _ in range(probes):
            mid = (low + high) / 2
            if optimise(players, league, owned=[*owned, candidate], budget=budget - mid).points >= baseline:
                low = mid
            else:
                high = mid
        ceiling = low
        rows.append({
            "giocatore": candidate,
            "prezzo_atteso": _price(candidate, league),
            "prezzo_massimo": round(ceiling, 1),
            "margine": round(ceiling - _price(candidate, league), 1),
        })
    rows.sort(key=lambda row: -row["margine"])
    return rows


def open_slots_by_role(league: LeagueConfig, filled_by_role: dict[str, int]) -> dict[str, int]:
    """Slots the league as a whole still has to fill, per role.

    The auction runs one role at a time and a phase ends only when every manager
    has completed that role, so this is counted, never estimated.
    """
    return {role: max(0, league.participants * slots - filled_by_role.get(role, 0))
            for role, slots in league.slots}


def reprice_remaining_market(players: list[dict], league: LeagueConfig, spent_in_league: float,
                             filled_by_role: dict[str, int]) -> None:
    """Re-derive expected prices from the credits and the slots the league has left.

    The pre-auction price assumes the whole pool is chasing the whole listing.
    Once managers start paying, both sides shrink, and rarely at the same rate:
    a room that has overspent early has fewer credits per remaining slot, so
    everything still on the board gets cheaper, and the reverse when the room is
    thrifty. Ignoring this is how a manager ends up holding credits he can no
    longer convert into players.

    Splitting what is left across the roles still open is the part that has to
    be counted rather than assumed. Distributing the remaining slots on the
    original role proportions is wrong under a role-ordered auction and gets
    worse at every phase boundary: with only forwards left it would price the
    last forty-eight of them as if a quarter of the money were still going to
    goalkeepers. Here each open role claims a share of the remaining credits in
    proportion to the market value it still has to absorb, which keeps the
    identity intact — expected prices over the players still to be bought sum
    to the credits still to be spent.

    Mutates `mercato.prezzo_atteso` in place. `filled_by_role` counts every
    player already sold, to any manager.
    """
    open_slots = open_slots_by_role(league, filled_by_role)
    credits_left = max(0.0, league.credit_pool - spent_in_league)
    still_needed: list[dict] = []
    for role, count in open_slots.items():
        if count <= 0:
            continue
        in_role = sorted((p for p in players if p["ruolo"] == role), key=lambda p: -p["fvm_original"])
        still_needed.extend(in_role[:count])
    if not still_needed:
        return
    total_fvm = sum(p["fvm_original"] for p in still_needed)
    floor_spend = len(still_needed) * league.minimum_bid
    scale = max(0.0, credits_left - floor_spend) / total_fvm if total_fvm > 0 else 0.0
    for player in players:
        player.setdefault("mercato", {})["prezzo_atteso"] = round(
            league.minimum_bid + scale * player["fvm_original"], 1)


def target_for(profile: LeagueProfile, k: float) -> float:
    """The slider: never a literal."""
    return float(profile.virtual_goals.threshold + k * profile.virtual_goals.step)


def cost_curve(players: list[dict], league: LeagueConfig, profile: LeagueProfile,
               ks: list[float], owned: list[dict] | None = None, budget: float | None = None) -> list[dict]:
    """What each step of target costs, so the marginal point has a price."""
    rows = []
    for k in ks:
        target = target_for(profile, k)
        solution = optimise(players, league, owned=owned, budget=budget, target=target, stop_at_target=True)
        rows.append({"k": k, "target": target, "punti": round(solution.points, 2),
                     "spesa": round(solution.spent, 1), "raggiunto": solution.points >= target})
    return rows


def load_players(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["players"]


def resolve_player_token(players: list[dict], token: str) -> dict:
    """Match a single `"Nome"` or `"#id"` token to exactly one player.

    The listing has no exact duplicate names but eighteen shared surnames
    (`Martinez Jo.` and `Martinez L.`, `Terracciano` and `Terracciano F.`), and
    assigning the wrong player during an auction cannot be undone. A name that
    does not identify exactly one player is refused rather than guessed; any
    caller that has an id, the UI included, should pass `#id`.
    """
    token = token.strip()
    if token.startswith("#"):
        by_id = {str(p["id"]): p for p in players}
        player = by_id.get(token[1:])
        if player is None:
            raise SystemExit(f"id non trovato: {token!r}")
        return player
    matches = [p for p in players if p["nome"].lower() == token.lower()]
    if not matches:
        near = [p["nome"] for p in players if token.lower() in p["nome"].lower()][:5]
        raise SystemExit(f"giocatore non trovato: {token!r}" + (f" — forse: {', '.join(near)}" if near else ""))
    if len(matches) > 1:
        raise SystemExit(f"nome ambiguo {token!r}: " +
                         ", ".join(f"#{p['id']} {p['nome']} ({p['squadra']})" for p in matches))
    return matches[0]


def resolve_players(players: list[dict], spec: str) -> tuple[list[dict], float]:
    """`"Nome:prezzo"` or `"#id:prezzo"`; prices are what was actually paid.

    See `resolve_player_token` for how a token is matched.
    """
    owned, paid = [], 0.0
    for chunk in filter(None, (piece.strip() for piece in spec.split(","))):
        token, _, price = chunk.rpartition(":")
        if not token:
            token, price = chunk, ""
        owned.append(resolve_player_token(players, token))
        paid += float(price or 0)
    return owned, paid


def filled_counts(*groups: list[dict]) -> dict[str, int]:
    """How many slots of each role the league has already filled."""
    counts = {role: 0 for role in ROLES}
    for group in groups:
        for player in group:
            counts[player["ruolo"]] = counts.get(player["ruolo"], 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Uso efficiente dei crediti in asta")
    parser.add_argument("--data", required=True, help="auction_data.json generato dalla pipeline")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--k", type=float, default=1, help="target minimo = threshold + k * step")
    parser.add_argument("--owned", default="", help='miei acquisti: "Nome:prezzo,Nome:prezzo"')
    parser.add_argument("--taken", default="", help='acquisti delle altre squadre: "Nome:prezzo,..."')
    parser.add_argument("--curve", action="store_true",
                        help="curva costo/target: la sola domanda in cui minimizzare la spesa ha senso")
    parser.add_argument("--player", default="",
                        help='giocatore appena chiamato: prezzo massimo esatto, "Nome" o "#id"')
    parser.add_argument("--bids", type=int, default=15, help="quanti prezzi massimi mostrare (0 per nessuno)")
    parser.add_argument("--informative-only", action="store_true",
                        help="escludi i giocatori con rate imputati dal FVM")
    args = parser.parse_args(argv)

    profile = LeagueProfile.load_json(args.profile)
    league = LeagueConfig.from_profile(profile)
    players = load_players(Path(args.data))
    reset_lineup_cache(profile.configuration_hash)
    if args.informative_only:
        players = [p for p in players if p.get("mercato", {}).get("informativo", True)]

    owned, paid = resolve_players(players, args.owned)
    taken, paid_by_others = resolve_players(players, args.taken)
    taken_ids = {p["id"] for p in taken}
    available = [p for p in players if p["id"] not in taken_ids]
    if taken or owned:
        reprice_remaining_market(available, league, paid + paid_by_others, filled_counts(owned, taken))
    budget = league.starting_credits - paid
    target = target_for(profile, args.k)

    if args.curve:
        print(f"{'k':>4} {'target':>7} {'punti':>7} {'spesa':>7}  esito")
        for row in cost_curve(available, league, profile, [0, .5, 1, 1.5, 2, 2.5, 3], owned, budget):
            print(f"{row['k']:>4} {row['target']:>7.1f} {row['punti']:>7.2f} {row['spesa']:>7.1f}  "
                  f"{'ok' if row['raggiunto'] else 'FUORI PORTATA'}")
        return 0

    solution = optimise(available, league, owned=owned, budget=budget, target=target)

    if args.player:
        called, _ = resolve_players(available, args.player)
        row = maximum_bid_for(available, league, solution, called[0], owned, budget)
        player = row["giocatore"]
        note = "" if player.get("mercato", {}).get("informativo", True) else "   rate imputati dal FVM: nessuna informazione oltre al prezzo"
        print(f"{player['nome']} ({player['ruolo']}, {player['squadra']})")
        print(f"  {_points(player):.2f} FP/giornata | prezzo atteso {row['prezzo_atteso']:.1f}cr")
        if row["prezzo_massimo"] <= 0:
            print("  NON RILANCIARE: non migliora il piano a nessun prezzo")
        else:
            print(f"  PREZZO MASSIMO {row['prezzo_massimo']:.0f} crediti (margine {row['margine']:+.1f})")
        if note:
            print(note)
        return 0

    verdict = "sopra il minimo" if solution.points >= target else "SOTTO IL MINIMO"
    print(f"minimo k={args.k} -> {target:.1f} FP/giornata | crediti miei {budget:.0f} "
          f"| fuori dal listone {len(taken)} giocatori")
    print(f"XI atteso {solution.points:.2f} FP/giornata ({verdict}) | impegnati {solution.spent:.1f} "
          f"| non allocati {solution.credits_left:.1f}")
    if solution.shadow_price > 0:
        print(f"al margine 1 credito vale {solution.shadow_price:.4f} FP/giornata "
              f"(cioe 1 FP costa {1 / solution.shadow_price:.1f} crediti)")
    print()
    for role, _ in league.slots:
        group = sorted((p for p in solution.roster if p["ruolo"] == role), key=_fantavoto, reverse=True)
        print(f"[{role}]")
        for player in group:
            mark = "*" if player["id"] in {p["id"] for p in owned} else " "
            flag = "" if player.get("mercato", {}).get("informativo", True) else "  (rate da FVM)"
            print(f"  {mark} {player['nome'][:22]:22s} {player['squadra'][:12]:12s} "
                  f"{_price(player, league):6.1f}cr  {_points(player):5.2f} FP/g{flag}")
    if args.bids:
        print()
        print("PREZZO MASSIMO DI RILANCIO (oltre, i crediti rendono di piu altrove)")
        for row in maximum_bids(available, league, solution, owned, budget, args.bids):
            player = row["giocatore"]
            flag = "" if player.get("mercato", {}).get("informativo", True) else "  (rate da FVM)"
            verdict = "OCCASIONE" if row["margine"] > 0 else "lascia andare"
            print(f"  {player['ruolo']} {player['nome'][:20]:20s} {player['squadra'][:11]:11s} "
                  f"atteso {row['prezzo_atteso']:6.1f}  max {row['prezzo_massimo']:6.1f}cr  "
                  f"margine {row['margine']:+6.1f}  {verdict}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
