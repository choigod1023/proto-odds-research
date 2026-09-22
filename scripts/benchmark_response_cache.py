"""Compare warm response preparation on identical data, excluding DB/network time."""
from contextlib import contextmanager
import gzip
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from match_api import MatchViews


class FixtureDatabase:
    @contextmanager
    def connect(self):
        yield self

    def execute(self, *args):
        return self

    def fetchone(self):
        return {'stored_at': 'benchmark'}

    def get_artifact_json(self, name):
        return (ROOT/'docs/data/picks_v2.json').read_text(encoding='utf-8'), 'benchmark'


views = MatchViews(FixtureDatabase())
for scope in ('recent', 'all'):
    views.get_bytes(scope)
    timings = {}
    for variant in ('old_serialize_compress', 'prepared_response'):
        samples = []
        for _ in range(100):
            start = time.perf_counter()
            if variant == 'old_serialize_compress':
                body = gzip.compress(json.dumps(views.get(scope), ensure_ascii=False,
                                                 separators=(',', ':')).encode(), compresslevel=3)
            else:
                body = views.get_bytes(scope)
            samples.append((time.perf_counter()-start)*1000)
        timings[variant] = {'mean_ms': statistics.mean(samples), 'max_ms': max(samples)}
    assert json.loads(gzip.decompress(body)) == views.get(scope)
    print(json.dumps({'scope': scope, 'requests_per_variant': 100,
                      'compressed_bytes': len(body), 'timings': timings}))
print(json.dumps({'total_wire_cache_bytes': sum(map(len, views.wire_cache.values()))}))
