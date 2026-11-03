# Fresh KBO player-history inputs, 2026

These four gzip JSONL files retain the previously fixed 709-game cohort (193 original plus 516 additional) and the original non-player features. They differ only in lineup and starting-pitcher selectors. All four use the same repaired prior-day player and bullpen histories. The existing 193-game and 709-game artifacts remain unchanged.

- `fresh-history-2026-projected.jsonl.gz`: prior-game projected lineup and pitcher.
- `fresh-history-2026-official_batting_only.jsonl.gz`: retrospective official lineup and projected pitcher.
- `fresh-history-2026-official_starter_only.jsonl.gz`: projected lineup and retrospective schedule-listed pitcher.
- `fresh-history-2026-official_batting_and_starter.jsonl.gz`: both retrospective official selectors.
- `fresh-history-2026-incomplete-batters.jsonl.gz`: 3,026 excluded incomplete batter observations, with unknown BB retained as null.
- `fresh-history-2026-manifest.json`: exact source hashes, cohort counts, selector coverage and component-usable freshness, including overlapping 193/516 and pre/post July 26 groups.

The supplement is frozen at `d11367b7b37f3ea34f4c08987ca042af9235575e`; the protocol was committed as `1a15ab92da2d26a1744600716a44dcdf4f6bc37b`. The build requires the pinned `kbor` and `klu` checkouts and their captured raw response stores. The builder verifies raw bytes against Git blobs and runs both independent raw-response audits before generating outputs. Source evidence records actual receipt times; these are not historical game-day receipt times. No historical publication or completion time is asserted. The 17 previously excluded games stay excluded.

Batter histories require validated AB, H and pure BB; 537 complete zero-AB/zero-BB rows remain observations and are included in the last-ten window. Of those, 518 have zero plate appearances and 19 have one plate appearance. A zero-only denominator is not a calculable rate. Pitcher ERA inputs require nonnegative earned runs and outs with a validated player ID; ambiguous repeated IDs within a game are excluded from individual history. Bullpen outs include unlinked relievers only when the individual outs reconcile to the official team total. Missing data remains missing. Distinct same-date games are never exposed to one another. Source lineups and scheduled pitchers used for projected selectors must come from a prior calendar day; ambiguous last-game lineups remain missing.

These are retrospective research inputs, not point-in-time-verified data or results. The frozen adapter's scalar function supports their individual feature values, but its complete `build_features` entry point rejects the new scenario names and fixed 709-row cohort; explicit downstream integration is needed. No model fitting, performance measurement, production release, or claim of an available pregame signal is made.

Run `python experiments/kbo-player-data-20261103/build_fresh_history_2026.py` to rebuild and `python experiments/kbo-player-data-20261103/test_fresh_history_2026.py` to validate. The test independently reconstructs post-cutoff player histories from the pinned source and checks all four outputs, temporal boundaries, factorial matching, untouched non-player features, zero and missing handling, and baseball innings conversion.
