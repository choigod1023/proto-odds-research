# Frozen incremental DC/recency ablation (2026-10-01)

Commit this file alone before empirical data loading/trial execution. This is
exploratory retrospective research on previously exposed D1/SP1 data, not a
new holdout. No production code, model settings, records or betting are changed.

## What is new, and what already exists

`src/score_dist.py` already supplies `dixon_coles_tau`; rho defaults to zero and
the production default is independent Poisson. Reuse that function unchanged.
PR258's `local_score_model_tournament.py` already supplies attack/defense
independent Poisson, source loading, folds, Shin and policy/ledger/bootstrap
helpers. Reuse its unchanged local dependency. New work is joint constrained
rho estimation and normalized exponential recency likelihood, with a 2x2
ablation. It is not a newly invented DC formula or a fresh empirical replication.

## Data, folds and safeguards

Same pinned footballdataset commit `e82cf59161e82e0fbcdc49ad02819e45c821a5c8`,
D1/SP1 season starts 2019..2024, same loader and validation. Keep actual matches
with missing prices for fitting; priced matches for prediction metrics/picks;
every actual league/date retains budget 1, even if all prices are missing.
Record each source URL/hash, code/dependency hashes and protocol commit/hash.
Verify this frozen protocol against its explicit commit before source loading.

Fold A train 2019–21 / inner 2022 / outer 2023; Fold B train 2019–22 / inner
2023 / outer 2024. Train/refit matches must be at least 7 days before the first
evaluation date. No same-day results or future team/result statistics. Teams
and league priors come only from training. Unseen teams use zero attack/defense
effects. Static parameters remain fixed throughout an evaluation season.
Outer A is later inner B; do not call these independent untouched replications.

## Fixed six base configurations, 60 inner trials

- independent Poisson: rho=0, no decay;
- learned DC: joint learned rho, no decay;
- recency-only: rho=0, half-life 180 or 365 days;
- DC+recency: learned rho, half-life 180 or 365 days.

All use fixed ridge=10 (a prior exposed research choice, not a new tuning grid),
per-league log-rate intercept, home effect, zero-centered team attack/defense.
Team log-effects bounded [-2,2] before centering, intercept/home in [-3,3].
For age relative to the last training date, raw weight=2^(-age/half-life).
Normalize weights to sum to n training matches; minimize sum(weight*NLL) plus
10/2 times the squared centered team effects. This preserves ridge relative to
total likelihood across half-lives. Report weight sums/effective sample sizes.

DC uses exactly the existing four-cell tau. Jointly fit attack/defense, league
terms and rho with the weighted score likelihood including log(tau), not a
post-hoc draw multiplier. To ensure positivity beyond observed training scores,
derive conservative maximum home/away rates from all training team effects
(and neutral unseen-team effects) at every optimizer step. Parameterize rho
using tanh(z), z in [-4,4], with positive magnitude at most
min(.3,(1-1e-6)/(max_home*max_away)), and negative magnitude at most
min(.3,(1-1e-6)/max(max_home,max_away)). This guarantees every low-score tau is
positive for every possible prediction pairing supported by those effects.
The bound is conservative and may constrain rho more than an observed-pair-only
fit; record fitted rho, feasible range, optimizer diagnostics and minimum tau.
Use analytic gradients including the bound's parameter dependence. At ties,
use a deterministic subgradient. L-BFGS-B maxiter 500, ftol 1e-10. Nonconvergence
is a recorded failure, never silently accepted or retuned using outer outcomes.

Grid truncation is adaptive: use score support at least 0..40 and enough for
each Poisson tail to be <1e-12, normalize HDA after verifying positive tau and
finite probabilities. This does not use outcomes or change rates at prediction.

Each base configuration blends with Shin using fixed market weights
{0,.25,.5,.75,1}. Six x five x two inner folds = 60 candidate evaluations,
including duplicate market-only IDs. Select by inner pooled HDA log loss with
ID tie-break only, both globally and separately within each ablation family.
Refit selected base configurations once per league on train+inner for outer;
reuse shared refits. Also report each selected base configuration unblended on
outer to expose component effects even when inner selects market weight 1.
These unblended diagnostic rows are not additional selection or ROI tuning.
No outer-based winner, policy or hyperparameter selection.

## Evaluation and simulated budgets

Reuse the previous five fixed policies: highestprob (no EV/odds floor),
p60range (p>=.60, 1.5<=odds<2.2), p60low (p>=.60, odds<2.2), ev02/ev05
(per-leg EV>=.02/.05, rank pairs by EV). All H/D/A outcomes including <1.5
remain eligible where the policy allows. One budget per league/date, at most
one two-distinct-match ticket, otherwise cash. Raw quoted odds product; no
claims of actual Proto execution, taxes, voids or contemporaneous odds.
p60range is a historical comparator, not exact current production policy.

Report HDA log loss/Brier/accuracy and outcomes, ticket wins/hit rate, ROI,
coverage and budget return with daily audit ledgers and per-match forecasts.
Use seed 20261001, 5000 ISO-week paired bootstrap replicates, 95% percentiles.
Report differences against Shin and the independent-Poisson ablation; include
unblended DC minus unblended independent Poisson for incremental interpretation.
Pair identical week samples, retain both leagues in a week. Undefined zero-
stake ROI/hit remains undefined with valid-replicate counts. CIs are descriptive,
not multiplicity/selection/exposure corrected and not a profit guarantee.

## Tests and primary reference

Test analytic gradient against finite differences; rho=0 reduces to independent
Poisson; all tau positive over extreme/unknown pairings; score mass and HDA
normalization; normalized decay preserves total likelihood/ridge scale; no
future or <7-day fit data; outer outcome mutation leaves predictions unchanged;
missing-price cash budgets and distinct-game ledger reconciliation; protocol
enforcement and source/code hashes. Run locally before empirical trials.

Primary source: Dixon & Coles (1997), [Modelling Association Football Scores and
Inefficiencies in the Football Betting Market](https://rss.onlinelibrary.wiley.com/doi/abs/10.1111/1467-9876.00065),
JRSS C 46(2), 265–280. Publisher abstract verifies Poisson regression and
maximum-likelihood modeling; its historical profitability does not establish
profitability of this implementation. Our ridge, conservative rho constraints,
fixed decay grid and probability-level market blend are explicit departures
from an exact original-paper replication.
