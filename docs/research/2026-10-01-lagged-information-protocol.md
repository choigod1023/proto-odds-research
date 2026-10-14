# Frozen lagged-information experiment (2026-10-01)

Commit this file alone BEFORE reading empirical outcomes/trial execution.
Pre-freeze inspection read only CSV headers (all 12 D1/SP1 files have HS, AS,
HST, AST and B365H/D/A), source code and literature. These outcome datasets were
already exposed in earlier work; this is exploratory retrospective research.
No production changes, no claim of a new untouched holdout or guaranteed profit.

## Additional information, not a new conceptual invention

Existing `src/features.py` already has form/rest; `src/soccer_process.py` and
`soccer_process2.py` already examined K-league shot/process signals; the
`accuracy_formula_lab` and prior tournament include market residual ideas.
Reuse local `local_score_model_tournament` unchanged for sources/guards/metrics/
policies. The incremental question is whether strictly lagged European shot
counts, schedule load and market-surprise history improve market-offset HDA
forecasts with train-only scaling and explicit feature ablations. This is not
just another Poisson or temperature setting and is not a first invention.

## Frozen data and temporal design

Only D1/SP1, season starts 2019..2024, source commit
`e82cf59161e82e0fbcdc49ad02819e45c821a5c8`. Extend the credited existing CSV
parser by identity-joining HS/AS/HST/AST; missing or invalid/nonnegative/nonfinite
stats become missing, never outcome-conditioned exclusions. Record source
hashes/coverage. Use actual priced fixtures for scoring/tickets, all actual
league/date groups for budget including missing-price-only cash days.

Fold A train2019–21 / inner2022 / outer2023; B train2019–22 / inner2023 /
outer2024. Parameter fitting excludes all results less than 7 days before first
evaluation. All scalers, missing-value medians and regression coefficients fit
on training only and refit once on train+inner. Team histories stay league-
specific. Online histories can incorporate evaluation results only once aged
7 days; coefficients never update within evaluation. Prior outer becomes next
inner as specified, so these are not two independent unseen replications.
No E0/I1/F1 confirmation in this sidecar: no new league is selected after results.

## Three information blocks and 8 fixed ablations

For each fixture, only rows dated at least 7 days before it may supply scores,
shot counts or market-surprise statistics. Take each team's last 8 such actual
matches, across seasons. Missing observations are ignored within that window;
an empty window yields missing, imputed from training medians (0 only if a
training column is entirely missing). No current-match HS/AS/HST/AST or result
enters its own feature vector. No future/team-season aggregate is consulted.

- shots: home and away rolling net shots and net shots-on-target, 4 columns.
- schedule: each team's rest days capped at 30, and number of previous matches
  in strictly past 7 and 14 days, 6 columns. This block uses dates/team names
  alone, not results or stats; strictly past fixtures need not wait 7 days.
  With no previous fixture rest is missing, counts 0. These are observed
  historical dates, not timestamp-verified original schedules; domestic-league
  only, omitting cups/Europe/internationals. No same-day schedule update.
- surprise: home and away last-8 mean (actual win/draw/loss score 1/.5/0 minus
  historical Shin expected score p(win)+.5*p(draw)), 2 columns. Missing previous
  prices omit that observation. No current or future result is used.

Families: bias_only control, shots, schedule, surprise, all, no_shots,
no_schedule, no_surprise. Each uses per-league market-offset multinomial model
softmax(log(Shin)+X*B), with intercept and standardized selected features plus
their missing indicators. Fit medians, means and scales on train only; zero
variance scale becomes 1. Ridge penalizes ALL coefficients including intercept.
Use mean negative log likelihood + lambda/2 * sum(B^2), fixed lambda in
{.1,1,10}. Analytic gradient, L-BFGS-B maxiter500 ftol1e-10; nonconvergence is
reported, not silently accepted. No unpenalized class bias confound. Bias-only
control distinguishes added information from general recalibration.

8 families x 3 lambdas + pure Shin = 25 candidate IDs per fold, 50 inner trials
total. Pick by inner pooled log loss with stable ID ties, globally and per
family. Refit selected family once per league on train+inner; reuse for pooled
selection. Keep every family outer result, not just positive ones. No outer
winner selection, added feature/ridge search, max-ROI selection or policy tuning.

## Budgets, uncertainty, provenance

Reuse fixed highestprob (no EV floor), p60range (p>=.6,1.5<=odds<2.2), p60low
(p>=.6,odds<2.2), ev02/ev05 (per-leg EV floors .02/.05, rank ticket EV). All HDA
including odds<1.5 where eligible; one unit budget per league/date, at most one
two-distinct-game ticket, else cash. Raw odds product, not actual Proto execution.
Report all trial scores, HDA log loss/Brier/accuracy point estimates, ticket
outcomes/hit/ROI/coverage/budget return and audit ledgers. Paired ISO-week 5000
bootstrap draws, seed20261001, 95% percentile CIs for log loss and betting
metrics versus Shin and bias-only control. Undefined zero-stake ROI/hit remains
undefined with valid-replicate counts. These CIs do not correct data exposure,
model selection or multiplicity. Report feature missingness and prediction
feature hashes/values, source and LF-normalized code/protocol hashes (POSIX keys).
Enforce explicit frozen protocol commit before data reads; Git unit tests mock
the guard, artifact tests verify actual hashes without requiring old Git history.

## Required synthetic tests and references

Analytic gradients, normalization, zero coefficients=Shin; current/future stat
and result mutations leave current/past features unchanged; <7-day stats blocked
and exactly7-day allowed; schedule features read dates only; train-only scaling
and missing priors; league isolation; fold exclusion; two distinct-game budgets,
cash for missing-price-only days; end-to-end ledger and provenance tests.

Primary rationale: [Wheatcroft, Forecasting football matches by predicting match
statistics](https://arxiv.org/abs/2001.09097) explicitly distinguishes unknown
current-match statistics from pre-match predicted statistics. We test lagged
rolling summaries, not its GAP algorithm or its claimed historical profits.
[Dellal et al., congested fixture periods](https://doi.org/10.1136/bjsports-2012-091290)
is a reason to test schedule load, not proof of odds-adjusted predictive value.
Neither conceptual novelty nor profitable transfer follows from these sources.
