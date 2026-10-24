# Edge diagnosis and constrained selection — frozen protocol

Offline exploration, archived primary markets only. Same previously reused dataset; no independent final holdout or live policy replay. No invented historical lineups. No production promotion.

1. Test 2025 and partial 2026 separately. Probability model trained before preceding year predicts preceding year out of sample. First half of preceding year fits temperature + class intercept calibration, second half selects raw/calibrated by Brier. Refit probability on all pretest data and selected calibrator on entire preceding-year OOS predictions. Disclose refit distribution shift.
2. Diagnose all-outcome probability bins and odds bins, model-minus-market direction, selected top outcomes, margin over break-even, Brier differences. All-outcome options are dependent within events; descriptive bins are not independent tests.
3. Candidate pair score sum(log(p)+lambda*log(odds)), lambda in [0,.1,.25,.5,1]. Select on preceding-year second-half actual ticket ROI, subject to pair hit rate >= probability-only minus allowance [0,.01,.02]. Tie prefer smaller lambda. Test fixed next year. Same event/shared team excluded. No price ceiling.
4. Compare same-day one-ticket policies: market, raw probability, calibrated/selected probability, constrained0/1/2 percentage-point policies. Match dates. Compare selected legs as two half-stake singles versus their parlay. Pair probability multiplication is an assumption.
5. Monthly total loss reserve KRW50,000, KRW10,000/ticket, weekly2/3; pooled cap shared. Next-day settlement proxy. Forced schedules and selected-ticket positive joint EV veto (not exhaustive search among profitable pairs) separately.
6. Paired 7-observed-date block bootstrap 10,000 for three constrained policies against probability policy, pair-hit and ROI; Holm over 5 groups*3*2=30. Test effects do not change selection or thresholds. Report yearly reversal, worst month, zero bet count and negative results.
