# Atomic storage-time match projection

Base: `97ffc5da068dfa66b57c85cabaa9d56944288160` (`origin/main`).
Branch: `codex/atomic-match-projection-20261001`. Backend and web changes;
no production access, deployment, restart or merge in this work.

## Storage and compatibility

`RuntimeDatabase.store_artifact('picks_v2', ...)` and successful
`import_artifact('picks_v2', ...)` publish the original artifact, a compact card
document, and one zlib BLOB per match inside the same `BEGIN IMMEDIATE`
transaction. The projection header carries the artifact's exact `stored_at`
revision and schema version. Detail rows belong to that header through an FK.
The writer derives a monotonically increasing microsecond timestamp under the
write lock, including when the wall clock stalls or moves backward. Any failure
rolls back all three representations; skipped imports leave all three unchanged.

The original payload and prediction ledger are not modified. Frozen decision
snapshots, prediction revisions, selections and options remain equal in cards
and detail responses. The existing `card_game` contract still determines which
player fields are compacted; other fields are preserved for compatibility.

`MatchViews` reads the revision, card document and compressed details from one
explicit WAL read transaction, then publishes its in-memory state only after a
successful load. It never decompresses all details, reconstructs a full picks
Python graph, or recompresses details on the materialized path. A detail request
decodes one game. Compact card graphs and compressed detail BLOBs are retained;
this is not an O(1)-memory cache. Recent/all/date filtering and response shapes
remain unchanged.

New tables are created on normal DB initialization. Existing DBs are not
automatically backfilled. A missing, incompatible-version or mismatched-revision
projection uses a read-only legacy fallback, including after an old writer
updates the original artifact. That fallback uses SQLite `json_remove` and
`json_each`, materializing only one full game at a time in Python. It still
parses full JSON in SQLite and builds compact cards/compressed details in the
warmer. It is not a memory-bounded SQLite parser and is slower than a stored
projection. It does not generate or write a persisted projection on requests.
The next ordinary successful picks write supplies the projection atomically.

Production HTTP uses `PreparedMatchResponses`, whose cold and warm list/detail
requests never query the DB or generate projections. Only its background
`refresh()` calls `MatchViews`. Direct internal calls to `MatchViews.get()` can
still execute the read-only legacy fallback; callers should not substitute them
for the prepared HTTP path. A DB/projection load failure before in-memory
publication retains the previous view and prepared replies.

Storage publication is atomic; HTTP scopes are **not globally atomic**.
`PreparedMatchResponses.refresh()` publishes each requested scope separately.
If recent succeeds and all fails, recent can expose the new revision while all
retains the old reply. JSON serialization or gzip failure can also occur after
`MatchViews` has advanced its revision/details but before the scope's prepared
reply is replaced. That scope retains its original bytes and revision, but its
old-revision detail requests are rejected (the HTTP handler can return 503).
Old matching details are not guaranteed after every refresh failure. The
revision check deliberately fails closed instead of returning new details
under an old list revision. This existing limitation is preserved; successful
retry restores scope availability without relabeling stale replies.

`open_artifact_json()` is copied exactly from commit `0ec985f8` for independent
streamed artifact gzip integration. It yields a read-only SQLite BLOB and its
revision from one snapshot; it requires Python's `sqlite3.Connection.blobopen`.
No ijson dependency or prior PR255 streamed MatchViews implementation is used.

## Reproduce local benchmarks

```powershell
python scripts/benchmark_match_projection.py --games 600 --history 200
python scripts/benchmark_match_projection.py --input docs/data/picks_v2.json
```

The script creates disposable local databases. Original-main and new writers
and readers run in separate fresh processes; baseline source comes from local
`git show` of the exact base above. The reader comparisons are original-main,
stored projection, and legacy fallback. Recent/all/detail response SHA-256
digests must match across all three. Warm timing is 30 `MatchViews.get_bytes`
calls (including DB revision checks and the first gzip preparation), not HTTP
latency. Refresh advances the same fixture's revision in another process so
writer allocations do not contaminate reader RSS. Windows working-set counters
include native SQLite allocations; peak RSS is process-lifetime high-water,
not independently reset per phase. CPU resolution is coarse on Windows.
Measurements below are one local run on Python 3.14/Windows, not production
capacity estimates or statistical confidence intervals. OS filesystem caches
are not cleared. Writers already hold their input graph at timing start.

### Archived repository fixture (NOT a live production copy)

- File bytes: 12,831,809; generated_at: `2026-09-05T07:12:50+00:00`.
- File SHA-256: `abe9350eee06ccf8a510c0295d4af8364e5d168f9174ee94d2c286c697822efa`.
- Stored compact artifact character count: 7,267,703 (SQLite `length(TEXT)`,
  not UTF-8 byte size). Fixture dates are old, so recent cards are empty on the
  measurement date; all/detail and frozen payload equality are checked as well.

All values below: wall seconds / CPU seconds / current RSS MB / peak RSS MB
(decimal MB). Order is original / materialized / fallback for reader rows.

| Phase | Original main | Materialized projection | Legacy fallback |
| --- | --- | --- | --- |
| Cold recent | .1671 / .1563 / 59.85 / 70.73 | .0592 / .0469 / 47.93 / 57.41 | .2466 / .2344 / 52.62 / 70.64 |
| Warm 30 | .2135 / .2031 / 59.90 / 75.72 | .2114 / .2031 / 50.66 / 67.29 | .2359 / .2031 / 55.65 / 70.66 |
| Revision refresh | .1974 / .2031 / 76.60 / 95.00 | .0690 / .0781 / 59.29 / 80.92 | .2595 / .2500 / 68.86 / 98.40 |
| First store | .1111 / .1094 / 56.00 / 98.78 | .3108 / .2969 / 58.62 / 98.30 | n/a |
| Replacement store | .0736 / .0625 / 56.01 / 98.80 | .2715 / .2656 / 60.50 / 102.36 | n/a |

The archive shows modest RSS savings, not the much larger synthetic reduction.
Fallback refresh actually peaks higher than the original on this fixture. The
new first/replacement stores cost roughly 0.20 seconds more locally; compression
and card generation have moved into the write transaction, increasing writer
lock duration. Existing WAL readers remain available while this happens.

### Synthetic fixture

600 matches, 200 generated player-history records each, all dated on the local
KST measurement day; generated_at is literally `synthetic`. Stored character
count 28,446,026. This deliberately emphasizes heavy detail histories and is
not a production workload sample.

| Phase | Original main | Materialized projection | Legacy fallback |
| --- | --- | --- | --- |
| Cold recent | .5143 / .4844 / 114.39 / 169.51 | .0303 / .0313 / 31.44 / 32.55 | .5915 / .5938 / 32.47 / 111.64 |
| Warm 30 | .6306 / .6250 / 114.48 / 169.51 | .6176 / .6250 / 33.63 / 35.74 | .6477 / .6563 / 34.29 / 111.64 |
| Revision refresh | .4550 / .4688 / 117.77 / 199.87 | .0353 / .0469 / 35.46 / 39.64 | .6274 / .6094 / 37.48 / 119.31 |
| First store | .3874 / .3750 / 109.15 / 253.36 | .6074 / .5469 / 109.95 / 253.03 | n/a |
| Replacement store | .2478 / .2500 / 109.01 / 253.39 | .4921 / .4688 / 109.74 / 254.35 | n/a |

Warm API processing does not materially improve; avoiding full reparse and
compression primarily helps first-load/revision changes. Writer peak RSS is
still dominated by the input graph, original JSON serialization and SQLite.
The extra projection adds storage/WAL writes. No claim is made that this alone
eliminates production OOM or establishes safe worker/admission limits.

## Regression coverage

Tests cover recent/all/KST year-boundary and overnight equality, full payload
and frozen detail equality, no materialized-path recompression, cold HTTP no-DB
behavior, import skip/publication, rapid distinct revisions, legacy migration
and old-writer mismatch fallback, concurrent commit between revision and
projection reads, readers during partial write publication, write failure
rollback, read failure retaining prepared bytes/revision, and read-only BLOB
snapshot/closure behavior.

Final integrated Python suite: 874 passed, 58 subtests passed. Web suite:
242 passed. Vite production build passed (137 modules), with outputs isolated
in the untracked `.validation-build` directory.

Additional scoped regressions inject gzip failure after the view advances, both
on recent and on all after recent has published. They verify unchanged failed-
scope reply bytes, rejection of old-revision details, partial scope publication,
and successful retry. These tests do not claim globally atomic HTTP publication.

## Integrated HTTP and web safeguards

The HTTP server caps workers and backlog at eight and returns a best-effort 503
on saturation. A 15-second socket I/O timeout is not a CPU/DB execution deadline.
Artifact gzip reads a consistent read-only SQLite BLOB snapshot in 64-KiB chunks.
This proposal supersedes PR #255's match-reader approach while retaining its
bounded HTTP and chunked gzip safeguards; it does not require ijson. Review this
as an alternative to #255, not as two independent changes to merge blindly.
A new collector image is required to deploy the supervisor changes.

Web auxiliary feeds now use single-flight requests, abort on unmount, and time
out after 15 seconds. Failed refreshes retain the last successful values.
Analysis, odds, recommendations and scores expose separate generated timestamps
and request errors: a successful request does not relabel an old analysis fresh.
Existing recommendation eligibility is unchanged. A local browser component
fixture verified a 120-minute-old analysis beside recent odds and a failed feed;
this was not a production page or end-to-end deployment verification.
