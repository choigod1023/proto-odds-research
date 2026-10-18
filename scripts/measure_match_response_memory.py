"""Local-only A/B of HTTP-parent memory. Never point at the production DB.

python scripts/measure_match_response_memory.py --fixture docs/data/picks_v2.json
Creates a disposable SQLite copy; both variants publish identical fixture data.
"""
import argparse
import ctypes
import gc
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def memory():
    if sys.platform == 'win32':
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('peak', 'rss', 'qpp', 'qp', 'qnp', 'qn', 'page', 'peakpage')]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'rss_kib': counters.rss // 1024, 'peak_kib': counters.peak // 1024}
    values = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    return {'rss_kib': int(values['VmRSS'].split()[0]), 'peak_kib': int(values['VmHWM'].split()[0])}


def measure(mode, database):
    from runtime_db import RuntimeDatabase
    from match_api import MatchViews, PreparedMatchResponses
    from isolated_match_responses import IsolatedMatchResponses
    sys.path.insert(0, str(ROOT))
    from deploy.supervisor import release_unused_heap
    db = RuntimeDatabase(database)
    response = (PreparedMatchResponses if mode == 'before' else IsolatedMatchResponses)(MatchViews(db))
    initial = memory()
    started = time.monotonic()
    for revision in range(3):
        with db.connect() as connection:
            connection.execute("UPDATE artifacts SET stored_at=? WHERE name='picks_v2'", (str(revision),))
        response.refresh()
        response.views.release_working_set()
        release_unused_heap()
    gc.collect()
    print(json.dumps({'mode': mode, 'initial': initial, 'after': memory(),
                      'seconds': round(time.monotonic() - started, 2),
                      'body_bytes': len(response.get_bytes()), 'details': len(response.views.games)}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', type=Path)
    parser.add_argument('--mode', choices=['before', 'after'])
    parser.add_argument('--database')
    args = parser.parse_args()
    if args.mode:
        measure(args.mode, args.database)
    else:
        if args.fixture is None:
            parser.error('--fixture is required')
        from runtime_db import RuntimeDatabase
        with tempfile.TemporaryDirectory(prefix='match-memory-local-') as directory:
            path = Path(directory) / 'runtime.sqlite3'
            db = RuntimeDatabase(path)
            db.store_artifact('picks_v2', json.loads(args.fixture.read_text(encoding='utf-8')))
            for mode in ('before', 'after'):
                subprocess.run([sys.executable, __file__, '--mode', mode, '--database', str(path)], check=True)
