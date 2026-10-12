"""Local synthetic before/after allocation comparison; no production reads.

python scripts/benchmark_streamed_views.py
Baseline source is pinned to the deployed 97ffc5da commit.
"""
import gc
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tracemalloc
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from runtime_db import RuntimeDatabase
from match_api import MatchViews


def run():
    source = subprocess.check_output(['git', 'show', '97ffc5da:src/match_api.py'], text=True, encoding='utf-8')
    baseline = types.ModuleType('baseline_match_api')
    exec(compile(source, '97ffc5da:src/match_api.py', 'exec'), baseline.__dict__)
    with tempfile.TemporaryDirectory(prefix='stream-view-bench-') as temporary:
        db = RuntimeDatabase(Path(temporary)/'synthetic.db')
        history = [{'name': f'선수{i}', 'history': list(range(40)), 'note': '가나다'*20} for i in range(160)]
        games = [{'home': f'홈{i}', 'away': f'원정{i}', 'year': 2026, 'date': '10.01(목) 10:00',
                  'options': [{'배당': 1.5, 'selection_id': 'home'}],
                  '선발': {'home_detail': {'name': '선수', 'history': history}}} for i in range(250)]
        db.store_artifact('picks_v2', {'generated_at': 'fixed', 'live': games, 'past': [],
                                     'prediction_performance': {'records': history*100}})
        del games, history
        outputs, metrics = {}, {}
        for name, cls in [('before', baseline.MatchViews), ('after', MatchViews)]:
            gc.collect()
            tracemalloc.start()
            started = time.perf_counter()
            views = cls(db)
            result = views.get_bytes('all')
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            metrics[name] = {'python_peak_mib': peak/1048576, 'seconds_with_tracing': time.perf_counter()-started}
            outputs[name] = __import__('gzip').decompress(result)
            del views, result
            gc.collect()
            started = time.perf_counter()
            views = cls(db)
            views.get_bytes('all')
            metrics[name]['seconds_without_tracing'] = time.perf_counter()-started
            del views
        assert json.loads(outputs['before']) == json.loads(outputs['after'])
        metrics['responses_equal'] = True
        metrics['peak_reduction_percent'] = (1-metrics['after']['python_peak_mib']/metrics['before']['python_peak_mib'])*100
        print(json.dumps(metrics))


if __name__ == '__main__':
    run()
