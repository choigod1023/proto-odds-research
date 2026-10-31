import datetime as dt
import gzip
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_archived_fixture_receipts_and_prices_are_not_prospective():
    with gzip.open(ROOT / 'data/model_candidates/mlb/parity-fixtures.jsonl.gz', 'rt', encoding='utf-8') as f:
        rows = [json.loads(line) for line in f]
    parse = lambda s: dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
    assert len(rows) == len({r['snapshot']['gamePk'] for r in rows}) == 310
    assert all(parse(r['snapshot']['collected_at']) > parse(r['decision_at']) for r in rows)
    assert all(r['odds'].get('observed_at') is None for r in rows)
    assert all(r['snapshot']['source_kind'] == 'official_historical_timecode_replay' for r in rows)
    assert all(r.get('labels') for r in rows)

def test_comparison_file_contains_no_mlb_event():
    comparison = json.loads((ROOT / 'experiments/operating-ledger-comparison-20261029/comparison.json').read_text(encoding='utf-8'))
    assert len(comparison['events']) == 146
    assert not [e for e in comparison['events'] if e['fixture'].get('league') == 'MLB']
