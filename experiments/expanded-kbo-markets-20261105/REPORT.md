# KBO M0 retrospective market comparison

This is a retrospective replay, not pregame receipt, live availability or execution-price evidence. M0 was trained on 2023–2025; 2025 is not held out. The 2026 outcomes had already been seen and this is not a blind test. No adoption or future profit claim follows.

## Cohorts and validation

The primary strict cohort requires both modern product identifiers: full-game total betId 79 / betTypId 7, or full-game two-way half handicap betId 7 / betTypId 5. The original unadjusted score comes from a full-game modern general winner betId 2 / betTypId 2. The sensitivity cohort also allows legacy handi-only products and winner rows; their product identity is not independently proven. The original published results and report are retained in previous-20261105; the original matched 44 results remain separate. The strict cohort chooses its own canonical product from modern candidates, before checking outcomes.

All 709 frozen forecasts are audited. Only unique date/home/away forecast official IDs can be linked; 239 score-based forecasts lack an official fixture ID and are excluded. General score candidates are grouped by fixture and official round; any round with more than one general winner row is skipped, and conflicting unique-round completed scores exclude the fixture. Among unambiguous completed rounds the numerically first is used. Each canonical row records product and general-winner round, matchSeq, identifiers, and archive body SHA-256; the score source path is recorded as well. Results do not require the general winner price to match the old 667-price cohort.

For each fixture and market, select lowest numeric round then matchSeq, before checking outcomes. Validate only that selected product; never fall back to a later one on failure. Require completed official original score, half line, two prices above 1, completed non-rejected product, product score and official grade consistent with the unadjusted score; model or market ties are excluded. An original baseball draw does not refund a half-line handicap. First-half and other markets are excluded. All archive bodies must be present and pass SHA-256. Raw archives and frozen forecasts are external read-only dependencies.

M0 selects the more probable side using its frozen home_rate and away_rate as independent Poisson run means; the market selects the lower price. `research/m1_service/model.py` computes the rates as exp(vector @ beta), then uses Skellam probabilities. `scripts/replay_m1_service.py` checks replayed three-way probabilities against preserved forecasts; it does not independently establish the historical availability of the model inputs. An independent worker-C confirmation of the rate/input contract was not available in this workspace. Results therefore remain conditional on that contract and the Poisson score model.

One unit per fixture. ROI uses all selected units and no refunds arise on included half lines. Drawdown is cumulative unit profit ordered by date, fixture ID, round, matchSeq; same-day ordering is arbitrary, not evidence of execution timing. Monthly drawdowns reset at each month. No product, round or outcome was selected by profitability.

Source hashes, per-fixture exclusions, counts and exact monthly figures are in audit.json; canonical.json contains the eligible rows. Run `python scripts/validate_m0_handicap.py --output experiments/expanded-kbo-markets-20261105/old44-handicap`, then `python experiments/expanded-kbo-markets-20261105/analyze.py` and `python experiments/expanded-kbo-markets-20261105/write_report.py`. The bundled source-manifest.json can be replaced with `--manifest PATH`; external paths listed in it must exist.

## Dates and handicap selection distribution

```json
{
  "date_ranges": {
    "strict": {
      "total": [
        "2026-04-21",
        "2026-07-26"
      ],
      "handicap": [
        "2026-04-21",
        "2026-07-26"
      ]
    },
    "sensitivity": {
      "total": [
        "2026-03-28",
        "2026-07-26"
      ],
      "handicap": [
        "2026-03-28",
        "2026-07-26"
      ]
    }
  },
  "handicap_distribution": {
    "strict": {
      "line_-2.5": 111,
      "pick_away": 111,
      "selected_line_2.5": 209,
      "selected_market_favorite": 209,
      "line_2.5": 98,
      "pick_home": 98
    },
    "sensitivity": {
      "line_-2.5": 151,
      "pick_away": 151,
      "selected_line_2.5": 274,
      "selected_market_favorite": 274,
      "line_2.5": 123,
      "pick_home": 123
    }
  }
}
```

The selected side has +2.5 in every eligible handicap: strict home +2.5 98 times and away +2.5 111 times; sensitivity home +2.5 123 times and away +2.5 151 times. These are also the market favorites in every case. This strong line advantage explains a plausible mechanical reason for agreement, rather than establishing independent model confirmation of the market or outperformance. The data contain no eligible ±1.5 handicap in either cohort.

## Exclusion audit

```json
{
  "forecast_without_official_id": 239,
  "forecast_official_id": 470,
  "sensitivity_handicap_raw_rows": 854,
  "sensitivity_total_raw_rows": 854,
  "strict_handicap_raw_rows": 784,
  "strict_total_raw_rows": 784,
  "strict_handicap_no_forecast": 453,
  "strict_total_no_forecast": 453,
  "sensitivity_handicap_no_forecast": 458,
  "sensitivity_total_no_forecast": 458,
  "strict_ambiguous_general_fixture_rounds": 0,
  "sensitivity_ambiguous_general_fixture_rounds": 0,
  "strict_handicap_fixture_candidates": 275,
  "strict_handicap_noncanonical_products": 56,
  "strict_handicap_eligible": 209,
  "strict_total_fixture_candidates": 275,
  "strict_total_noncanonical_products": 56,
  "strict_total_eligible": 200,
  "strict_handicap_excluded_line": 56,
  "strict_total_excluded_line": 56,
  "strict_total_excluded_market_tie": 9,
  "strict_handicap_excluded_winner_missing_or_ambiguous": 10,
  "strict_total_excluded_winner_missing_or_ambiguous": 10,
  "strict_total_forecast_fixtures_without_candidate": 434,
  "strict_handicap_forecast_fixtures_without_candidate": 434,
  "sensitivity_handicap_fixture_candidates": 340,
  "sensitivity_handicap_noncanonical_products": 56,
  "sensitivity_handicap_eligible": 274,
  "sensitivity_total_fixture_candidates": 340,
  "sensitivity_total_noncanonical_products": 56,
  "sensitivity_total_eligible": 264,
  "sensitivity_total_excluded_market_tie": 10,
  "sensitivity_handicap_excluded_line": 56,
  "sensitivity_total_excluded_line": 56,
  "sensitivity_handicap_excluded_winner_missing_or_ambiguous": 10,
  "sensitivity_total_excluded_winner_missing_or_ambiguous": 10,
  "sensitivity_total_forecast_fixtures_without_candidate": 369,
  "sensitivity_handicap_forecast_fixtures_without_candidate": 369
}
```

Counts labelled no candidate include unidentified forecasts and must not be added to the separate missing-ID count. Raw product rows and fixture counts have different denominators.

## Results: observed 2026 split and months

| Cohort | Month | N | Model hits / rate | Model net / ROI / max drawdown | Market hits / rate | Market net / ROI / max drawdown | Disagreements; model vs market hits |
|---|---|---:|---|---|---|---|---|
| strict total | all | 200 | 93 / 46.50% | -38.98 / -19.49% / 39.59 | 106 / 53.00% | -22.56 / -11.28% / 26.60 | 87; 37 vs 50 |
| strict total | 2026-04 | 20 | 7 / 35.00% | -7.75 / -38.75% / 8.36 | 8 / 40.00% | -6.65 / -33.25% / 7.26 | 7; 3 vs 4 |
| strict total | 2026-05 | 82 | 38 / 46.34% | -16.48 / -20.10% / 18.21 | 42 / 51.22% | -12.47 / -15.21% / 20.06 | 36; 16 vs 20 |
| strict total | 2026-06 | 38 | 19 / 50.00% | -4.71 / -12.39% / 7.06 | 21 / 55.26% | -2.31 / -6.08% / 8.49 | 18; 8 vs 10 |
| strict total | 2026-07 | 60 | 29 / 48.33% | -10.04 / -16.73% / 12.99 | 35 / 58.33% | -1.13 / -1.88% / 10.88 | 26; 10 vs 16 |
| strict handicap | all | 209 | 133 / 63.64% | -28.25 / -13.52% / 31.25 | 133 / 63.64% | -28.25 / -13.52% / 31.25 | 0; 0 vs 0 |
| strict handicap | 2026-04 | 20 | 16 / 80.00% | 1.40 / 7.00% / 3.05 | 16 / 80.00% | 1.40 / 7.00% / 3.05 | 0; 0 vs 0 |
| strict handicap | 2026-05 | 88 | 55 / 62.50% | -14.32 / -16.27% / 15.32 | 55 / 62.50% | -14.32 / -16.27% / 15.32 | 0; 0 vs 0 |
| strict handicap | 2026-06 | 40 | 28 / 70.00% | -2.10 / -5.25% / 5.22 | 28 / 70.00% | -2.10 / -5.25% / 5.22 | 0; 0 vs 0 |
| strict handicap | 2026-07 | 61 | 34 / 55.74% | -13.23 / -21.69% / 13.83 | 34 / 55.74% | -13.23 / -21.69% / 13.83 | 0; 0 vs 0 |
| sensitivity total | all | 264 | 126 / 47.73% | -46.39 / -17.57% / 47.76 | 137 / 51.89% | -35.60 / -13.48% / 43.42 | 115; 52 vs 63 |
| sensitivity total | 2026-03 | 15 | 8 / 53.33% | -1.40 / -9.33% / 4.86 | 11 / 73.33% | 3.39 / 22.60% / 1.35 | 7; 2 vs 5 |
| sensitivity total | 2026-04 | 69 | 32 / 46.38% | -13.76 / -19.94% / 14.08 | 28 / 40.58% | -23.08 / -33.45% / 23.40 | 28; 16 vs 12 |
| sensitivity total | 2026-05 | 82 | 38 / 46.34% | -16.48 / -20.10% / 18.21 | 42 / 51.22% | -12.47 / -15.21% / 20.06 | 36; 16 vs 20 |
| sensitivity total | 2026-06 | 38 | 19 / 50.00% | -4.71 / -12.39% / 7.06 | 21 / 55.26% | -2.31 / -6.08% / 8.49 | 18; 8 vs 10 |
| sensitivity total | 2026-07 | 60 | 29 / 48.33% | -10.04 / -16.73% / 12.99 | 35 / 58.33% | -1.13 / -1.88% / 10.88 | 26; 10 vs 16 |
| sensitivity handicap | all | 274 | 174 / 63.50% | -38.79 / -14.16% / 41.13 | 174 / 63.50% | -38.79 / -14.16% / 41.13 | 0; 0 vs 0 |
| sensitivity handicap | 2026-03 | 15 | 11 / 73.33% | -0.50 / -3.33% / 2.24 | 11 / 73.33% | -0.50 / -3.33% / 2.24 | 0; 0 vs 0 |
| sensitivity handicap | 2026-04 | 70 | 46 / 65.71% | -8.64 / -12.34% / 11.90 | 46 / 65.71% | -8.64 / -12.34% / 11.90 | 0; 0 vs 0 |
| sensitivity handicap | 2026-05 | 88 | 55 / 62.50% | -14.32 / -16.27% / 15.32 | 55 / 62.50% | -14.32 / -16.27% / 15.32 | 0; 0 vs 0 |
| sensitivity handicap | 2026-06 | 40 | 28 / 70.00% | -2.10 / -5.25% / 5.22 | 28 / 70.00% | -2.10 / -5.25% / 5.22 | 0; 0 vs 0 |
| sensitivity handicap | 2026-07 | 61 | 34 / 55.74% | -13.23 / -21.69% / 13.83 | 34 / 55.74% | -13.23 / -21.69% / 13.83 | 0; 0 vs 0 |
| old44 total | all | 44 | 23 / 52.27% | -4.63 / -10.52% / 7.35 | 25 / 56.82% | -2.70 / -6.14% / 8.00 | 22; 10 vs 12 |
| old44 total | 2026-03 | 15 | 8 / 53.33% | -1.40 / -9.33% / 4.86 | 11 / 73.33% | 3.39 / 22.60% / 1.35 | 7; 2 vs 5 |
| old44 total | 2026-04 | 29 | 15 / 51.72% | -3.23 / -11.14% / 5.20 | 14 / 48.28% | -6.09 / -21.00% / 7.62 | 15; 8 vs 7 |
| old44 handicap | all | 44 | 29 / 65.91% | -5.83 / -13.25% / 7.57 | 29 / 65.91% | -5.83 / -13.25% / 7.57 | 0; 0 vs 0 |
| old44 handicap | 2026-03 | 15 | 11 / 73.33% | -0.50 / -3.33% / 2.24 | 11 / 73.33% | -0.50 / -3.33% / 2.24 | 0; 0 vs 0 |
| old44 handicap | 2026-04 | 29 | 18 / 62.07% | -5.33 / -18.38% / 5.33 | 18 / 62.07% | -5.33 / -18.38% / 5.33 | 0; 0 vs 0 |
