"""Local opt-in collection: explicit article URLs, at most five per invocation."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from public_pregame_sources import fetch_article, link_claims


def run(entries):
    if not isinstance(entries, list) or not 1 <= len(entries) <= 5:
        raise ValueError('manifest must contain 1..5 articles')
    documents, records, failures = [], [], []
    for i, entry in enumerate(entries):
        if i:
            time.sleep(2)
        try:
            document = fetch_article(entry['url'])
            linked, status = link_claims(document, entry.get('fixture', {}))
            document['link_status'] = status
            documents.append(document)
            records.extend(linked)
        except Exception as exc:
            # Never print requests, cookies, credentials or unbounded error bodies.
            failures.append({'index': i, 'error_type': type(exc).__name__,
                             'reason': str(exc) if isinstance(exc, ValueError) else 'fetch_failed'})
    groups = {}
    for row in records:
        groups.setdefault(row['content_sha256'], set()).add(row['source'])
    for row in records:
        row['same_text_sources'] = sorted(groups[row['content_sha256']])
        row['independent_source_count'] = None
    return {'documents': documents, 'records': records, 'failures': failures,
            'monthly_loss_limit_krw': 50000, 'roi_delta': None,
            'simulation_status': 'not_run', 'affects_probability': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists; snapshots must not be overwritten')
    if args.manifest.stat().st_size > 64_000:
        parser.error('manifest too large')
    result = run(json.loads(args.manifest.read_text(encoding='utf-8')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({'documents': len(result['documents']), 'records': len(result['records']),
                      'failures': result['failures'], 'roi_delta': None}))
