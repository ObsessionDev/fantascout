# Model Assumptions

The application is intentionally a Classic Fantacalcio advisor for Serie A.
The Serie A source calendar is validated as 20 teams, 38 matchdays and 380
matches. The fantasy league may select a shorter configured interval within
that season.

## Player projections

- Historical observations are weighted newest to oldest: 60%, 30%, 10%.
- A player marked `TITOLARE`, `BALLOTTAGGIO`, or `RISERVA` has a current
  availability prior of 85%, 55%, or 15%. When historical availability exists,
  the final probability is 65% current prior and 35% history.
- Event rates are normalized to a documented 75-minute rated appearance.
- A player with no rated appearance in any loaded season does not take zero for
  his event rates. Zero is not a neutral value: the expected bonus is negative
  for goalkeepers, who concede goals, and positive for midfielders and forwards,
  so a missing history would inflate one end of the listing and deflate the
  other. Each rate is instead regressed on `log(1 + FVM)` across the players of
  the same role who do have history, and the fit is evaluated at the player's
  own FVM. Coefficients are fitted at run time from the loaded seasons and are
  never hard-coded; a role with fewer than `rate_prior_min_samples` observations
  or no spread collapses to a flat prior at the role median, and imputed rates
  are clipped at zero.
  The market value is the only signal available for these players, so their
  projection is by construction consistent with their price: the imputation
  removes a directional bias, it does not manufacture an edge. Such players
  carry `proiezione.fonte_rate = "prior_fvm"` and must be shown as low
  confidence. Set `ModelConfig.impute_missing_history = False` to restore the
  previous zero-rate behaviour.
- Primary penalty takers receive a 0.12 expected-goal-per-90 uplift.
- European competitions apply a rotation discount to outfield availability.
- Fixture projections vary by opponent strength and home/away status while
  preserving the player-level seasonal mean.

## Auction values

- The source FVM is preserved as `fvm_original`.
- The UI allocates configured role budgets using FVM as a relative weight.
- The default role split is P 7%, D 18%, C 25%, A 50%, with a 5% soft target
  flexibility. These are editable profile rules.
- Advice values the best fixture-aware lineup and missing-vote coverage rather
  than summing the projections of every player in the roster.
- Goalkeepers from the same club form one capped coverage unit; goalkeepers
  from different clubs can add fallback coverage and fixture rotation value.
  Same-club probability follows explicit `PRIMO`, `SECONDO`, `TERZO`, and
  contested hierarchy groups before any unknown goalkeeper.
- Outfield coverage uses configured bench roles, substitution mode, and the
  global substitution cap. Projection deviation contributes only through a
  small probability-weighted upside term, so a zero-vote player has zero value.
- Only an explicit `confirmed_inactive` signal makes a player ineligible.
  `RISERVA`, `NON_CLASSIFICATO`, low probability, and missing editorial tiers
  reduce utility or confidence but are not hard exclusions.

## Auction market and roster construction

- The source FVM is converted into league credits by two identities, with no
  free parameter: the market places exactly `participants * roster_size`
  players and spends exactly `participants * starting_credits` doing it, at no
  less than the minimum bid each. Every player is therefore worth the minimum
  bid plus a share of the remainder proportional to his FVM. The players who
  set the scale are taken per role, because the roster fixes how many of each
  the market must buy. Recorded as `mercato.prezzo_atteso`.
- A per-role fit of expected points on `log(price)`, over the players the
  market will actually buy, gives the baseline `mercato.atteso_dal_prezzo`.
  The residual is `mercato.surplus`. Coefficients are fitted at run time and
  are exported under `meta.curva_mercato`.
- A player whose event rates were imputed from FVM sits on that curve by
  construction, so his surplus carries no information and he is flagged
  `mercato.informativo = false`.
- `mercato.fp_per_giornata` charges a player for the matchdays he misses and is
  the right unit for a listing. It is the wrong unit for a roster: the expected
  number of absences in an eleven is well below the substitution cap, so most
  missed votes are covered by the bench. `optimize.lineup_value` therefore
  values a slot as the starter when he is rated plus the probability-weighted
  bench behind him when he is not, capped at `bench_switch.max_substitutions`,
  with any uncovered absence scoring `incomplete_lineup.score`. Ignoring
  coverage understates a roster by roughly a seventh.
- The roster optimiser fills every slot at the minimum bid and then spends the
  remaining credits on upgrades, best gain per credit first. This follows from
  the fact that only the fielded eleven score: credits spent above the minimum
  on the fourteen covering slots buy very little.
- Unspent credits score nothing, so the objective is to maximise points within
  the budget. The target is a floor to report against, never a stopping rule.
  The one exception is the cost curve, whose question is what a target costs.
- A maximum bid is defined against the plan, not against a single swap: the
  highest price at which owning the player, and then spending the rest as well
  as possible, still beats the plan without him. Obtained by bisection, since
  the plan's value falls monotonically as the budget shrinks.
- Purchases by other managers consume the pool as well as the listing, and
  rarely at the same rate. Expected prices are re-derived from the credits and
  slots the league has left, so a room that has overspent early makes every
  remaining player cheaper.
- The target is always `virtual_goals.threshold + k * virtual_goals.step` and
  is never written as a literal.

## Simulation

- Monte Carlo uses a reproducible seed and 1,000 iterations by default.
- Bench composition and the maximum number of substitutions come from
  `bench_switch` in the active profile.
- `bench_switch.composition` selects how the bench is formed. `by_role`, the
  default and the upstream behaviour, fixes how many of each role sit on it.
  `any_role` seats exactly `bench_size` players whatever their role, which is
  what a league does when it names eleven and a bench of N. Under `any_role` the
  bench is a scarce resource the roles compete for — a fourth spare defender
  travels only if he is worth more as cover than a second spare forward — and
  the roster optimiser seats the spares with the highest probability-weighted
  value. The season simulator still reads the per-role bench; only the auction
  advice and the roster optimiser honour `any_role` so far.
- `Basic` and `Strict` replacements preserve the absent starter's role; `None`
  disables replacements. The configured formation remains unchanged.
- Sample rosters are ordered by probability-weighted projected contribution
  over the configured league horizon, with FVM used only as a tie-breaker.

These are model defaults, not assertions about future player performance.
