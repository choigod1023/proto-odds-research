"""Local-only isolated-process comparison; never opens the production DB.

Usage: python scripts/benchmark_odds_context.py
Python allocation peak is not RSS; Windows process peak includes SQLite/native memory.
"""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from runtime_db import RuntimeDatabase


def peak_rss():
    if os.name != 'nt':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [
            (name, ctypes.c_size_t) for name in ('peak', 'working', 'p1', 'p2', 'p3', 'p4', 'page', 'peakpage')]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1), ctypes.byref(counters), counters.cb)
    return counters.peak / 1024


if len(sys.argv) > 1:
    db = RuntimeDatabase(sys.argv[2])
    tracemalloc.start()
    started = time.perf_counter()
    data = db.get_artifact('picks_v2') if sys.argv[1] == 'full' else db.odds_collection_context()
    elapsed = time.perf_counter() - started
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    # Compare the exact consumer inputs, preserving option order and value types.
    canonical = {'rounds': data.get('rounds'), 'generated_at': data.get('generated_at'), 'live': [
        {'round': g.get('round'), 'options': [{k: o.get(k) for k in ('게임번호', 'market', 'label', '배당')}
          for o in g.get('options') or []]} for g in data.get('live') or []]}
    import hashlib
    print(json.dumps({'mode': sys.argv[1], 'seconds': elapsed, 'python_peak_mib': peak / 1048576,
                      'process_peak_mib': peak_rss() / 1024,
                      'sha256': hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()}))
else:
    with tempfile.TemporaryDirectory(prefix='odds-context-') as directory:
        path = Path(directory) / 'fixture.sqlite3'
        db = RuntimeDatabase(path)
        fixture = json.loads((ROOT / 'docs/data/picks_v2.json').read_text(encoding='utf-8'))
        db.store_artifact('picks_v2', fixture)
        print(json.dumps({'fixture': 'repository picks_v2, not production', 'json_chars': len(json.dumps(fixture, ensure_ascii=False))}))
        del fixture
        for _ in range(3):
            for mode in ('full', 'projected'):
                subprocess.run([sys.executable, __file__, mode, str(path)], check=True)
