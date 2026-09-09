# Fixed recommendation hit-rate experiment — 2026-09-09

Objective: actual settled recommendation/game hit rate. PA accuracy, shot error and Brier are not substitutes.

Result: the fixed candidate failed to improve recommendation hit rate. Primary market 21/42 (50.00%) versus candidate 20/42 (47.62%), a loss of 2.38 percentage points at identical 12.73% coverage. The sole changed recommendation lost where the market pick won. Full production-budget membership was identical: both 24/47 (51.06%), 14.24% coverage. This provides no improvement evidence.

The hypothesis, selectors and first nine tests were committed as `916db4b7` before candidate scoring. The first invocation stopped before selection/scoring because the older outcome cache omitted `training_mode`. Compatibility was added only for its exact producer SHA-256, verified against the frozen-training implementation at `ac488416`; the candidate was not changed. One completed candidate evaluation followed. Ten final synthetic/integration tests pass. No best-of-test selection was performed.

Exploratory only: the K1 2025–2026 evaluation was reused by earlier experiments; there is no independent confirmation or production authorization.

## Frozen protocol

One new candidate uses the cached `outcome` process probabilities (outcome fit frozen through 2024). No models were rebuilt, no evaluation labels selected weights, thresholds, budgets or cache variants. The earlier rolling and favorite caches are read only for provenance and complete prior-result disclosure.

Both arms use the real frontend market-favorite eligibility, market probability >=55%, and the same preferred-odds daily tier. The candidate ranks model/market direction agreement first, then min(market, process), then market probability, kickoff and stable ID. This minimum is a ranking score, not a calibrated probability or statistical lower bound.

Primary: fixed one recommendation per eligible KST league/day in each arm. This deliberately changes the production volume; comparison against market uses that SAME one-pick budget. Secondary: preserve the full production count and mandatory market >=60% picks, rerank only remaining slots. No outcome-dependent count matching is used. Labels are attached only after both selectors finish.

Training: 397 games through 2024-11-24T14:00:00+09:00. Evaluation: 2025-02-15T13:00:00+09:00 to 2026-07-26T19:30:00+09:00. Eligible: 47 games across 42 league/days.

## Actual results

### primary

- all/all: market 21/42 (50.00%); candidate 20/42 (47.62%); delta -2.38 pp; descriptive 95% date-bootstrap [-7.14, +0.00] pp. Both coverage 12.73% of 330 games; mean odds 1.528/1.530; changed picks 1.
- K리그1/all: market 21/42 (50.00%); candidate 20/42 (47.62%); delta -2.38 pp; descriptive 95% date-bootstrap [-7.14, +0.00] pp. Both coverage 12.73% of 330 games; mean odds 1.528/1.530; changed picks 1.
- K리그1/2025: market 11/25 (44.00%); candidate 11/25 (44.00%); delta +0.00 pp; descriptive 95% date-bootstrap [+0.00, +0.00] pp. Both coverage 11.26% of 222 games; mean odds 1.552/1.552; changed picks 0.
- K리그1/2026: market 10/17 (58.82%); candidate 9/17 (52.94%); delta -5.88 pp; descriptive 95% date-bootstrap [-17.65, +0.00] pp. Both coverage 15.74% of 108 games; mean odds 1.494/1.496; changed picks 1.

### production_budget

- all/all: market 24/47 (51.06%); candidate 24/47 (51.06%); delta +0.00 pp; descriptive 95% date-bootstrap [+0.00, +0.00] pp. Both coverage 14.24% of 330 games; mean odds 1.534/1.534; changed picks 0.
- K리그1/all: market 24/47 (51.06%); candidate 24/47 (51.06%); delta +0.00 pp; descriptive 95% date-bootstrap [+0.00, +0.00] pp. Both coverage 14.24% of 330 games; mean odds 1.534/1.534; changed picks 0.
- K리그1/2025: market 13/28 (46.43%); candidate 13/28 (46.43%); delta +0.00 pp; descriptive 95% date-bootstrap [+0.00, +0.00] pp. Both coverage 12.61% of 222 games; mean odds 1.554/1.554; changed picks 0.
- K리그1/2026: market 11/19 (57.89%); candidate 11/19 (57.89%); delta +0.00 pp; descriptive 95% date-bootstrap [+0.00, +0.00] pp. Both coverage 17.59% of 108 games; mean odds 1.504/1.504; changed picks 0.

## All prior proposals (not selected as new winners)

- outcome, K1/all: prior unmatched production replay market 24/47, process 23/58; these differing counts are not evidence of improvement.
- outcome, K1/2025: prior unmatched production replay market 13/28, process 15/39; these differing counts are not evidence of improvement.
- outcome, K1/2026: prior unmatched production replay market 11/19, process 8/19; these differing counts are not evidence of improvement.
- rolling-outcome, K1/all: prior unmatched production replay market 24/47, process 28/53; these differing counts are not evidence of improvement.
- rolling-outcome, K1/2025: prior unmatched production replay market 13/28, process 18/36; these differing counts are not evidence of improvement.
- rolling-outcome, K1/2026: prior unmatched production replay market 11/19, process 10/17; these differing counts are not evidence of improvement.
- favorite-outcome, K1/all: prior unmatched production replay market 24/47, process 26/50; these differing counts are not evidence of improvement.
- favorite-outcome, K1/2025: prior unmatched production replay market 13/28, process 16/36; these differing counts are not evidence of improvement.
- favorite-outcome, K1/2026: prior unmatched production replay market 11/19, process 10/14; these differing counts are not evidence of improvement.

## Limitations and decision

No promotion. Any observed increase is descriptive and must be confirmed on newly collected, timestamp-verified future recommendations. A zero change means this ranking did not change the actual selected games; a decrease is retained as a failed result. Only K1 winner markets exist in these caches; no other league or full-site generalization is supported.

Coverage denominator is all 330 cached evaluation games, not all scheduled K1 games or site markets. Equal per-day recommendation counts do not imply identical picks or identical odds; mean odds are disclosed. Bootstrap intervals are unadjusted for repeated exploration and do not repair data reuse. Chronological code checks cannot verify historical data availability.

Known archived odds capture timestamps: 0; actual publication timing of the reconstructed process inputs is unverified. Within-day slate ranking assumes pregame signals available for the full slate, which these archives cannot prove. Upstream process forecasts update from prior games with D-2 cutoff; frozen refers to the outcome mapping, not permanently frozen team histories.

All three source SQLite files were opened read-only and their SHA-256 hashes match before/after. Private per-pick outputs and full prior reports are stored only in ignored SQLite. No network data collection, production, scheduler, deployment or merge changes.

## Reproduce

```powershell
python src/hit_rate_policy_experiment.py --cache-root ../dynamic-count-experiment-20260909/outputs/dynamic-count --output outputs/hit-rate-policy/result.sqlite3 --report experiments/hit-rate-policy-20260909.md
python -m unittest discover -s tests -p test_hit_rate_policy_experiment.py -v
```

Choose unused output/report paths when rerunning. Reruns are not independent experiments.

Source hashes:

- outcome: `8194cce1799af3035912e29c502b5643454f21bce165001ccaab16626df90424`
- rolling-outcome: `f515c4cfeebbdaef23719d73c5a2d68c60fdaa4d3560f859be59de1d0c23401e`
- favorite-outcome: `1361127a959f22ff6d1ae68d99fa81e5d0726cc9c8c36bf1b2f1d6000528e9d3`
