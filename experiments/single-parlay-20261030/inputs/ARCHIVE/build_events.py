"""Private, offline ARCHIVE sidecar. No ticket engine, ROI, network or repo imports.

Run: python .local-test/ARCHIVE/build_events.py
The upstream normalized universe is settled-only, NOT all historically buyable offers.
"""
import gzip
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
SOURCE_ROOT = ROOT.parent / 'proto-all-pick-profitability-20261030'
SOURCE = SOURCE_ROOT / 'experiments/proto-all-picks-20261030/offers.jsonl.gz'
NORMALIZATION = SOURCE.with_name('normalization.json')
NORMALIZER = SOURCE_ROOT / 'scripts/proto_all_pick_research.py'
PROTOCOL = ROOT / 'docs/research/2026-10-30-single-parlay-protocol.json'
MODEL = 'market_proportional_archive_proxy'
KST = timezone(timedelta(hours=9))


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def stable(value, length=None):
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    return digest[:length] if length else digest


def earliest_alias(row):
    # Numeric chronological ordering; preserve actual game_no including leading zeros.
    # Raw game_no breaks any equal-numeric ties deterministically, without outcomes.
    aliases = row['aliases']
    assert aliases and all(len(a) == 3 for a in aliases)
    return min(aliases, key=lambda a: (int(a[0]), int(a[1]), int(a[2]), str(a[2])))


def features(row):
    alias = earliest_alias(row)
    n = row['n_way']
    assert n in (2, 3) and len(row['odds']) == len(row['names']) == n
    assert all(math.isfinite(o) and o > 1 for o in row['odds'])
    inv = [1.0 / o for o in row['odds']]
    total = math.fsum(inv)
    return [dict(offer_id=row['market_id'] + ':' + str(i),
        market_id=row['market_id'], market=row['family'],
        label=row['family'] + (' ' + row['line'] if row['line'] is not None else ''),
        selection=name, side_index_0based=i, line=row['line'], n_way=n,
        issue_year=alias[0], round=alias[1], game_no=alias[2], odds=odds,
        scores={MODEL: inv[i] / total}, probability_models=[MODEL],
        probability_status='unverified_market_implied_probability')
        for i, (name, odds) in enumerate(zip(row['names'], row['odds']))]


def read_jsonl(path):
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def add_count(counter, key, markets, choices):
    counter[key]['frames'] += 1
    counter[key]['physical_markets'] += markets
    counter[key]['choices'] += choices


def main():
    assert OUT.name == 'ARCHIVE' and OUT.parent.name == '.local-test'
    output = OUT / 'events.jsonl.gz'
    assert not output.exists(), 'Do not overwrite an existing sidecar'
    inputs = [SOURCE, PROTOCOL, NORMALIZATION, NORMALIZER]
    before_hashes = {str(p): sha(p) for p in inputs}
    protocol = json.loads(PROTOCOL.read_text(encoding='utf-8'))
    assert protocol['protocol'] == 'single-vs-low-parlay-v1'
    source_rows = {}
    events = defaultdict(list)
    source_status = Counter()
    row_numbers = {}
    alias_changed = multiple_alias = 0
    identity_fields = ('kickoff', 'sport', 'league', 'home', 'away')
    for line_no, row in enumerate(read_jsonl(SOURCE), 1):
        mid = row['market_id']
        assert mid not in source_rows
        physical = [row[k] for k in identity_fields]
        assert stable(physical, 24) == row['event_id']
        assert stable([*physical, row['family'], row['line'], row['n_way']], 24) == mid
        assert row['status'] in ('settled', 'refund')
        assert row['status'] == 'refund' or type(row['winner']) is int and 0 <= row['winner'] < row['n_way']
        source_rows[mid] = row
        row_numbers[mid] = line_no
        events[row['event_id']].append(row)
        source_status[row['status']] += 1
        multiple_alias += len(row['aliases']) > 1
        alias_changed += earliest_alias(row) != [row['season'], row['round'], row['game_no']]
        # Exhaustive outcome-blindness + alias-order invariance verification.
        signature = features(row)
        erased = {k: v for k, v in row.items() if k not in ('winner', 'result', 'status')}
        erased['aliases'] = list(reversed(row['aliases']))
        assert features(erased) == signature

    assert len(source_rows) == 166463
    assert sum(r['n_way'] for r in source_rows.values()) == 389203
    assert len(events) == 44396
    by_date, by_league, by_year = (defaultdict(Counter) for _ in range(3))
    by_league_year = defaultdict(Counter)
    counters = Counter()
    choice_status = Counter()
    max_error = 0.0
    naive_timestamps = 0
    temporary = OUT / 'events.jsonl.gz.partial'
    with temporary.open('xb') as binary:
        with gzip.GzipFile(filename='', fileobj=binary, mode='wb', mtime=0, compresslevel=6) as stream:
            for eid, rows in sorted(events.items(), key=lambda pair: (pair[1][0]['kickoff'], pair[0])):
                first = rows[0]
                assert all(tuple(r[k] for k in identity_fields) == tuple(first[k] for k in identity_fields) for r in rows)
                kickoff = datetime.fromisoformat(first['kickoff'])
                assert kickoff.tzinfo is None
                naive_timestamps += 1
                kickoff = kickoff.replace(tzinfo=KST)
                frame = dict(cohort='historical_market_proxy', event_id=eid,
                    snapshot_id=stable(['historical_market_proxy', eid]),
                    kickoff=kickoff.isoformat(), available_at=(kickoff-timedelta(hours=24)).isoformat(),
                    availability_proven=False,
                    availability_scope='hypothetical_kickoff_minus_24h_not_observed',
                    timezone_assumption='source_naive_ISO_assumed_Asia/Seoul_UTC+09:00',
                    source_kickoff_naive=first['kickoff'], sport=first['sport'],
                    league=first['league'], home=first['home'], away=first['away'],
                    probability_models=[MODEL], model_status='unverified_market_implied_probability',
                    universe_scope='upstream_normalized_settled_only_selection_biased', choices=[])
                for row in sorted(rows, key=lambda r: r['market_id']):
                    for choice in features(row):
                        refund = row['status'] == 'refund'
                        choice.update(hit=None if refund else choice['side_index_0based'] == row['winner'],
                            refund=refund, result_status=row['status'], source_result_label=row['result'],
                            source_file=str(SOURCE), source_row_1based=row_numbers[row['market_id']])
                        frame['choices'].append(choice)
                        choice_status[choice['result_status']] += 1
                stream.write((json.dumps(frame, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n').encode('utf-8'))
                n_choices = len(frame['choices'])
                counters.update(frames=1, physical_markets=len(rows), choices=n_choices)
                add_count(by_date, kickoff.date().isoformat(), len(rows), n_choices)
                add_count(by_league, first['league'], len(rows), n_choices)
                add_count(by_year, str(kickoff.year), len(rows), n_choices)
                add_count(by_league_year, first['league'] + '/' + str(kickoff.year), len(rows), n_choices)

    # Independent readback against original source, Decimal probability reference,
    # exact settlement correspondence and complete one-to-one side coverage.
    seen_events, seen_choices, seen_markets = set(), set(), set()
    readback = Counter()
    for frame in read_jsonl(temporary):
        eid = frame['event_id']
        assert eid not in seen_events
        seen_events.add(eid)
        assert frame['snapshot_id'] == stable(['historical_market_proxy', eid])
        assert frame['cohort'] == 'historical_market_proxy' and frame['availability_proven'] is False
        assert frame['probability_models'] == [MODEL]
        kickoff, available = map(datetime.fromisoformat, (frame['kickoff'], frame['available_at']))
        assert kickoff.utcoffset() == available.utcoffset() == timedelta(hours=9)
        assert kickoff - available == timedelta(hours=24)
        grouped = defaultdict(list)
        for choice in frame['choices']:
            assert choice['offer_id'] not in seen_choices
            seen_choices.add(choice['offer_id'])
            grouped[choice['market_id']].append(choice)
        for mid, choices in grouped.items():
            assert mid not in seen_markets
            seen_markets.add(mid)
            row = source_rows[mid]
            assert row['event_id'] == eid and len(choices) == row['n_way']
            assert frame['kickoff'] == row['kickoff'] + '+09:00'
            assert all(frame[k] == row[k] for k in ('league', 'home', 'away', 'sport'))
            alias = sorted(row['aliases'], key=lambda a: (int(a[0]), int(a[1]), int(a[2]), str(a[2])))[0]
            with localcontext() as ctx:
                ctx.prec = 40
                inv = [Decimal(1) / Decimal(str(o)) for o in row['odds']]
                denominator = sum(inv)
                for i, choice in enumerate(choices):
                    assert choice['side_index_0based'] == i
                    assert choice['offer_id'] == mid + ':' + str(i)
                    assert [choice['issue_year'], choice['round'], choice['game_no']] == alias
                    assert choice['odds'] == row['odds'][i] and choice['selection'] == row['names'][i]
                    assert choice['market'] == row['family'] and choice['line'] == row['line']
                    assert choice['result_status'] == row['status'] and choice['source_result_label'] == row['result']
                    refund = row['status'] == 'refund'
                    assert choice['refund'] == refund and choice['hit'] == (None if refund else i == row['winner'])
                    assert choice['source_row_1based'] == row_numbers[mid]
                    assert choice['probability_models'] == [MODEL] and list(choice['scores']) == [MODEL]
                    error = abs(choice['scores'][MODEL] - float(inv[i] / denominator))
                    max_error = max(max_error, error)
                    assert error < 1e-12
            assert math.isclose(math.fsum(c['scores'][MODEL] for c in choices), 1.0, abs_tol=1e-12)
        readback.update(frames=1, physical_markets=len(grouped), choices=len(frame['choices']))
    assert readback == counters and seen_events == set(events) and seen_markets == set(source_rows)
    assert len(seen_choices) == 389203
    assert before_hashes == {str(p): sha(p) for p in inputs}, 'Read-only inputs changed during export'
    temporary.rename(output)
    upstream = json.loads(NORMALIZATION.read_text(encoding='utf-8'))
    manifest = dict(created_at=datetime.now(timezone.utc).isoformat(), protocol=protocol['protocol'],
        cohort='historical_market_proxy', probability_models=[MODEL],
        probability_definition='p_i=(1/odds_i)/sum_j(1/odds_j) within each complete physical market',
        probability_status='unverified_market_implied_probability; not saved Shin or current operating model',
        source_sha256=before_hashes, source_file_bytes=SOURCE.stat().st_size,
        output_sha256=sha(output), export_code_sha256=sha(__file__),
        counts=dict(counters), source_market_status=dict(source_status), choice_status=dict(choice_status),
        kickoff_date_range=[min(by_date), max(by_date)], counts_by_kst_date=dict(sorted(by_date.items())),
        counts_by_league=dict(sorted(by_league.items())), counts_by_kickoff_year=dict(sorted(by_year.items())),
        counts_by_league_and_kickoff_year=dict(sorted(by_league_year.items())),
        multiple_alias_markets=multiple_alias, earliest_alias_differs_from_primary_row=alias_changed,
        alias_rule='minimum (numeric season, numeric round, numeric game_no, original game_no tie-break); original alias values retained including leading zeros; no outcomes consulted',
        offer_id_rule='source market_id + colon + zero-based side index',
        event_identity_rule='preserve source event_id; validated SHA256(first24) of compact UTF8 JSON [naive kickoff,sport,league,home,away]',
        snapshot_id_rule='full SHA256 compact UTF8 JSON [historical_market_proxy,event_id]; one frame per physical event',
        time_assumption='All source ISO timestamps naive; attach +09:00 without converting clock. available_at=kickoff-24h is hypothetical, never observed.',
        availability_proven=False, pending_unsettled_universe_present=False,
        source_normalization_reported_audit=upstream['audit'],
        source_normalization_reported_input_sha256=upstream['source_sha256'],
        validation=dict(readback_counts=dict(readback), source_hashes_unchanged=True,
            physical_identity_hashes_checked=len(source_rows), outcome_blindness_and_alias_order_tested_markets=len(source_rows),
            unique_offer_ids=len(seen_choices), probability_max_absolute_error_vs_decimal=max_error,
            all_market_probability_vectors_sum_to_one=True, all_actual_settlements_match=True,
            all_choices_and_markets_preserved=True, assumed_kst_frames=naive_timestamps),
        limitations=[
            'Settled-only selection bias: this exact input has no pending/unsettled markets and no refund markets. Do not infer a complete historical buyable universe.',
            'Prior normalization excluded cancellations with unpriced/1.00 sentinel odds, unsettled/unrecognized results, conflicting price/result/state reissues, invalid prices and unsupported shapes. Export does not recover exclusions.',
            'Upstream reported exclusions/counts are copied for context, not independently regenerated from raw archives in this sidecar.',
            'Earliest issued alias is an issue identifier, not proof of earliest price observation or historical availability.',
            'Physical event grouping inherits upstream cleaned-team identity; no new external fixture identity verification.',
            'Market reciprocal probabilities are unverified and uncalibrated; no saved Shin probabilities, saved operating model or tipster input.',
            'Settlement uses upstream actual product-specific result/winner labels; no simulated outcomes or independent official verification.',
            '2023-2026 archive previously exposed; no untouched holdout claimed. No train/test split, ranking, ticket construction or ROI run.',
            'Frames retain all eligible source market choices including half-game families; parent ticket engine owns frozen eligibility/checkpoint/session rules.',
            'Private ignored sidecar only; no tracked edits, commits, PR, network fetches or production changes.'])
    (OUT/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps({k: manifest[k] for k in ('counts','source_market_status','kickoff_date_range','counts_by_kickoff_year','output_sha256','validation')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
