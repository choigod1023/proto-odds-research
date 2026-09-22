"""Read-only cache benchmark. Run each variant in a fresh process.

py scripts/benchmark_match_cache.py --base origin/main --trace
py scripts/benchmark_match_cache.py --trace
Omit --trace for timings without allocation-tracing overhead.
"""
import argparse
import gc
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import tracemalloc
import types
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
parser = argparse.ArgumentParser()
parser.add_argument('--base')
parser.add_argument('--trace', action='store_true')
parser.add_argument('--collector', action='store_true', help='Measure consecutive full-document loads')
parser.add_argument('--input', type=Path, default=ROOT/'docs/data/picks_v2.json')
args = parser.parse_args()
if args.collector:
    tracemalloc.start()
    previous = json.loads(args.input.read_text(encoding='utf-8'))
    if not args.base:
        del previous
    latest = json.loads(args.input.read_text(encoding='utf-8'))
    retained, peak = tracemalloc.get_traced_memory()
    print(json.dumps({'variant': args.base or 'optimized', 'collector_load_only': True,
                      'input_bytes': args.input.stat().st_size,
                      'retained_bytes': retained, 'peak_bytes': peak}))
    sys.exit(0)
if args.base:
    source = subprocess.check_output(['git', 'show', f'{args.base}:src/match_api.py'], cwd=ROOT)
    module = types.ModuleType('baseline_match_api')
    exec(compile(source, 'baseline_match_api.py', 'exec'), module.__dict__)
else:
    import match_api as module


class Database:
    revision = 'benchmark'

    @contextmanager
    def connect(self):
        yield self

    def execute(self, *args):
        return self

    def fetchone(self):
        return {'stored_at': self.revision}

    def get_artifact_json(self, name):
        return args.input.read_text(encoding='utf-8'), self.revision


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


if args.trace:
    tracemalloc.start()
database = Database()
views = module.MatchViews(database)
start = time.perf_counter()
recent = views.get('recent')
cold = time.perf_counter() - start
all_games = views.get('all')
row = next(iter(all_games.get('live') or all_games['past']))
start = time.perf_counter()
detail = views.get('detail', row['_detail_key'], 'benchmark')
detail_time = time.perf_counter() - start
start = time.perf_counter()
for _ in range(100):
    views.get('recent')
warm = (time.perf_counter() - start) / 100
gc.collect()
memory = tracemalloc.get_traced_memory() if args.trace else None
hashes = {'recent': digest(recent), 'all': digest(all_games), 'detail': digest(detail)}
# Drop simulated in-flight responses before measuring an artifact revision change.
del recent, all_games, row, detail
if args.trace:
    tracemalloc.reset_peak()
database.revision = 'next'
views.get('recent')
refresh_memory = tracemalloc.get_traced_memory() if args.trace else None
print(json.dumps({'variant': args.base or 'optimized', 'input_bytes': args.input.stat().st_size,
                  'traced': args.trace, 'cold_seconds': cold, 'detail_seconds': detail_time,
                  'warm_seconds': warm, 'retained_peak_bytes': memory,
                  'refresh_retained_peak_bytes': refresh_memory, 'hashes': hashes}, indent=2))
