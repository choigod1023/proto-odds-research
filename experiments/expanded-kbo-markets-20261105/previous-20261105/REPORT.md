# KBO M0 retrospective market comparison

This is a retrospective replay, not pregame receipt, live availability or an execution-price record. The M0 model trained on 2023–2025; 2025 is not held out. 2026 outcomes had already been seen; this is not a blind test. No adoption, deployment or profit claim follows.

## Rule and provenance

All 709 frozen forecasts are audited. A score means forecast is compared with the same official full-game product, without requiring the unrelated general-winner price to match the old replay. Forecast IDs must be unique physical KBO IDs for date/home/away; unidentified score-based IDs are excluded, as are ambiguous keys. Old archive products use handicap types 9/2; newer archives use betId 79/7, with full-game winner betId 2. First-half and other products are excluded. Official general winner scores may come from a different verified round for the same unambiguous fixture; conflicting completed scores exclude the fixture.

Before inspecting outcomes, choose the lowest numeric round then matchSeq among full-game product rows for each fixture and market. Validate that chosen row only; never fall back to a later product on failure. Require a completed official original score, half-line, two prices above 1, completed non-rejected product, matching official product score and grade; model or market ties are excluded. A drawn original game does not refund a half-line handicap. M0 selects the more probable side using independent Poisson score rates; market selects the lower price. One unit per fixture; ROI includes all selected units. Drawdown uses cumulative unit profit, ordered by date, fixture ID, round, matchSeq; same-day ordering is arbitrary and not execution timing. No product/round was selected by profitability.

Sources, SHA-256 hashes, all per-fixture exclusions, counts, monthly splits and each canonical row are in audit.json and canonical.json. Raw source files are external read-only dependencies; missing files or hash mismatches fail execution. Run `python scripts/validate_m0_handicap.py --output experiments/expanded-kbo-markets-20261105/old44-handicap` before `python experiments/expanded-kbo-markets-20261105/analyze.py --manifest PATH`, then `python experiments/expanded-kbo-markets-20261105/write_report.py`. Requires scipy and the source paths recorded in audit.json.

## Exclusion audit

```json
{
  "forecast_without_official_id": 239,
  "forecast_official_id": 470,
  "handicap_raw_rows": 854,
  "total_raw_rows": 854,
  "handicap_no_forecast": 458,
  "total_no_forecast": 458,
  "handicap_fixture_candidates": 340,
  "handicap_noncanonical_products": 56,
  "handicap_eligible": 274,
  "total_fixture_candidates": 340,
  "total_noncanonical_products": 56,
  "total_eligible": 264,
  "total_excluded_market_tie": 10,
  "handicap_excluded_line": 56,
  "total_excluded_line": 56,
  "handicap_excluded_winner_missing": 10,
  "total_excluded_winner_missing": 10,
  "total_forecast_fixtures_without_candidate": 369,
  "handicap_forecast_fixtures_without_candidate": 369
}
```

Counts labelled no candidate include unidentified forecasts and must not be added to the separate missing-ID count. Raw row counts and fixture counts have different denominators.

## Results: entire observed 2026 retrospective split and months

| Cohort | Month | N | Model hits / rate | Model net / ROI / max drawdown | Market hits / rate | Market net / ROI / max drawdown | Disagreements; model vs market hits |
|---|---|---:|---|---|---|---|---|
| canonical total | all | 264 | 126 / 47.73% | -46.39 / -17.57% / 47.76 | 137 / 51.89% | -35.60 / -13.48% / 43.42 | 115; 52 vs 63 |
| canonical total | 2026-03 | 15 | 8 / 53.33% | -1.40 / -9.33% / 4.86 | 11 / 73.33% | 3.39 / 22.60% / 1.35 | 7; 2 vs 5 |
| canonical total | 2026-04 | 69 | 32 / 46.38% | -13.76 / -19.94% / 14.08 | 28 / 40.58% | -23.08 / -33.45% / 23.40 | 28; 16 vs 12 |
| canonical total | 2026-05 | 82 | 38 / 46.34% | -16.48 / -20.10% / 18.21 | 42 / 51.22% | -12.47 / -15.21% / 20.06 | 36; 16 vs 20 |
| canonical total | 2026-06 | 38 | 19 / 50.00% | -4.71 / -12.39% / 7.06 | 21 / 55.26% | -2.31 / -6.08% / 8.49 | 18; 8 vs 10 |
| canonical total | 2026-07 | 60 | 29 / 48.33% | -10.04 / -16.73% / 12.99 | 35 / 58.33% | -1.13 / -1.88% / 10.88 | 26; 10 vs 16 |
| canonical handicap | all | 274 | 174 / 63.50% | -38.79 / -14.16% / 41.13 | 174 / 63.50% | -38.79 / -14.16% / 41.13 | 0; 0 vs 0 |
| canonical handicap | 2026-03 | 15 | 11 / 73.33% | -0.50 / -3.33% / 2.24 | 11 / 73.33% | -0.50 / -3.33% / 2.24 | 0; 0 vs 0 |
| canonical handicap | 2026-04 | 70 | 46 / 65.71% | -8.64 / -12.34% / 11.90 | 46 / 65.71% | -8.64 / -12.34% / 11.90 | 0; 0 vs 0 |
| canonical handicap | 2026-05 | 88 | 55 / 62.50% | -14.32 / -16.27% / 15.32 | 55 / 62.50% | -14.32 / -16.27% / 15.32 | 0; 0 vs 0 |
| canonical handicap | 2026-06 | 40 | 28 / 70.00% | -2.10 / -5.25% / 5.22 | 28 / 70.00% | -2.10 / -5.25% / 5.22 | 0; 0 vs 0 |
| canonical handicap | 2026-07 | 61 | 34 / 55.74% | -13.23 / -21.69% / 13.83 | 34 / 55.74% | -13.23 / -21.69% / 13.83 | 0; 0 vs 0 |
| old44 total | all | 44 | 23 / 52.27% | -4.63 / -10.52% / 7.35 | 25 / 56.82% | -2.70 / -6.14% / 8.00 | 22; 10 vs 12 |
| old44 total | 2026-03 | 15 | 8 / 53.33% | -1.40 / -9.33% / 4.86 | 11 / 73.33% | 3.39 / 22.60% / 1.35 | 7; 2 vs 5 |
| old44 total | 2026-04 | 29 | 15 / 51.72% | -3.23 / -11.14% / 5.20 | 14 / 48.28% | -6.09 / -21.00% / 7.62 | 15; 8 vs 7 |
| old44 handicap | all | 44 | 29 / 65.91% | -5.83 / -13.25% / 7.57 | 29 / 65.91% | -5.83 / -13.25% / 7.57 | 0; 0 vs 0 |
| old44 handicap | 2026-03 | 15 | 11 / 73.33% | -0.50 / -3.33% / 2.24 | 11 / 73.33% | -0.50 / -3.33% / 2.24 | 0; 0 vs 0 |
| old44 handicap | 2026-04 | 29 | 18 / 62.07% | -5.33 / -18.38% / 5.33 | 18 / 62.07% | -5.33 / -18.38% / 5.33 | 0; 0 vs 0 |
