# KBO M0 retrospective market comparison

This is a retrospective replay, not pregame receipt, live availability or execution-price evidence. M0 was trained on 2023–2025; 2025 is not held out. The 2026 outcomes had already been seen and this is not a blind test. No adoption or future profit claim follows.

## Cohorts and validation

The primary strict cohort requires both modern product identifiers: full-game total betId 79 / betTypId 7, or full-game two-way half handicap betId 7 / betTypId 5. The original unadjusted score comes from a full-game modern general winner betId 2 / betTypId 2. The sensitivity cohort also allows legacy handi-only products and winner rows; their product identity is not independently proven. The original published results and report are retained in previous-20261105; the original matched 44 results remain separate. The strict cohort chooses its own canonical product from modern candidates, before checking outcomes.

All 709 frozen forecasts are audited. The original strict and sensitivity groups exclude 239 score-based forecasts lacking an official fixture ID. The optional strict_resolved group adds 237 independently resolved identities using the preserved original forecast ID, verified date/home/away/kickoff metadata and official game ID; two kickoff mismatches remain excluded. The resolved_only group contains only the additions. Alias source-file hashes and excluded cases are recorded in audit.json; each matched product must also match the resolver round, body hash and kickoff evidence. Duplicate official IDs and ambiguous date/team keys cause failure. General score candidates are grouped by fixture and official round; any round with more than one general winner row is skipped, and conflicting unique-round completed scores exclude the fixture. Among unambiguous completed rounds the numerically first is used. Each canonical row records product and general-winner round, matchSeq, identifiers, and archive body SHA-256; the score source path is recorded as well. Results do not require the general winner price to match the old 667-price cohort.

For each fixture and market, select lowest numeric round then matchSeq, before checking outcomes. Validate only that selected product; never fall back to a later one on failure. Require completed official original score, half line, two prices above 1, completed non-rejected product, product score and official grade consistent with the unadjusted score; model or market ties are excluded. An original baseball draw does not refund a half-line handicap. First-half and other markets are excluded. All archive bodies must be present and pass SHA-256. Raw archives and frozen forecasts are external read-only dependencies.

M0 selects the more probable side using its frozen home_rate and away_rate as independent Poisson run means; the market selects the lower price. `research/m1_service/model.py` computes the rates as exp(vector @ beta), then uses Skellam probabilities. `scripts/replay_m1_service.py` checks replayed three-way probabilities against preserved forecasts; it does not independently establish the historical availability of the model inputs. An independent worker-C confirmation of the rate/input contract was not available in this workspace. Results therefore remain conditional on that contract and the Poisson score model.

One unit per fixture. ROI uses all selected units and no refunds arise on included half lines. Drawdown is cumulative unit profit ordered by date, fixture ID, round, matchSeq; same-day ordering is arbitrary, not evidence of execution timing. Monthly drawdowns reset at each month. No product, round or outcome was selected by profitability.

Source hashes, per-fixture exclusions, counts and exact monthly figures are in audit.json; canonical.json contains the eligible rows. Run `python scripts/validate_m0_handicap.py --output experiments/expanded-kbo-markets-20261105/old44-handicap`, then `python experiments/expanded-kbo-markets-20261105/analyze.py --identity-aliases C:/Users/user/Documents/Codex/expanded-kbo-identities-20261105/experiments/expanded-kbo-identities-20261105/resolved.json` and `python experiments/expanded-kbo-markets-20261105/write_report.py`. The bundled source-manifest.json can be replaced with `--manifest PATH`; external paths listed in it must exist.

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
    },
    "strict_resolved": {
      "total": [
        "2026-04-21",
        "2026-10-27"
      ],
      "handicap": [
        "2026-04-21",
        "2026-10-27"
      ]
    },
    "resolved_only": {
      "total": [
        "2026-07-28",
        "2026-10-27"
      ],
      "handicap": [
        "2026-07-28",
        "2026-10-27"
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
    },
    "strict_resolved": {
      "line_-2.5": 207,
      "pick_away": 207,
      "selected_line_2.5": 366,
      "selected_market_favorite": 366,
      "line_2.5": 161,
      "pick_home": 161,
      "selected_line_-2.5": 2,
      "selected_market_underdog": 2
    }
  }
}
```

In the original strict cohort the selected side has +2.5 in all 209 handicaps. In strict_resolved it has +2.5 in 366 of 368 and -2.5 twice; those two selections disagree with the market favorite. This line advantage plausibly contributes to the strong agreement; it does not establish independent model confirmation of the market or outperformance. No eligible ±1.5 handicap occurs in these cohorts.

## Exclusion audit

```json
{
  "forecast_without_official_id": 239,
  "forecast_official_id": 470,
  "verified_aliases": 237,
  "unresolved_score_forecasts": 2,
  "sensitivity_handicap_raw_rows": 854,
  "sensitivity_total_raw_rows": 854,
  "strict_handicap_raw_rows": 784,
  "strict_total_raw_rows": 784,
  "strict_resolved_handicap_raw_rows": 784,
  "strict_resolved_total_raw_rows": 784,
  "strict_handicap_no_forecast": 453,
  "strict_total_no_forecast": 453,
  "sensitivity_handicap_no_forecast": 458,
  "sensitivity_total_no_forecast": 458,
  "strict_resolved_handicap_no_forecast": 134,
  "strict_resolved_total_no_forecast": 134,
  "strict_ambiguous_general_fixture_rounds": 0,
  "sensitivity_ambiguous_general_fixture_rounds": 0,
  "strict_resolved_ambiguous_general_fixture_rounds": 0,
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
  "sensitivity_handicap_forecast_fixtures_without_candidate": 369,
  "strict_resolved_handicap_fixture_candidates": 512,
  "strict_resolved_handicap_noncanonical_products": 138,
  "strict_resolved_handicap_eligible": 368,
  "strict_resolved_total_fixture_candidates": 512,
  "strict_resolved_total_noncanonical_products": 138,
  "strict_resolved_total_eligible": 353,
  "strict_resolved_handicap_excluded_line": 134,
  "strict_resolved_total_excluded_line": 134,
  "strict_resolved_total_excluded_market_tie": 15,
  "strict_resolved_handicap_excluded_winner_missing_or_ambiguous": 10,
  "strict_resolved_total_excluded_winner_missing_or_ambiguous": 10,
  "strict_resolved_total_forecast_fixtures_without_candidate": 197,
  "strict_resolved_handicap_forecast_fixtures_without_candidate": 197
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
| strict_resolved total | all | 353 | 172 / 48.73% | -53.60 / -15.18% / 59.31 | 180 / 50.99% | -50.81 / -14.39% / 51.42 | 158; 75 vs 83 |
| strict_resolved total | 2026-04 | 20 | 7 / 35.00% | -7.75 / -38.75% / 8.36 | 8 / 40.00% | -6.65 / -33.25% / 7.26 | 7; 3 vs 4 |
| strict_resolved total | 2026-05 | 82 | 38 / 46.34% | -16.48 / -20.10% / 18.21 | 42 / 51.22% | -12.47 / -15.21% / 20.06 | 36; 16 vs 20 |
| strict_resolved total | 2026-06 | 38 | 19 / 50.00% | -4.71 / -12.39% / 7.06 | 21 / 55.26% | -2.31 / -6.08% / 8.49 | 18; 8 vs 10 |
| strict_resolved total | 2026-07 | 70 | 35 / 50.00% | -9.26 / -13.23% / 13.99 | 40 / 57.14% | -2.69 / -3.84% / 10.88 | 33; 14 vs 19 |
| strict_resolved total | 2026-08 | 57 | 29 / 50.88% | -6.45 / -11.32% / 10.65 | 29 / 50.88% | -8.37 / -14.68% / 13.24 | 26; 13 vs 13 |
| strict_resolved total | 2026-09 | 56 | 24 / 42.86% | -13.63 / -24.34% / 14.96 | 24 / 42.86% | -15.12 / -27.00% / 17.27 | 24; 12 vs 12 |
| strict_resolved total | 2026-10 | 30 | 20 / 66.67% | 4.68 / 15.60% / 3.53 | 16 / 53.33% | -3.20 / -10.67% / 4.98 | 14; 9 vs 5 |
| strict_resolved handicap | all | 368 | 231 / 62.77% | -53.66 / -14.58% / 56.73 | 233 / 63.32% | -50.20 / -13.64% / 53.27 | 2; 0 vs 2 |
| strict_resolved handicap | 2026-04 | 20 | 16 / 80.00% | 1.40 / 7.00% / 3.05 | 16 / 80.00% | 1.40 / 7.00% / 3.05 | 0; 0 vs 0 |
| strict_resolved handicap | 2026-05 | 88 | 55 / 62.50% | -14.32 / -16.27% / 15.32 | 55 / 62.50% | -14.32 / -16.27% / 15.32 | 0; 0 vs 0 |
| strict_resolved handicap | 2026-06 | 40 | 28 / 70.00% | -2.10 / -5.25% / 5.22 | 28 / 70.00% | -2.10 / -5.25% / 5.22 | 0; 0 vs 0 |
| strict_resolved handicap | 2026-07 | 71 | 42 / 59.15% | -12.54 / -17.66% / 15.23 | 43 / 60.56% | -10.82 / -15.24% / 13.83 | 1; 0 vs 1 |
| strict_resolved handicap | 2026-08 | 57 | 33 / 57.89% | -11.58 / -20.32% / 12.43 | 34 / 59.65% | -9.84 / -17.26% / 10.69 | 1; 0 vs 1 |
| strict_resolved handicap | 2026-09 | 60 | 37 / 61.67% | -10.23 / -17.05% / 11.99 | 37 / 61.67% | -10.23 / -17.05% / 11.99 | 0; 0 vs 0 |
| strict_resolved handicap | 2026-10 | 32 | 20 / 62.50% | -4.29 / -13.41% / 6.70 | 20 / 62.50% | -4.29 / -13.41% / 6.70 | 0; 0 vs 0 |
| resolved_only total | all | 153 | 79 / 51.63% | -14.62 / -9.56% / 20.63 | 74 / 48.37% | -28.25 / -18.46% / 28.94 | 71; 38 vs 33 |
| resolved_only total | 2026-07 | 10 | 6 / 60.00% | 0.78 / 7.80% / 1.27 | 5 / 50.00% | -1.56 / -15.60% / 2.25 | 7; 4 vs 3 |
| resolved_only total | 2026-08 | 57 | 29 / 50.88% | -6.45 / -11.32% / 10.65 | 29 / 50.88% | -8.37 / -14.68% / 13.24 | 26; 13 vs 13 |
| resolved_only total | 2026-09 | 56 | 24 / 42.86% | -13.63 / -24.34% / 14.96 | 24 / 42.86% | -15.12 / -27.00% / 17.27 | 24; 12 vs 12 |
| resolved_only total | 2026-10 | 30 | 20 / 66.67% | 4.68 / 15.60% / 3.53 | 16 / 53.33% | -3.20 / -10.67% / 4.98 | 14; 9 vs 5 |
| resolved_only handicap | all | 159 | 98 / 61.64% | -25.41 / -15.98% / 27.62 | 100 / 62.89% | -21.95 / -13.81% / 25.88 | 2; 0 vs 2 |
| resolved_only handicap | 2026-07 | 10 | 8 / 80.00% | 0.69 / 6.90% / 2.00 | 9 / 90.00% | 2.41 / 24.10% / 1.00 | 1; 0 vs 1 |
| resolved_only handicap | 2026-08 | 57 | 33 / 57.89% | -11.58 / -20.32% / 12.43 | 34 / 59.65% | -9.84 / -17.26% / 10.69 | 1; 0 vs 1 |
| resolved_only handicap | 2026-09 | 60 | 37 / 61.67% | -10.23 / -17.05% / 11.99 | 37 / 61.67% | -10.23 / -17.05% / 11.99 | 0; 0 vs 0 |
| resolved_only handicap | 2026-10 | 32 | 20 / 62.50% | -4.29 / -13.41% / 6.70 | 20 / 62.50% | -4.29 / -13.41% / 6.70 | 0; 0 vs 0 |
| old44 total | all | 44 | 23 / 52.27% | -4.63 / -10.52% / 7.35 | 25 / 56.82% | -2.70 / -6.14% / 8.00 | 22; 10 vs 12 |
| old44 total | 2026-03 | 15 | 8 / 53.33% | -1.40 / -9.33% / 4.86 | 11 / 73.33% | 3.39 / 22.60% / 1.35 | 7; 2 vs 5 |
| old44 total | 2026-04 | 29 | 15 / 51.72% | -3.23 / -11.14% / 5.20 | 14 / 48.28% | -6.09 / -21.00% / 7.62 | 15; 8 vs 7 |
| old44 handicap | all | 44 | 29 / 65.91% | -5.83 / -13.25% / 7.57 | 29 / 65.91% | -5.83 / -13.25% / 7.57 | 0; 0 vs 0 |
| old44 handicap | 2026-03 | 15 | 11 / 73.33% | -0.50 / -3.33% / 2.24 | 11 / 73.33% | -0.50 / -3.33% / 2.24 | 0; 0 vs 0 |
| old44 handicap | 2026-04 | 29 | 18 / 62.07% | -5.33 / -18.38% / 5.33 | 18 / 62.07% | -5.33 / -18.38% / 5.33 | 0; 0 vs 0 |
