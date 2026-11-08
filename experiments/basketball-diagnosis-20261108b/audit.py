"""Summarize the captured API projection, not the operating ledger."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

LEAGUES = {'NBA', 'KBL', 'WKBL'}


def time(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('timezone required')
    return result


def audit(data):
    now = time(data['generated_at'])
    groups = defaultdict(Counter)
    seen = set()
    for row in data['result_games']:
        if row.get('sport') != 'bk' or row.get('league') not in LEAGUES:
            continue
        pred = row.get('prediction_record') or {}
        try:
            kickoff, captured = time(pred['kickoff']), time(pred['captured_at'])
        except (KeyError, TypeError, ValueError):
            groups['excluded']['missing_or_invalid_time'] += 1
            continue
        identity = pred.get('prediction_snapshot_id')
        if not identity or identity in seen:
            groups['excluded']['missing_or_duplicate_snapshot_id'] += 1
            continue
        seen.add(identity)
        for days in (7, 14, 30):
            if not now - timedelta(days=days) <= kickoff <= now:
                continue
            prefix = f'{days}d/{row["league"]}'
            timing = 'pre_t30' if captured < kickoff - timedelta(minutes=30) else 'late_or_t30'
            market = pred.get('market') or 'missing'
            result = pred.get('result') or 'missing'
            groups[f'{prefix}/projection/{market}/{timing}'][result] += 1
            results = pred.get('market_recommendation_results') or {}
            for item in (pred.get('market_recommendations') or {}).get('items') or []:
                outcome = results.get(item.get('offer_id')) or {}
                key = f'{prefix}/market/{item.get("market", "missing")}/{item.get("model", "missing")}/{timing}'
                groups[key][outcome.get('result') or 'missing'] += 1
    return {key: dict(sorted(counts.items())) for key, counts in sorted(groups.items())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    data = json.loads(raw)
    summary = {'source_sha256': hashlib.sha256(raw).hexdigest(),
               'generated_at': data['generated_at'],
               'scope': 'API result_games projection; not an operating-ledger cohort',
               'groups': audit(data)}
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
