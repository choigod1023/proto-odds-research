"""Replay an explicitly supplied read-only DB extract; never contact production."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import snapshot


def audit(path):
    blob = path.read_bytes()
    data = json.loads(gzip.decompress(blob))
    previous = {}
    counts = Counter()
    examples = []
    raw_rows = 0
    for state in data['states']:
        row, observations = state['row'], state['observations']
        raw_rows += len(observations)
        record = {k: row.get(k) for k in snapshot.FIELDS}
        record.update(year=row['season'], ts=observations[0]['observed_at'],
                      odds=','.join(f'{float(price):.2f}' for price in json.loads(row['odds_json'])))
        key = (row['season'], row['round'], row['game_no'])
        before = previous.get(key)
        if before:
            change = snapshot._snapshot_change(before['record'], record)
            if change:
                counts[change['change_type']] += 1
                if change['line_changed'] and not change['price_changed'] and len(examples) < 5:
                    examples.append(dict(product=list(key), market=row['market_family'],
                        home=row['home'], away=row['away'], old_label=before['record']['market_label'],
                        new_label=row['market_label'], odds=record['odds'],
                        previous_last_observed_at=before['last_observed_at'],
                        first_new_observed_at=observations[0]['observed_at'],
                        old_detector_changed=False, new_detector_changed=True))
        previous[key] = dict(record=record, last_observed_at=observations[-1]['observed_at'])
    if raw_rows != data['meta']['raw_rows']:
        raise ValueError('source row count differs from manifest')
    return dict(source_sha256=hashlib.sha256(blob).hexdigest(), source_file=str(path),
                source_meta=data['meta'], observed_products=len(previous),
                transition_counts=dict(counts), old_detector_missed_line_only=counts['line'],
                examples=examples)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--slice', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = audit(args.slice)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=True))
