# Offline exhaustive five-class PA diagnostic

Classes: K, BB+HBP, HR, nonHRhit, otherPA. OtherPA includes both former otherout and residualreach; it is never labeled outs. All 2,247 corrected PA rows are preserved, including errors/interference/choices. Three non-PA runner events remain excluded by the corrected extractor.

Historical seeds use all acquired MLB regular-season player splits for 2024 and January–May 31, 2025. Every raw URL/body hash, requested window, page offset, unique player ID and count identity is checked. Full-season game-log receipts used for acquisition validation are never model features. Range bounds are request-validated and previously sample-reconciled, not echoed in bulk responses. Retrospective receipt is not proof of point-in-time availability.

Seeded league sums batting counts over the full acquired league population once (not only target players; pitching counts are not added again). Batter PA and pitcher BF each map to [K, BB+HBP, HR, hits−HR, denominator−K−BB−HBP−hits]. All counts are nonnegative and sum to their denominator. Both years receive equal per-PA weight, without recency tuning.

League probabilities=(counts+1)/(PA+5). Each player distribution has fixed strength 100 toward that league prior; candidate averages batter and pitcher distributions equally. Missing players fall back to the league prior. Cold baselines use the same five classes and settings without historical seeds. June 1 is warmup for every model, even seeded models. Predict a full day before updating any counts. Comparison uses exactly 1,177 PA in 15 games on June 2–3. No sweeps, network or production changes.

## Conditional PA scores

Natural-log loss and multiclass Brier (sum over five classes), lower is better.
- 2025-06-01 / cold_batter_pitcher: PA=1070, games=15, log loss=1.609437912, Brier=0.800000000.
- 2025-06-01 / cold_league: PA=1070, games=15, log loss=1.609437912, Brier=0.800000000.
- 2025-06-01 / seeded_batter_pitcher: PA=1070, games=15, log loss=1.286274367, Brier=0.672555713.
- 2025-06-01 / seeded_league: PA=1070, games=15, log loss=1.301626375, Brier=0.678902099.
- 2025-06-02 / cold_batter_pitcher: PA=555, games=7, log loss=1.327120452, Brier=0.684790000.
- 2025-06-02 / cold_league: PA=555, games=7, log loss=1.327349785, Brier=0.684721013.
- 2025-06-02 / seeded_batter_pitcher: PA=555, games=7, log loss=1.303077844, Brier=0.677846592.
- 2025-06-02 / seeded_league: PA=555, games=7, log loss=1.322103547, Brier=0.683054775.
- 2025-06-03 / cold_batter_pitcher: PA=622, games=8, log loss=1.324549761, Brier=0.686381494.
- 2025-06-03 / cold_league: PA=622, games=8, log loss=1.324961379, Brier=0.686462716.
- 2025-06-03 / seeded_batter_pitcher: PA=622, games=8, log loss=1.311421871, Brier=0.681008397.
- 2025-06-03 / seeded_league: PA=622, games=8, log loss=1.323618622, Brier=0.685988192.
- ALL_EVALUATION / cold_batter_pitcher: PA=1177, games=15, log loss=1.325761939, Brier=0.685631044.
- ALL_EVALUATION / cold_league: PA=1177, games=15, log loss=1.326087603, Brier=0.685641437.
- ALL_EVALUATION / seeded_batter_pitcher: PA=1177, games=15, log loss=1.307487347, Brier=0.679517486.
- ALL_EVALUATION / seeded_league: PA=1177, games=15, log loss=1.322904207, Brier=0.684604975.

Seeded candidate minus seeded league: log loss=-0.015416860; Brier=-0.005087489.

These are descriptive conditional PA scores for realized batter/pitcher identities, not pregame lineup predictions or game-win improvement. Only two evaluation dates and 15 clustered game units; this bounded sample cannot establish generalization, betting value, or win accuracy.

## Audit

Original counts: {'BB-HBP': 208, 'otherout': 1044, 'K': 505, 'otherhit': 414, 'residualreach': 21, 'HR': 55}.
Coarsened counts: {'BB+HBP': 208, 'otherPA': 1065, 'K': 505, 'nonHRhit': 414, 'HR': 55}.
Evaluation identity SHA-256: a340035e9f7900cb64ba37ae3bc0a09c126d0e6c3293f490ea9b8361e00dab4d.
Zero-history PA: {'seeded_batter_pitcher': {'batter': 9, 'pitcher': 15}, 'cold_batter_pitcher': {'batter': 161, 'pitcher': 994}}.
Historical seed audits: [{"group": "hitting", "start": "2024-01-01", "end": "2024-12-31", "players": 742, "counts": {"K": 41197, "BB+HBP": 16949, "HR": 5453, "nonHRhit": 34370, "otherPA": 84480}, "denominator": 182449}, {"group": "hitting", "start": "2025-01-01", "end": "2025-05-31", "players": 575, "counts": {"K": 14345, "BB+HBP": 6329, "HR": 1876, "nonHRhit": 12408, "otherPA": 30418}, "denominator": 65376}, {"group": "pitching", "start": "2024-01-01", "end": "2024-12-31", "players": 855, "counts": {"K": 41197, "BB+HBP": 16949, "HR": 5453, "nonHRhit": 34370, "otherPA": 84480}, "denominator": 182449}, {"group": "pitching", "start": "2025-01-01", "end": "2025-05-31", "players": 649, "counts": {"K": 14345, "BB+HBP": 6329, "HR": 1876, "nonHRhit": 12408, "otherPA": 30418}, "denominator": 65376}].

Both source databases are opened read-only and their SHA-256 hashes are unchanged:
- C:\Users\user\.codex\visualizations\2026\09\04\01a06bca-36db-7623-8d86-0f2d21fb08f2\mlb-pitch-pilot-20260909\outputs\pilot-20260909\pilot.sqlite: 485afb40cbfc434a1ad52665f2ec71ea5863162ff2467dc1d9235adad8bb6c9d
- C:\Users\user\.codex\visualizations\2026\09\04\01a06bca-36db-7623-8d86-0f2d21fb08f2\mlb-player-history-20260909\experiments\mlb_player_history\private\run-20260909\history.sqlite: 2458f31362dc4c4fcbbab8cd93ff2633a86f6bd7165866fbd37d3386fe2add1c

Private results.sqlite stores all four models’ row-level probabilities, counts and losses; summary.json records code hashes and detailed audit. Only code, synthetic tests and aggregate Markdown are tracked. Source: existing official MLB StatsAPI receipts and corrected cached feed extraction.

## Verification

13 tests passed (5 follow-up tests plus 8 acquisition regression tests). Follow-up tests cover
exact exhaustive coarsening, historical sum identities, same/future-day invariance,
strength-100 pooling, missing-player fallback, read-only access and cutoff rejection.
No additional network requests, production changes, scheduling, push or PR.
