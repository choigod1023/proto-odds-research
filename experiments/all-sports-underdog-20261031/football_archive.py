"""Normalize the external football archive without using derived match features."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from research_chronology import DateWitnesses

FIELDS = ('Division', 'MatchDate', 'HomeTeam', 'AwayTeam', 'FTHome', 'FTAway',
          'FTResult', 'OddHome', 'OddDraw', 'OddAway')


def number(value):
    try:
        result = Decimal(value.strip())
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, AttributeError):
        return None


def numeric_signature(value):
    parsed = number(value)
    return ('number', parsed) if parsed is not None else ('invalid', value.strip())


def fixture(row):
    league, day, home, away = (row[k].strip() for k in FIELDS[:4])
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except ValueError:
        day = ''
    if not all((league, day, home, away)) or home.casefold() == away.casefold():
        return None
    return league, day, home, away


def parse(row):
    errors = []
    key = fixture(row)
    if not key:
        errors.append('invalid_fixture')
    scores = [number(row[k]) for k in ('FTHome', 'FTAway')]
    if any(x is None or x < 0 or x != x.to_integral_value() for x in scores):
        errors.append('invalid_score')
    result = row['FTResult'].strip().upper()
    if result not in ('H', 'D', 'A'):
        errors.append('invalid_result')
    elif 'invalid_score' not in errors:
        expected = 'H' if scores[0] > scores[1] else 'A' if scores[0] < scores[1] else 'D'
        if result != expected:
            errors.append('result_score_mismatch')
    odds = [number(row[k]) for k in ('OddHome', 'OddDraw', 'OddAway')]
    if any(x is None or x <= 1 or not math.isfinite(float(x)) or float(x) <= 1 for x in odds):
        errors.append('invalid_odds')
    elif not Decimal('1') <= sum(Decimal(1) / x for x in odds) <= Decimal('1.40'):
        errors.append('invalid_margin')
    return key, result, odds, errors


def run(source, output, audit_path, *, date_witnesses=None, allow_unverified_chronology=False):
    source, output, audit_path = map(Path, (source, output, audit_path))
    if len({p.resolve() for p in (source, output, audit_path)}) != 3:
        raise ValueError('source, output and audit must be distinct paths')
    sha = hashlib.sha256()
    with source.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            sha.update(block)
    witnesses = DateWitnesses(date_witnesses, source.read_bytes()) if date_witnesses is not None else None
    chronology_counts = Counter()
    corrections = []
    groups = defaultdict(list)
    reasons, exclusions = Counter(), Counter()
    raw_years, raw_leagues = Counter(), Counter()
    raw_days = []
    total = 0
    with source.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not set(FIELDS).issubset(reader.fieldnames):
            raise ValueError('missing required CSV columns')
        for row in reader:
            total += 1
            row = {k: (row.get(k) or '').strip() for k in FIELDS}
            key, result, odds, errors = parse(row)
            reasons.update(errors)
            if row['Division']:
                raw_leagues[row['Division']] += 1
            try:
                d = date.fromisoformat(row['MatchDate'])
                if d.isoformat() == row['MatchDate']:
                    raw_days.append(row['MatchDate'])
                    raw_years[str(d.year)] += 1
            except ValueError:
                pass
            if key is None:
                exclusions['invalid_rows'] += 1
                continue
            signature = tuple(numeric_signature(row[k]) for k in ('FTHome', 'FTAway', 'OddHome', 'OddDraw', 'OddAway')) + (result,)
            groups[key].append((signature, result, odds, errors, row))
    records = []
    for key, rows in groups.items():
        if len({r[0] for r in rows}) > 1:
            exclusions['conflicting_fixture_rows'] += len(rows)
            continue
        if rows[0][3]:
            exclusions['invalid_rows'] += len(rows)
            continue
        # Resolve source conflicts BEFORE correcting dates, so a bad duplicate
        # cannot be left at the original date while its valid twin is moved.
        status = witnesses.assess(rows[0][4]) if witnesses else None
        if status is None or status['status'] == 'unverified':
            chronology_counts['unverified'] += 1
            if not allow_unverified_chronology:
                exclusions['unverified_fixture_date'] += len(rows)
                continue
        elif status['status'] == 'contradicted':
            chronology_counts['contradicted_corrected'] += 1
            corrections.append({'source_fixture': list(key), **status})
            key = (key[0], status['verified_day'], key[2], key[3])
        else:
            chronology_counts['confirmed'] += 1
        exclusions['identical_duplicate_rows'] += len(rows) - 1
        league, day, home, away = key
        records.append({'id': 'extfoot|' + '|'.join(quote(x, safe='') for x in key),
                        'cohort': 'external_football_archive', 'sport': 'sc', 'league': league,
                        'day': day, 'kickoff': day + 'T00:00:00', 'kickoff_precision': 'date',
                        'home': home, 'away': away, 'odds': [float(x) for x in rows[0][2]],
                        'y': {'H': 0, 'D': 1, 'A': 2}[rows[0][1]],
                        'chronology_status': 'independent_date_witness' if status and status['status'] != 'unverified' else 'unverified_diagnostic',
                        'chronology_witness_id': status['witness_id'] if status else None,
                        'original_day': rows[0][4]['MatchDate'],
                        'sources': ['external_matches_csv']})
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('date correction creates a duplicate fixture; resolve witnesses first')
    records.sort(key=lambda x: (x['day'], x['id']))
    output.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with output.open('wb') as f, gzip.GzipFile(filename='', mode='wb', fileobj=f, mtime=0) as gz:
        for record in records:
            gz.write((json.dumps(record, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8'))
    accepted_days = [r['day'] for r in records]
    audit = {'source': str(source), 'source_sha256': sha.hexdigest(), 'all_raw_rows': total,
             'accepted_rows': len(records), 'excluded_rows': total - len(records),
             'exclusions': dict(sorted(exclusions.items())), 'validation_issues_overlapping': dict(sorted(reasons.items())),
             'raw_date_range': [min(raw_days), max(raw_days)] if raw_days else None,
             'accepted_date_range': [min(accepted_days), max(accepted_days)] if accepted_days else None,
             'raw_by_league': dict(sorted(raw_leagues.items())), 'raw_by_year': dict(sorted(raw_years.items())),
             'accepted_by_league': dict(sorted(Counter(r['league'] for r in records).items())),
             'accepted_by_year': dict(sorted(Counter(r['day'][:4] for r in records).items())),
             'limitations': ['MatchDate is a timezone-free source date; kickoff is a date-only placeholder, not verified UTC or an actual time.',
                             'MatchTime is unused; source clock, independent result witness, odds observation time and price availability are unverified.'],
             'duplicate_policy': 'All rows in a fixture with differing score, result or odds are excluded, including invalid rows; identical duplicates retain one valid row.',
             'issue_counts_note': 'Validation issues can overlap and include conflicting fixtures; exclusions are mutually exclusive row counts.'}
    audit.update(chronology_policy='explicit_unverified_diagnostic' if allow_unverified_chronology else 'independent_fixture_dates_only',
                 chronology_counts=dict(chronology_counts), date_corrections=corrections,
                 chronology_scope='Exact archive SHA, division, source date, teams and score; no generic month/day swap',
                 pregame_receipts_verified=False, execution_prices_verified=False)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--audit', required=True)
    parser.add_argument('--date-witnesses', type=Path)
    parser.add_argument('--allow-unverified-chronology', action='store_true',
                        help='Explicit retrospective diagnostic only; not validation certification')
    args = parser.parse_args()
    registry = json.loads(args.date_witnesses.read_text(encoding='utf-8')) if args.date_witnesses else None
    summary = run(args.source, args.output, args.audit, date_witnesses=registry,
                  allow_unverified_chronology=args.allow_unverified_chronology)
    print(json.dumps({k: summary[k] for k in ('accepted_rows', 'chronology_policy', 'chronology_counts')}))
    if not summary['accepted_rows'] and not args.allow_unverified_chronology:
        parser.exit(2, 'No independently witnessed fixtures: chronology validation blocked.\n')


if __name__ == '__main__':
    main()
