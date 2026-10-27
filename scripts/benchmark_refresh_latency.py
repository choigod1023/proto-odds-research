"""Offline before/after comparison. Never reads or writes a production DB."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from runtime_db import RuntimeDatabase
from prediction_ledger import PredictionLedger, _AppendIndex
from match_api import MatchViews, PreparedMatchResponses
from player_fixture_time import fixture_time

def old_module(name, path, revision, directory):
    source = subprocess.run(['git', 'show', f'{revision}:src/{path}.py'], cwd=ROOT,
                            check=True, capture_output=True).stdout
    filename = Path(directory) / (name + '.py')
    filename.write_bytes(source)
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def timed(call, repeats):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        result = call()
        samples.append(time.perf_counter() - start)
    return result, statistics.median(samples)

def main():
    if os.environ.get("PROODD_DB_PATH"):
        raise SystemExit("Refusing benchmark with a configured production database")
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', default='51959c91')
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='offline-latency-') as folder:
        old_db = old_module('baseline_db', 'runtime_db', args.baseline, folder)
        old_ledger = old_module('baseline_ledger', 'prediction_ledger', args.baseline, folder)
        old_views = old_module('baseline_views', 'match_api', args.baseline, folder)
        db = RuntimeDatabase(Path(folder) / 'test.sqlite3')
        before_db = old_db.RuntimeDatabase(db.path)
        player = json.loads((ROOT / 'data/raw/player_info.json').read_text(encoding='utf-8'))
        db.put_document('player_info', player)
        requests = []
        for row in player.get('games', []):
            stamp = fixture_time(row.get('game_datetime'), row.get('league'), row.get('source'))
            if stamp:
                requests.append((row.get('league'), row.get('home_team'), row.get('away_team'), stamp.isoformat()))
        requests = list(dict.fromkeys(requests))[:100]
        results = {'baseline': args.baseline, 'repeats': args.repeats,
                   'player_source_games': len(player.get('games', [])), 'requested_fixtures': len(requests)}
        b, bt = timed(lambda: before_db.player_fixtures_document(requests), args.repeats)
        a, at = timed(lambda: db.player_fixtures_document(requests), args.repeats)
        assert a == b
        results['fixture_lookup'] = {'before_s': bt, 'after_s': at, 'equal': True}
        records = PredictionLedger(ROOT / 'data/raw/prediction_ledger/pregame.jsonl').records()
        template = next(r for r in records if r['record_type'] == 'prediction')
        cores = [{**template, 'snapshot_id': f'benchmark-{n}', 'event_id': f'benchmark-{n}',
                  'deduplication_key': f'benchmark-{n}'} for n in range(250)]
        clock = lambda: datetime(2026, 10, 27, tzinfo=timezone.utc)
        def append(cls, indexed):
            ledger = cls(Path(folder)/'not-written.jsonl', clock=clock)
            staged = list(records)
            if indexed:
                ledger._batch_index = _AppendIndex(staged)
            for core in cores:
                ledger._append_to_records(staged, core)
            return staged[len(records):]
        b, bt = timed(lambda: append(old_ledger.PredictionLedger, False), args.repeats)
        a, at = timed(lambda: append(PredictionLedger, True), args.repeats)
        assert a == b
        results['ledger_append'] = {'history_rows': len(records), 'new_rows': len(cores),
                                   'before_s': bt, 'after_s': at, 'equal_including_hashes': True}
        picks = json.loads((ROOT / 'docs/data/picks_v2.json').read_text(encoding='utf-8'))
        db.store_artifact('picks_v2', picks)
        def views(module):
            view = module.MatchViews(db)
            ready = module.PreparedMatchResponses(view)
            ready.requested.add('all')
            ready.refresh()
            return ({k: json.loads(ready.get_bytes(k, compressed=False)) for k in ready.requested}, view.games)
        import match_api
        b, bt = timed(lambda: views(old_views), args.repeats)
        a, at = timed(lambda: views(match_api), args.repeats)
        assert a == b
        results['web_views_control'] = {'games': len(a[1]), 'before_s': bt, 'after_s': at, 'equal': True}
        print(json.dumps(results, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
