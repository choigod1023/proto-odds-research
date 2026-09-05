# Recent results source adapters

Implemented only in `src/sports_sources_results.py`, with fixtures and failure
tests in `tests/test_sports_sources_results.py`. No DB, scheduler, UI, model,
team-name matching, deployment, or runtime configuration changes.

## Runner contract

Call `collect_naver(fetch, league, since, until, limit)` or
`collect_mlb(fetch, since, until, limit)`. `fetch(url)` returns decoded response
text. It must raise on HTTP errors and use no retries, particularly for 403/429.
The adapters parse JSON themselves, do not create HTTP sessions, and propagate
transport exceptions unchanged. The runner owns raw response capture and
`observed_at`; neither is duplicated here.

CLI league names, matching the runner's registry: `KBO`, `NPB`, `NBA`, `KBL`,
`WKBL`, `KOVO남`, `KOVO여`. The Naver collector also supports `MLB` if the runner
wants `naver:MLB`; the separate official MLB collector always emits `MLB`.
`V리그남`/`V리그여` are not registry keys. No category aliases or fuzzy matches.

Registry evidence is `live_scores.CATS` for baseball and `court_info.NAVER_CATS`
for basketball/volleyball. The exact request selectors are:

- KBO: `upperCategoryId=kbaseball`, `categoryId=kbo`.
- MLB/NPB: `upperCategoryId=wbaseball`, `categoryId=mlb` / `npb`.
- NBA/KBL/WKBL: `superCategoryId=basketball`, `categoryId=nba` / `kbl` / `wkbl`.
- KOVO남/KOVO여: `superCategoryId=volleyball`, `categoryId=kovo` / `wkovo`.

Cup aliases from court_info are not imported: the categories also contain
ordinary league games, so a cup-only label would misrepresent the response.
WNBA raises `UnsupportedLeagueError` without a request. No WNBA category was
found in existing adapters or the inspected public basketball schedule client.
A basketball-wide, category-unfiltered schedule probe for 2026-08-29 through
2026-09-04 returned 13 games in `basketballetc` (7) and `ubasketball` (6), without
a WNBA category. This does **not** establish WNBA offseason or lack of WNBA games;
it establishes only unverified Naver coverage. No guessed `categoryId=wnba` is used.

Each record contains `provider`, source-native string `event_id`, `league`,
Korean `sport` (야구/농구/배구), aware UTC ISO `kickoff_at`, `status`, source-native
string `home_id`/`away_id`, original `home_name`/`away_name`, integer scores (null
for cancellations), `score_unit` (runs/points/sets), `source_url`, `metrics` with
home/away objects, and `metric_status`. The runner must namespace event/team IDs
by provider and league; Naver MLB team IDs differ from official MLB IDs. No
automatic cross-provider equivalence or team-form join is asserted.

Dates are inclusive **provider schedule dates**, not UTC kickoff dates. Naver
uses KST; MLB uses officialDate. Naver's offset-free ISO gameDateTime is KST,
documented in live_scores and parsed that way in soccer_info._naver_start. The
legacy `%m/%d/%Y %H:%M:%S` format is supported for the same documented reason.
The same observed MLB game confirms the offset: Naver `20260904SFPI0` says
`2026-09-04T01:35:00`; official MLB `823337` says `2026-09-03T16:35:00Z`.
Timezone-aware values are respected. Missing/invalid/TBD kickoff is an error,
not a manufactured midnight timestamp. MLB naive timestamps are rejected.

## Completeness and terminal status

Ranges must be 1–31 inclusive days and limit must be an integer 1–1000. Larger
history backfills should explicitly partition dates at the runner. Naver fetches
100 games per page, at most 20 pages, and uses the observed one-based `page`
parameter and `result.gameTotalCount`. It counts all schedule games, including
nonfinal rows, before deciding that pagination is complete. It does not stop
merely because the output limit or a short page was reached.

Only explicit RESULT/END/ENDED are finals; live/scheduled/suspended games are
skipped. The observed `cancel=true` overrides even statusCode BEFORE or a stale
RESULT. Explicit CANCEL/CANCELED/CANCELLED/POSTPONED are cancellation/invalidation
markers; scores and metrics are cleared. Cancellation records are for
invalidation, never inputs to final-score team form.

`[]` means a validated schedule contains no eligible terminal records. It is
never substituted for bad JSON, an error envelope, missing required fields, or
HTTP failure. `ResultsSourceError` reports malformed/inconsistent source data.
`PartialResultsError` exposes `status='partial'`, `reason`, `partial_results`,
and `source_url`: output overflow, repeated pages/IDs, changed totals, missing
pages, or the page budget. Partials are bounded by limit and must not be
mistaken for a complete range. Normal return values are sorted newest kickoff
first, breaking ties by native event ID. A limit that is too small raises with
explicit partials rather than silently returning an incomplete range.

MLB requests one bounded schedule and validates both overall and daily
totalGames. Exact duplicated events are deduplicated; conflicting duplicates
raise. The public gameStatus endpoint confirms that abstractGameState Final
also covers postponed/cancelled games: codedGameState C/D invalidates, F is
final (including completed-early official finals), and O (Game Over before F),
T/U (suspended), live and scheduled are skipped. No final is inferred from score.

## Metrics and provenance

Naver `fields=all` returned `homeTeamRheb`/`awayTeamRheb` for baseball. The first
three positions are exposed as runs/hits/errors, checked against final scores.
For Naver game `20260904SFPI0`, `[5,5,0,8]` and `[2,2,1,6]` match the official MLB
boxscore runs/hits/errors. The fourth value is deliberately omitted: it is not
baseOnBalls alone (the matching MLB batting values are 7 and 5 walks, plus one
hit-by-pitch per side). No unsupported interpretation of that fourth value.

Basketball schedules provide final points and quarter scores; volleyball
provides final sets and per-set point scores. This adapter uses the explicit
final TeamScore fields, not sums of periods. Actual per-game shooting/attack
ratios are not present in these schedule fixtures; metrics are empty and
metric_status is not_available. Season averages from court_info are not reused
as game statistics. The observed KBL sample's quarter sum also illustrates why
the provider's explicit final score should not be reconstructed from periods.

Official MLB enriches the newest five final records with
`/api/v1/game/{gamePk}/boxscore`: **at most six requests total**, independent of
output limit. It verifies native team IDs and batting runs against the schedule.
Only actual `teams.{home,away}.teamStats.batting` counts are copied: runs, hits,
atBats, baseOnBalls, strikeOuts, homeRuns. Rates such as avg/obp/slg/ops in this
endpoint can be cumulative and are not copied as game ratios. No xG, expected
stats, Statcast, predictions, or fabricated zero metrics. Records beyond the
five-boxscore budget keep scores and have empty metrics/not_available. Truly
absent batting stats likewise remain unavailable; a broken boxscore response
or denied HTTP request raises. Output-limit errors occur before enrichment,
so their partial records contain schedule scores only.

Naver source_url points to the native game page. Official MLB source_url points
to the schedule request for score-only records and the actual boxscore endpoint
for enriched records. Raw request/response provenance still belongs to the runner.

## Actual probes and smoke verification (2026-09-05)

All final adapter smoke requests used standard requests.get with timeout=20,
`User-Agent: proto-odds-research/1.0`, and `Accept: application/json`. No Referer,
browser impersonation, authentication, payment, proxy workaround, or retries.
All 23 smoke HTTP calls returned 200. Public Referer was not required.

Current-window Naver smoke, **2026-09-04 KST** (one call per league):

- KBO: 5 finals; MLB: 9 finals; NPB: 5 finals; all with actual R/H/E counts.
- NBA: 0; KBL: 0; WKBL: 0; KOVO남: 0; KOVO여: 0. These are successful empty
  schedules for that exact date. NBA is offseason; no earlier date is substituted.

Separate, explicitly historical demonstration, **2026-02-01 KST**:

- NBA: 6 finals; KBL: 3; WKBL: 1; KOVO남: 1; KOVO여: 1 (one call each).
- Native sample results: NBA 2026013130 CHA 111–SA 106; KBL
  2026020106164701182 team 06 89–team 16 96; WKBL 202602010460160 team 07 43–team
  09 76; KOVO남 20260201022M174 team 1005 3–team 1008 0 sets; KOVO여
  20260201022F175 team 2004 3–team 2007 1 sets.

Additional adapter smoke:

- KBO 2026-08-29..2026-09-04: 30 records, 26 finals + 4 cancellations, one call.
  Observed cancellation 20260830KTSS02026 has statusCode BEFORE, statusNum 0,
  cancel true, 0–0 source placeholder scores, and empty RHEB arrays.
- KBO 2026-09-04 with temporary in-process page size 2: three calls, all 5 finals
  recovered. Page 1 IDs HHLT/KTHT, page 2 NCWO/OBSK, page 3 SSLG (full IDs start
  with 20260904 and end with 02026). Production page size remains 100.
- Official MLB **2026-09-03 officialDate**: 9 finals, 5 enriched boxscores,
  4 score-only records, six calls. Games may kick off September 4 UTC.

Source URLs used for the schema evidence and bounded probes:

- [Naver KBO current day](https://api-gw.sports.naver.com/schedule/games?fields=all&upperCategoryId=kbaseball&categoryId=kbo&fromDate=2026-09-04&toDate=2026-09-04&size=100&page=1)
- [Naver NPB current day](https://api-gw.sports.naver.com/schedule/games?fields=all&upperCategoryId=wbaseball&categoryId=npb&fromDate=2026-09-04&toDate=2026-09-04&size=100&page=1)
- [Naver NBA current day](https://api-gw.sports.naver.com/schedule/games?fields=all&superCategoryId=basketball&categoryId=nba&fromDate=2026-09-04&toDate=2026-09-04&size=100&page=1)
- [Naver NBA historical demonstration](https://api-gw.sports.naver.com/schedule/games?fields=all&superCategoryId=basketball&categoryId=nba&fromDate=2026-02-01&toDate=2026-02-01&size=100&page=1)
- [Naver KBL historical demonstration](https://api-gw.sports.naver.com/schedule/games?fields=all&superCategoryId=basketball&categoryId=kbl&fromDate=2026-02-01&toDate=2026-02-01&size=100&page=1)
- [Naver WKBL historical demonstration](https://api-gw.sports.naver.com/schedule/games?fields=all&superCategoryId=basketball&categoryId=wkbl&fromDate=2026-02-01&toDate=2026-02-01&size=100&page=1)
- [Naver men's volleyball demonstration](https://api-gw.sports.naver.com/schedule/games?fields=all&superCategoryId=volleyball&categoryId=kovo&fromDate=2026-02-01&toDate=2026-02-01&size=100&page=1)
- [Naver women's volleyball demonstration](https://api-gw.sports.naver.com/schedule/games?fields=all&superCategoryId=volleyball&categoryId=wkovo&fromDate=2026-02-01&toDate=2026-02-01&size=100&page=1)
- [Basketball-wide WNBA coverage probe](https://api-gw.sports.naver.com/schedule/games?fields=all&superCategoryId=basketball&fromDate=2026-08-29&toDate=2026-09-04&size=100)
- [Official MLB schedule](https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2026-09-03&endDate=2026-09-03)
- [Official MLB observed boxscore](https://statsapi.mlb.com/api/v1/game/823337/boxscore)
- [Official MLB status vocabulary](https://statsapi.mlb.com/api/v1/gameStatus)

Validation: `python -m pytest tests/test_sports_sources_results.py -q` — 85 passed.
Fixture projections retain observed field names, types and values for all eight
Naver leagues, a real cancellation, and official MLB schedule/boxscore. Synthetic
cases are restricted to error/status/budget/ordering scenarios. No raw artifact
files or observed_at columns were added to the adapter.
