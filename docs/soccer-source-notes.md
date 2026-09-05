# Soccer source adapters

## Integration contract

Only `src/sports_sources_soccer.py` and its offline tests implement adapter work.
The legacy `src/fotmob_xg.py` is unchanged. Its embedded `__NEXT_DATA__` and
`content.stats.Periods.All` parsing approach is reused in independent pure parsing
functions, because importing the legacy module also imports `requests` and
`runtime_db`. Its `_get`, retry loop, file writer and DB code are never called.

The synchronous callback has exactly one positional argument:

```python
fetch(url: str) -> str

collect_fotmob(fetch, league='kleague1', *, since: date, until: date, limit: int)
collect_statsbomb(fetch, competition: int, season: int, limit: int,
                 since: date | None = None, until: date | None = None)
```

The callback owns HTTP status checks, byte/time limits, pacing, and raw response
persistence. It must raise on errors/challenges, make no retries, and disable
redirects or validate each destination against the same public-source policy.
No CAPTCHA solving, proxy, browser impersonation, or disallowed API fallback.
The adapter makes at most **one listing request plus `limit` detail requests**,
and returns at most `limit` records. `limit=0` makes zero requests. The listing is
a fixed overhead, so `limit=1` allows the requested one-page/one-match smoke.
Skipped details never cause additional replacement requests. Failures propagate;
there is no silent catch or retry. Invalid limits/ranges fail before network I/O.

FotMob leagues: `kleague1`, `kleague2`, `j1`, `j2`. Dates are inclusive UTC dates.
Only the current public league page is scanned: no season pagination or claim of
complete historical coverage. Fixtures are deduplicated by native ID and ordered
by UTC kickoff descending before applying the detail budget. Both the fixture
and match header must say finished; cancelled, awarded, abandoned and postponed
records are excluded. Match ID and available league identity are cross-checked.
A public matchup URL can later resolve to another meeting: mismatch raises
instead of attaching that meeting's xG to the original fixture.

Output uses native provider IDs as strings, `sport='축구'`, `status='final'`,
integer team scores and `score_unit='goals'`. FotMob `league` is the adapter key;
StatsBomb `league` is the competition ID string (e.g. `'43'`), with integer
`competition`, `season`, and readable `competition_name`, `season_name` metadata.
No alias matching is performed. `source_url` identifies the detail body;
`listing_url` identifies the other body needed to reproduce a record.

FotMob `kickoff_at` is an aware UTC ISO timestamp; `game_date` is its UTC date and
`time_precision='instant'`. A missing timezone raises instead of being guessed.
StatsBomb's documented `kick_off` has no timezone: output has `kickoff_at=None`,
the unchanged `match_date` as `game_date`, `time_precision='date'`, and the unchanged
local `kick_off` as `source_kickoff`. No next-day timestamp is fabricated.

The main runner owns `observed_at`, raw bodies, append-only event versions,
end-of-date UTC ordering for date-only records, and exclusion of versions observed
after `as_of`. The agreed query is
`recent_team_games(provider, league, team_id, as_of, limit)`, using native source
IDs. This adapter does not implement that query or automatically join data into
production models. Example query identity: StatsBomb / `'43'` / `'779'`.

## Metric meanings and missingness

FotMob `metric_scope='provider_match_all_periods'`: only provider `All` team
`expected_goals` and `expected_goals_non_penalty` fields become xG and npxG.
Half-specific stats, scores, shot counts and coverage labels cannot produce xG.

StatsBomb `metric_scope='shot_statsbomb_xg_periods_1_to_4'`: sum each team's
`shot.statsbomb_xg` in periods 1-4, including extra time. Period 5 shootout shots
are excluded. npxG also excludes shot type `Penalty` (ID 88). Unknown shot type
prevents an npxG total; missing xG on any team shot prevents a misleading partial
xG sum for that team. A team with no observed shots is conservatively missing,
because an empty/truncated event body is not proof of measured zero. Explicit
provider zero values are retained. Duplicate shot IDs and invalid values raise.

Missing metrics are omitted, never set to zero. Records without any available
team xG carry `metrics={}`, `metric_status='not_available'`, and retain their
final score. One-sided xG is `partial`; xG for both teams is `available` (npxG may
still be absent). Scores and xG retain their independent meanings.

Every StatsBomb record has `sample_scope='historical_open_data'`. Competition and
season must be explicit positive integer IDs; there are no default live sources.
Only `match_status='available'` entries are accepted, with final score fields.

## Primary-source review (2026-09-05)

[FotMob robots.txt](https://www.fotmob.com/robots.txt) currently allows the root
path for the general user agent and explicitly disallows `/api/*`, `/auth/*`,
`/info`, `/health`, and `/contact_us`. The special allowances for named search bots
are not used. Only public league and match HTML is requested. Robots rules are
access instructions, not a blanket copyright or reuse license.

The canonical [StatsBomb Open Data repository](https://github.com/hudl/open-data)
(the old `statsbomb/open-data` URL redirects there) describes selected competition
seasons for research and football analytics, with match and event JSON files.
Its [README](https://github.com/hudl/open-data/blob/master/README.md) asks for
StatsBomb attribution and logo when publishing analysis. The
[license](https://github.com/hudl/open-data/blob/master/LICENSE.pdf), last updated
8 September 2023 in the retrieved document, limits the service to research and
analysis; it restricts commercial exploitation and redistribution of the data.
These diagnostics do not establish permission for commercial production use.
Raw provider bodies are not committed with this adapter. Review the license
before publishing analyses or sharing provider data.

The [matches specification](https://github.com/hudl/open-data/blob/master/doc/Open%20Data%20Matches%20v3.0.0.pdf)
defines `match_date`, timezone-less `kick_off`, final scores, and collection status
`available`. The [events specification](https://github.com/hudl/open-data/blob/master/doc/Open%20Data%20Events%20v4.0.0.pdf)
defines shot xG and the period mapping (5 = shootout). The adapter follows these
definitions and the actual diagnostic payloads, not an unofficial API wrapper.

## Actual bounded network smoke (2026-09-05)

All calls were read-only GETs using Python's standard HTTP client, an explicit
`soccer-source-diagnostic/1.0` user agent, no proxy, redirects disabled, a
20-second socket timeout, and an 8,000,000-byte response cap (read at most cap+1
bytes to detect overflow). No retries, authentication or repository clone.
The PDF/README/robots documentation reads were separate from the data budgets.

FotMob: exactly one listing and one latest finished match HTML request, both 200:

- [K League 1 listing](https://www.fotmob.com/leagues/9080/matches/k-league-1):
  847,893 bytes. Nested listing walk found 465 entries before deduplication.
- [Gangwon FC vs Pohang Steelers](https://www.fotmob.com/matches/pohang-steelers-vs-gangwon-fc/h9asld4):
  985,094 bytes. Match `5140017`, `2026-08-30T10:30:00.000Z`, finished and not
  cancelled; native IDs `164734` / `109373`, score 0-0. All-period provider xG
  0.81 / 0.89 and npxG 0.81 / 0.89. Both page and fixture IDs agreed.

This FotMob network check directly extracted and inspected the embedded fixture,
general, header and All-period stats before implementing the adapter. It was not
repeated after implementation. Offline contract tests exercise the adapter using
the inspected payload shape, including the observed metric values; the committed
fixtures are synthetic and not raw provider HTML.

StatsBomb: `collect_statsbomb(fetch, 43, 106, 1)` ran against the network after
implementation, exactly two requests, both 200:

- [World Cup 2022 match index](https://raw.githubusercontent.com/hudl/open-data/master/data/matches/43/106.json):
  119,132 bytes; 0.234 seconds.
- [Match 3869685 events](https://raw.githubusercontent.com/hudl/open-data/master/data/events/3869685.json):
  3,757,121 bytes; 0.688 seconds. Latest sample Argentina vs France, December 18,
  2022; IDs `779` / `771`; score 3-3; `source_kickoff='17:00:00.000'` and
  `kickoff_at=None`. Adapter xG: 2.758305926 / 2.272617949; npxG:
  1.974805926 / 0.705617949. Shootout excluded; historical scope present.

HTTP 403 stop-without-retry behavior is covered offline for listing and detail
requests for both providers; no actual 403 occurred in these successful smokes.

Run offline tests with:

```console
python -m unittest discover -s tests -p test_sports_sources_soccer.py -v
```
