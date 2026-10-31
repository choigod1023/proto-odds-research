"""Fail-closed, offline date witnesses for retrospective research archives.

An ISO-shaped date is not an independently verified fixture date. Witnesses
are manually reviewed facts, not pregame availability or executable prices.
No network, fuzzy team matching, global month/day swap, or production writes.
"""
from __future__ import annotations

from collections import Counter
import csv
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('date must be canonical ISO day')
    return value


def _score(value):
    score = Decimal(str(value))
    if not score.is_finite() or score < 0 or score != score.to_integral_value():
        raise ValueError('invalid score')
    return int(score)


def fixture_key(row):
    if any(not isinstance(row[k], str) or not row[k].strip() or row[k] != row[k].strip()
           for k in ('Division', 'HomeTeam', 'AwayTeam')):
        raise ValueError('fixture identity must be explicit and canonical')
    if row['HomeTeam'].casefold() == row['AwayTeam'].casefold():
        raise ValueError('fixture teams must differ')
    return (row['Division'], _day(row['MatchDate']), row['HomeTeam'],
            row['AwayTeam'], _score(row['FTHome']), _score(row['FTAway']))


class DateWitnesses:
    def __init__(self, registry, source_bytes):
        digest = hashlib.sha256(source_bytes).hexdigest()
        if registry.get('source_sha256') != digest:
            raise ValueError('archive differs from witnessed source')
        self.source_sha256 = digest
        self.witnesses = {}
        identifiers = set()
        for witness in registry['witnesses']:
            identifier = witness.get('id')
            if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                raise ValueError('missing or duplicate witness id')
            identifiers.add(identifier)
            key = fixture_key(witness['source_fixture'])
            if key in self.witnesses:
                raise ValueError('duplicate or conflicting date witness')
            _day(witness['confirmed_day'])
            stamp = datetime.fromisoformat(witness['checked_at_utc'].replace('Z', '+00:00'))
            if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
                raise ValueError('witness needs a UTC check timestamp')
            if date.fromisoformat(witness['confirmed_day']) > stamp.date():
                raise ValueError('result witness cannot precede the fixture')
            if witness.get('kind') != 'official_fixture_result':
                raise ValueError('not a fixture-result date witness')
            for field in ('official_home', 'official_away', 'identity_check'):
                if not witness.get(field):
                    raise ValueError('missing explicit fixture identity evidence')
            urls = witness.get('urls', [])
            if not urls or any(urlparse(u).scheme != 'https' or
                               urlparse(u).hostname not in {'www.fotball.no', 'www.hamkam.no'}
                               for u in urls):
                raise ValueError('unapproved witness source')
            if witness.get('observation_method') != 'web_tool_rendered_text':
                raise ValueError('unsupported observation method')
            self.witnesses[key] = witness

    def assess(self, row):
        key = fixture_key(row)
        witness = self.witnesses.get(key)
        source_day = row['MatchDate']
        if witness is None:
            return {'source_day': source_day, 'verified_day': None,
                    'status': 'unverified', 'witness_id': None}
        actual = witness['confirmed_day']
        return {'source_day': source_day, 'verified_day': actual,
                'status': 'confirmed' if source_day == actual else 'contradicted',
                'witness_id': witness['id']}

    def require_verified(self, rows):
        """Return dated copies only when every row has an independent witness."""
        checked = []
        for row in rows:
            item = self.assess(row)
            if item['status'] == 'unverified':
                raise ValueError('unverified fixture chronology: strict model comparison blocked')
            checked.append({**row, 'MatchDate': item['verified_day'],
                            'chronology_witness': item['witness_id'],
                            'original_MatchDate': row['MatchDate']})
        if not checked:
            raise ValueError('empty chronology cohort')
        return checked


def audit_archive(source: Path, registry: dict):
    payload = source.read_bytes()
    witnesses = DateWitnesses(registry, payload)
    counts, per_league = Counter(), {}
    findings = []
    with source.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            league = per_league.setdefault(row['Division'], Counter())
            try:
                day = date.fromisoformat(row['MatchDate'])
                if day.day <= 12 and day.month != day.day:
                    counts['month_day_ambiguous'] += 1
                    league['month_day_ambiguous'] += 1
            except ValueError:
                pass
            try:
                result = witnesses.assess(row)
            except (ValueError, ArithmeticError, KeyError):
                counts['invalid_fixture'] += 1
                league['invalid_fixture'] += 1
                continue
            counts[result['status']] += 1
            league[result['status']] += 1
            # An ambiguity flag is NOT a finding that the date is wrong.
            day = date.fromisoformat(row['MatchDate'])
            if result['status'] != 'unverified':
                findings.append({'fixture': list(fixture_key(row)), **result,
                                 'shift_days': (date.fromisoformat(result['verified_day']) - day).days})
    total = sum(counts[k] for k in ('confirmed', 'contradicted', 'unverified', 'invalid_fixture'))
    return {'source_sha256': witnesses.source_sha256, 'rows': total, 'counts': dict(counts),
            'by_league': {k: dict(v) for k, v in sorted(per_league.items())},
            'findings': findings,
            'strict_chronology_ready': bool(total) and not any(counts[k] for k in ('unverified', 'invalid_fixture', 'contradicted')),
            'witnessed_rebuild_ready': bool(total) and not counts['unverified'] and not counts['invalid_fixture'],
            'pregame_receipts_verified': False, 'execution_prices_verified': False,
            'note': 'Unverified is not proven incorrect. Ambiguous dates are never automatically swapped.'}


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--registry', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve() in {args.source.resolve(), args.registry.resolve()}:
        raise ValueError('output must not overwrite inputs')
    report = audit_archive(args.source, json.loads(args.registry.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'counts': report['counts'], 'strict_chronology_ready': report['strict_chronology_ready']}))


if __name__ == '__main__':
    main()
