# KBO official batting order counterfactual (phase 4)

Status: ready_for_independent_review. This is an exploratory retrospective counterfactual, not a point-in-time verified pregame evaluation. The 2026 season had already been examined in earlier phases. The official orders are assumed available before the game; their historical publication and receipt times are not proven. This is not evidence of executable prices, actual wallet returns, or deployment readiness. Pitchers remain the prior-game projections, not confirmed starters.

## Design and provenance

The comparison uses the existing 2025-selected player model, with its coefficients, standardization and alpha fixed. No fitting or tuning was performed. The preregistered protocol is in `phase4-lineup-protocol.md` (commit `59f2dc80`). Predictions for the 193 matching official-lineup games were frozen before phase 4 outcome scoring in commit `516d5068`; `phase4/predictions.json` SHA-256 is `5c0a9d88287e0bf84747003386cecac0a81684b169ec08604776de3669679c17`. A replay after adding portable Git-blob/checkout verification reproduced this hash exactly. Scoring code was committed at `727c98e4` before producing results.

The upstream lineup overlay is the exact Git blob at data-worker commit `a28cf0d19003bc436d174bb85af46be86ee1ee24`, SHA-256 `ddc66e073ada98ef9cbf469fac64aed53e45ac3d4e44a1e4330f2c661eced627`; the pre-existing projection and the comparison contract were also checked against their exact committed blobs and hashes. Team, game and player identities, nine-player order, and historical source dates were checked. Only the two lineup-strength inputs changed; all other model inputs remained identical. The original predictions replayed with maximum error 0.0. Historical batting data was missing for 10 of 3,474 player appearances; no aggregate lineup feature was null. This is a selective 193-game subset of 726 season games; 533 games were not processed and should not be described as missing lineups. Selection and availability can bias the comparison.

## Results

For the same 193 games, with three-way outcome scoring (home/draw/away):

| Model | Correct | Accuracy | Log loss | Brier score |
| --- | ---: | ---: | ---: | ---: |
| Original player | 108 | 55.96% | 0.844016 | 0.526511 |
| Official-order counterfactual | 112 | 58.03% | 0.843335 | 0.525929 |
| Frozen team | 110 | 56.99% | 0.843530 | 0.526008 |

The counterfactual changed four picks, all four improving the observed three-way result. Mean absolute probability change per class was 0.00117 for three-way and 0.00191 for conditional two-way probabilities. The 5,000-replicate paired weekly bootstrap, seed 20261103, gives counterfactual-minus-original accuracy difference 95% interval +0.45 to +3.98 percentage points, log-loss difference −0.00107 to −0.00038, and Brier difference −0.00092 to −0.00032. These intervals reflect only eight observed weeks, a small, selected retrospective sample, and do not account for earlier model selection or unknown lineup availability; they do not establish prospective benefit.

Both the strict time-matched and extended date/team price intersections contain the same 166 games, March 28–May 14, 2026: 162 original archived prices and four restored official prices, with no date-only restoration. In this subset the original player forecast had 92 wins and the counterfactual 95; four draws were refunded in both. A one-unit bet on every game gives original −15.53 units (−9.36% ROI) and counterfactual −10.28 units (−6.19% ROI). The market and frozen-team comparators returned −22.25 (−13.40%) and −15.77 units (−9.50%), respectively. ROI divides by all 166 staked units, including refunded draws. Three picks changed; the paired seven-week bootstrap interval for ROI improvement is 0 to +7.15 percentage points. Archived observations do not prove a price was available or executable before the game, and refunds follow the archived two-way settlement assumption.

## Verification and artifacts

`phase4_score.py` checks the frozen prediction hash and committed blob before reading outcome labels, verifies outcome uniqueness and original phase 2/3 source hashes, and writes a new result without overwriting an existing file. `phase4/results.json` includes per-game scoring and price ledgers. Existing model, phase 2 and phase 3 artifacts were preserved. Source integrity and leakage checks include strict earlier-date history checks and restrictions on player fields; the frozen model replay also prevents fitting and minimization. A separate Python calculation independently recomputed all three-way metrics and each price-cohort settlement, including draw refunds. The targeted model, price and phase 4 suite passed 29 tests. All outputs are research artifacts; no live system was changed.
