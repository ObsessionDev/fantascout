# Model Assumptions

The application is intentionally a Classic Fantacalcio advisor for Serie A.
The Serie A source calendar is validated as 20 teams, 38 matchdays and 380
matches. The fantasy league may select a shorter configured interval within
that season.

## Player projections

The projection engine is `advisor.engine`; `docs/MOTORE.md` gives the
measurements behind every choice below. It predicts, per player and matchday,
the probability of a vote, the mean pure vote and the expected count of each
scoring event per rated appearance.

- **Inputs known before a season starts.** Season totals of every earlier
  season in `data/raw`, and the current list with its opening price `Qt.I`.
  The historical lists were saved at the end of their season, so `Qt.A` and
  `FVM` are never used: they would carry the outcome.
- **Shrunk history.** Past seasons are pooled with a discount of 0.45 per
  season of age, over at most six seasons, and pulled towards the role mean
  with the weight of 12 rated appearances (vote), 20 (events) or one season
  (presence). This is the empirical-Bayes posterior mean of a normal,
  gamma-Poisson and beta-binomial model. Event rates are per rated
  appearance, the unit in which the fantasy vote counts them.
- **Calibration, learned.** Per role, a logistic model (presence, on matchdays
  with a vote out of 38), weighted least squares (vote, weighted by
  appearances) and a Poisson model with exposure (each event) take the shrunk
  history, the amount of history, a no-history flag, `log(Qt.I)` and its
  interaction with the flag, the club's goals scored and conceded per
  matchday last season, a promoted-club flag, and last season's presence on
  its own. Ridge penalty 10 on standardised features. Coefficients are fitted
  at run time on every season that precedes the target, never hard-coded.
- **Cold start.** A player with no rated history gets his projection from the
  same models, which learned from past newcomers what a given opening price in
  a given club leads to. A promoted club stands at the mean of past promoted
  clubs in their first season. Such players carry `fonte_rate =
  "avvio_freddo"` and `mercato.informativo = false`: their numbers follow the
  price by construction.
- **Uncertainty, measured.** From the residuals, per role, the method of
  moments fits `E[r^2] = tau2 + sigma2 / n`: `tau2` is how far a true season
  level strays from the prediction, `sigma2` the noise of one matchday. The
  presence concentration comes from the beta-binomial overdispersion. They are
  exported under `proiezione.incertezza`; `sd_giornata_voto` is also the
  matchday vote deviation. On unseen seasons the 80% interval of the season
  fantasy mean covers 84%, the 50% interval 54%, and the 80% interval of
  appearances 80%.
- **The season in progress** (`advisor.inseason`). The pre-season projection is
  the prior and the matchdays played are data, combined conjugately: presence
  `(p·kappa + votes) / (kappa + matchdays)`, vote
  `(k·mean + votes·observed) / (k + votes)` with `k = sigma2 / tau2`, events
  `(alpha + count) / (beta + votes)` with the gamma prior of the measured
  variance. The trials of a player are the matchdays his club played; absence
  from the data is a matchday without a vote. Recency weighting within the
  season (`half_life`) exists and is off until matchday data can measure it.
- **Scoring.** The bonus is the event rates priced by the profile, penalties
  saved and missed included. A clean sheet, when the profile scores it, has
  probability `exp(-goals conceded per appearance)`: modelled, not measurable
  from season totals.
- **Kept from before, not measurable** (no history exists): a player marked
  `TITOLARE`, `BALLOTTAGGIO` or `RISERVA` has a current availability prior of
  85%, 55% or 15%, blended 65/35 with the projection; the primary penalty
  taker gets 0.12 extra goals per appearance; European clubs take a rotation
  discount on outfield presence; fixture projections vary by opponent and
  venue around the player's seasonal mean.
- **Measured and rejected:** a changed-club flag, a price by history
  interaction, and gradient-boosted trees on the same features (worse on every
  metric). The previous fixed 60/30/10 weights and the per-90 normalisation
  of rates, which inflated bonus and malus by 20%, are gone.
- **Fallback.** With fewer than three historical seasons that have both a list
  and statistics, or with `ModelConfig.engine = "legacy"`, the pipeline keeps
  the previous projection: fixed weights over the declared history, rates
  normalised to 75 minutes, and the FVM regression for players without
  history.

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
- Each iteration draws once per player a season level for the vote, one for
  the bonus and a share of matchdays played, from `proiezione.incertezza`, so
  good and bad seasons of a player hang together as they do in reality. The
  lineup is picked knowing that share. Players without the measured spread
  draw nothing and keep the previous behaviour.
- Penalties saved and missed, and the clean sheet, enter the simulated fantasy
  vote with the profile's values.
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
