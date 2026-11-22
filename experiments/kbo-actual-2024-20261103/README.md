# 2024 KBO retrospective actual-roster evidence

This artifact covers the fixed 728-fixture 2024 population in the pinned input
(`rows.jsonl` SHA-256 in `manifest.json`). It is an identity-only retrospective
reconstruction, not proof that any lineup was available before the decision time.
Every input fixture gets a row, including exclusions; only rows with an empty
`exclusion_reasons` array qualify. No result, price, pitcher performance, batting
performance, or `bf` field is exported as a model input.

For each eligible game, the official date schedule identifies the game, team sides,
series, and starting pitcher ID/name. Official confirmed batting slots 1–9 are
cross-checked against the corresponding official game-record presentation. The
game record's unique explicit `선발` flag must match the official schedule starting
pitcher name and ID, except for the documented rename below. Game-side/order identity candidates from the pinned archived
batter file are confirmed by exact name in an official player profile. No fuzzy
name matching, first-nine inference, winner-as-starter assumption, or performance
matching is used. Mismatches and missing identities exclude the fixture.
The one documented rename is handled with specific evidence: pitcher 68419 is
`최채흥` in the official schedule and same-game archived ID record, and `최지명`
in the official boxscore and current official profile. The profile response,
archive hash, source game key, and independent audit commit are retained.

The archive's synthetic postseason year IDs (including `3333`, `4444`, `5555`,
`6666`, and `7777`) are resolved by the official date, team codes and game number;
the date-only `score:` fixture requires a unique official date and team pairing.
Ambiguous same-day pairings are excluded. The source key is retained
when a synthetic ID is mapped. Official announcement time and historical receipt
time are unknown; the evidence receipt timestamps describe collection now.
The boxscore response does not independently carry a verified game ID: its binding
uses the recorded request parameters plus the schedule and lineup cross-checks.

Collection limitation: an earlier restart inadvertently left two collector
processes running concurrently. The request log showed intervals under the
specified 1.5 seconds and duplicated requests. Both were stopped, cached
response hashes were checked, and an OS-exclusive lock was added before
resuming. The manifest quantifies the spacing violations and counts requests,
including a conservative allowance for interrupted in-flight requests; do not
represent this collection as fully compliant with the spacing requirement.

`rosters.jsonl` is the machine-readable result. `batter-identity-only.json` contains
only game-side batter code, name, and order from the archived file. The compressed
`official-evidence.tar.gz` contains original official responses, request metadata,
and attempts; those original responses can contain target-game results and statistics
and must never be consumed as features. `manifest.json` pins hashes and coverage.
The capture process stores its resumable, mutable state outside the repository and
publishes the final artifacts only after capture and profile verification finish.

Tests: `python -m unittest discover -s tests -p test_research_kbo_actual_2024.py -v`.
The broader comparison contract is pinned at SHA-256
`512c6172eb792ed6e5f7afe7b90d4cfaa0e4087b05e0a497ec7cf9a997cb2480`.
