# Score-model tournament: frozen exploratory protocol

Freeze this file in its own commit before loading data or running trials.
Date: 2026-10-01. These datasets have already been exposed in earlier research:
this is exploratory retrospective comparison, NOT a new untouched holdout.
No production data, recommendations, configuration, or betting are changed.

## Data and nesting

Use only D1 and SP1 season-start years 2019 through 2024 (2019/20 through
2024/25), pinned to footballdataset commit
`e82cf59161e82e0fbcdc49ad02819e45c821a5c8`. Loader is a credited adaptation
of PR256's `evaluate_draw_calibration_holdout.py`, adding team names and goals.
No runtime dependency on another worktree. Retain actual matches with missing
B365H/D/A prices for rating history; exclude missing-price matches from scoring
and wagering. Validate full results, unique identities and complete seasons.
Record source URLs, byte hashes and exclusions. Markets use Shin probabilities.

Fold A: train season-start 2019–2021, inner 2022, outer 2023.
Fold B: train 2019–2022, inner 2023, outer 2024.
Choose a candidate by pooled inner HDA log loss only (stable candidate ID breaks
ties); refit that selected configuration once using train+inner before outer.
Also report the inner-selected candidate within each family without choosing
between families using outer results. Reuse its refit if it is the pooled winner.
Outer 2023 is later Fold B's inner data as explicitly prescribed; outer folds
are not independent unseen replications. Never select a winner on outer ROI.

All result-derived online features consume only actual matches dated at least
7 calendar days before the prediction date. Batch same-day rows before updates.
Season-start values are not calendar-year cutoffs. Static training fits end at
least 7 days before the earliest inner/outer evaluation date. Team IDs include
league. Unseen teams receive training-derived neutral priors, never future team
or outcome statistics. Elo state can update during inner/outer using the same
7-day rule; regression/Poisson parameters remain frozen within each evaluation.

## Fixed candidate budget and model departures

1. Elo + L2 multinomial logistic HDA: K in {20,40}, ridge in {1,10}.
   Neutral initial rating 0, fixed Elo home bonus 50, denominator 400; win/draw/
   loss scores 1/.5/0, symmetric updates. Per-league logistic features are
   intercept, home-away rating difference /400, and its absolute value. Ridge
   penalizes slopes, not intercepts. Unseen-team Elo starts at neutral 0; the
   outcome-frequency intercept is learned solely on that fold's training rows.
2. Per-league attack/defense independent Poisson MLE: ridge in {1,10}; log mean
   = league intercept + home advantage (home goals only) + attack - defense.
   Team effects have zero-sum identification and L2 shrinkage. Unseen effects
   are zero, giving fitted training league priors. No online parameter refits.
   Integrate scores 0..40 then normalize HDA; report optimizer failures rather
   than silently inventing a fit. Means bounded numerically for safe evaluation.
3. For all six base configurations blend (1-w)*model + w*Shin, w in
   {0,.25,.5,.75,1}: 30 candidate IDs per inner fold, 60 inner trials total.
   w=1 duplicates are deliberately counted. Standalone Shin is the comparator.

This is a modest bounded extension, not an exhaustive model search. SciPy
optimization avoids a new scikit-learn dependency. Dixon–Coles joint constrained
likelihood is not included in the frozen grid: a post-hoc draw multiplier is
not an equivalent substitute. Independent Poisson assumes conditional score
independence, lacks low-score dependence correction and time decay, and can
misestimate sparse 0–0/1–0/0–1/1–1 cells. No claim of reproducing full
Dixon–Coles or Hvattum–Arntzen is made; the latter motivates Elo features, not
the exact fixed feature/regularization grid used here. A full constrained DC
implementation is deferred rather than selected after inspecting these trials.
Optimizer settings: L-BFGS-B maxiter 500, ftol 1e-10; fail explicitly if not
converged. No model/candidate added in response to outer results.

## Fixed secondary simulated policies

Consider all H/D/A outcomes, including decimal odds below 1.5. Each league/date
has budget 1; place at most one two-leg ticket from two distinct matches, stake
1, otherwise retain cash. No singles, cross-league/date pairs, or duplicate
match legs. Eligibility applies to each leg:

- highest probability: no odds range or EV floor; rank pairs by product p.
- p60range: p>=.60, 1.5<=odds<=2.2; rank product p.
- p60low: p>=.60, odds<2.2 (including <1.5); rank product p.
- ev02: p*odds-1>=.02; rank pair EV = product(p*odds)-1.
- ev05: p*odds-1>=.05; rank the same pair EV.

Deterministic ties use sorted match IDs/outcome indices. Returns use raw product
of quoted decimal odds, no rounding, taxes, commission, voids or price changes.
Report stake ROI, ticket hit rate, budget return, days/tickets/coverage, and
match-level log loss, Brier and accuracy. A losing ticket returns -1, winning
ticket product odds -1, cash 0. Include auditable daily ledgers and forecast
records in results JSON. This is not executable Korean Proto performance;
archived B365 odds do not establish decision-time availability or independence
between legs, and monetary profit is not guaranteed.

## Uncertainty and audit

Seed 20261001, 5000 weekly paired cluster bootstrap draws, percentile 95% CIs.
Calendar ISO-week clusters include both leagues; sample same weeks for model
and Shin. Report CIs for model metrics and model-minus-Shin differences in
log loss, ROI, ticket hit, coverage and budget return. Resampled zero-stake
replicates yield undefined ROI/hit, excluded with counts. Treat CIs as
descriptive, without multiplicity or selection-bias correction. Record all
60 candidate losses, selected IDs, fit counts, failures, runtime, versions,
protocol commit/hash and source hashes. No outer-derived winner proclamation.

Tests: normalization/positivity, future and <7-day result mutations leave earlier
features unchanged, exactly-seven-day history is permitted, training-only
unseen team priors, distinct-game/day budgets, empty cash days, return ledger
reconciliation, and deterministic paired bootstrap. Run locally only.

Primary literature:
- Dixon & Coles (1997), [Modelling Association Football Scores and Inefficiencies
  in the Football Betting Market](https://rss.onlinelibrary.wiley.com/doi/abs/10.1111/1467-9876.00065).
- Hvattum & Arntzen (2010), [Using ELO ratings for match result prediction in
  association football](https://www.sciencedirect.com/science/article/pii/S0169207009001708).
  Publisher access may be restricted; do not infer details beyond verified text.
