"""Local synthetic allocation check. Never opens the production database."""
import gc
import gzip
import json
from pathlib import Path
import sys
import tempfile
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from artifact_responses import ArtifactResponses
from runtime_db import RuntimeDatabase


def measure(fn):
    gc.collect()
    tracemalloc.start()
    result = fn()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, {'retained_python_bytes': current, 'peak_python_bytes': peak}


def main():
    with tempfile.TemporaryDirectory() as directory:
        db = RuntimeDatabase(Path(directory) / 'synthetic.db')
        games = [dict(sport='bs', date='09.22 18:00', home=f'홈{i}', away=f'원정{i}',
                      history=[{'player': f'선수{j}', 'runs': j % 10} for j in range(1000)])
                 for i in range(120)]
        db.store_artifact('sample', {'live': games})
        # Use the generic artifact to avoid prediction-ledger side effects.
        with db.connect() as c:
            c.execute("UPDATE artifacts SET name='picks_v2' WHERE name='sample'")
        del games
        def old_wire():
            body, stamp = db.get_artifact_json('picks_v2')
            raw = body.encode()
            return stamp, raw, gzip.compress(raw)
        old, old_stats = measure(old_wire)
        del old
        cache = ArtifactResponses(db)
        new, new_stats = measure(lambda: cache.get_bytes('picks_v2'))
        del new, cache
        full, full_stats = measure(lambda: db.get_artifact('picks_v2'))
        labels = [{k: g[k] for k in ('sport', 'date', 'home', 'away')} for g in full['live']]
        del full
        projected, projected_stats = measure(db.proto_team_labels)
        assert projected == labels
        print(json.dumps({'old_wire': old_stats, 'compressed_only': new_stats,
                          'full_predictions': full_stats, 'team_labels': projected_stats}, indent=2))


if __name__ == '__main__':
    main()
