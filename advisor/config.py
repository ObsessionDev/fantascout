from dataclasses import dataclass

from .league_profile import LeagueProfile


@dataclass(frozen=True)
class ModelConfig:
    season_days: int = 38
    history_weights: tuple[float, float, float] = (0.6, 0.3, 0.1)
    european_rotation_discount: float = 0.90
    default_std: dict[str, float] = None  # type: ignore[assignment]
    defense_table: str = "LEAGUE"
    # A player with no rated history gets his event rates from a per-role fit on
    # log(1 + FVM) instead of from zero. See fit_rate_priors in pipeline.py.
    impute_missing_history: bool = True
    rate_prior_min_samples: int = 12

    def __post_init__(self):
        if self.default_std is None:
            object.__setattr__(self, "default_std", {"P": 0.65, "D": 0.75, "C": 0.85, "A": 0.90})


ROSTER_SLOTS = {"P": 3, "D": 8, "C": 8, "A": 6}


@dataclass(frozen=True)
class LeagueConfig:
    """Confirmed rules for the 2026/27 Classic league."""
    participants: int = 8
    starting_credits: int = 750
    entry_fee_eur: int = 100
    score_threshold: int = 66
    points_per_virtual_goal: int = 5
    defense_modifier_enabled: bool = True
    defense_table: str = "LEAGUE"
    discard_injury_months_over: int = 3
    discard_allowed_through_matchday: int = 28
    payouts_eur: tuple[int, int, int] = (300, 220, 100)
    allowed_formations: tuple[str, ...] = ("3-4-3", "3-5-2", "4-3-3", "4-4-2", "4-5-1", "5-3-2", "5-4-1")
    bench_roles: tuple[str, ...] = ("P", "P", "D", "D", "D", "C", "C", "C", "A", "A", "A")
    switch_mode: str = "Basic"
    max_substitutions: int = 3
    bench_composition: str = "by_role"
    bench_size: int = 11
    slots: tuple[tuple[str, int], ...] = (("P", 3), ("D", 8), ("C", 8), ("A", 6))
    team_names: tuple[str, ...] = ()
    user_team: str = ""
    scoring_goal: float = 3
    scoring_assist: float = 1
    scoring_yellow_card: float = -.5
    scoring_red_card: float = -1
    scoring_own_goal: float = -2
    scoring_goalkeeper_conceded_goal: float = -1
    defense_required_defenders: int = 4
    defense_tiers: tuple[tuple[float, float], ...] = ((6.0, 1), (6.5, 2), (7.0, 3))
    win_points: int = 3
    draw_points: int = 1
    loss_points: int = 0
    tie_breakers: tuple[str, ...] = ("goal_difference", "head_to_head", "season_fantasy_score")
    exact_tie_policy: str = "shared_rank"
    payouts: tuple[int, ...] = (300, 220, 100)
    unplaced_payout_policy: str = "no_payout"
    incomplete_lineup_policy: str = "zero_score"
    incomplete_lineup_score: float = 0
    minimum_bid: int = 1
    bid_increment: int = 1
    reserve_credits_per_open_slot: int = 1

    @classmethod
    def from_profile(cls, profile: LeagueProfile) -> "LeagueConfig":
        """Adapt the versioned profile without making services depend on its shape."""
        return cls(
            participants=len(profile.participants.team_names),
            team_names=profile.participants.team_names,
            user_team=profile.participants.user_team,
            starting_credits=profile.credits.starting,
            entry_fee_eur=profile.credits.entry_fee_eur,
            score_threshold=profile.virtual_goals.threshold,
            points_per_virtual_goal=profile.virtual_goals.step,
            defense_modifier_enabled=profile.defense_modifier.enabled,
            defense_table=profile.defense_modifier.table_name,
            defense_required_defenders=profile.defense_modifier.required_defenders,
            defense_tiers=tuple((tier.minimum_average, tier.bonus) for tier in profile.defense_modifier.tiers),
            payouts_eur=tuple(prize.amount_eur for prize in profile.payouts.prizes),
            payouts=tuple(prize.amount_eur for prize in profile.payouts.prizes),
            unplaced_payout_policy=profile.payouts.unplaced_policy,
            allowed_formations=profile.formations.allowed,
            bench_roles=profile.bench_switch.bench_roles,
            switch_mode=profile.bench_switch.mode,
            max_substitutions=profile.bench_switch.max_substitutions,
            bench_composition=profile.bench_switch.composition,
            bench_size=profile.bench_switch.size,
            slots=tuple((role, getattr(profile.roster_slots, role)) for role in ("P", "D", "C", "A")),
            scoring_goal=profile.scoring.goal,
            scoring_assist=profile.scoring.assist,
            scoring_yellow_card=profile.scoring.yellow_card,
            scoring_red_card=profile.scoring.red_card,
            scoring_own_goal=profile.scoring.own_goal,
            scoring_goalkeeper_conceded_goal=profile.scoring.goalkeeper_conceded_goal,
            win_points=profile.standings.win_points,
            draw_points=profile.standings.draw_points,
            loss_points=profile.standings.loss_points,
            tie_breakers=profile.standings.tie_breakers,
            exact_tie_policy=profile.standings.exact_tie_policy,
            incomplete_lineup_policy=profile.incomplete_lineup.policy,
            incomplete_lineup_score=profile.incomplete_lineup.score,
            minimum_bid=profile.auction.minimum_bid,
            bid_increment=profile.auction.bid_increment,
            reserve_credits_per_open_slot=profile.auction.reserve_credits_per_open_slot,
        )

    @property
    def roster_size(self) -> int:
        return sum(count for _, count in self.slots)

    @property
    def credit_pool(self) -> int:
        """Every credit that will change hands in the auction."""
        return self.participants * self.starting_credits

    @property
    def roster_slots(self) -> dict[str, int]:
        return dict(self.slots)

    @property
    def net_utilities_eur(self) -> tuple[int, int, int, int]:
        return tuple(amount - self.entry_fee_eur for amount in self.payouts) + (-self.entry_fee_eur,)
