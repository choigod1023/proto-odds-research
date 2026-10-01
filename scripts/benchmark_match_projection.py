"""Synthetic LOCAL ONLY benchmark, no production paths or network.

python scripts/benchmark_match_projection.py --games 600 --history 200
python scripts/benchmark_match_projection.py --input docs/data/picks_v2.json
Each reader variant starts in a fresh process after fixture generation finishes.
Baseline is the immutable independent base's MatchViews, loaded with git show.
"""
import argparse
import ctypes
import gc
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
BASE = '97ffc5da068dfa66b57c85cabaa9d56944288160'


def rss():
    if sys.platform == 'win32':
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL('kernel32')
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        get_memory = ctypes.WinDLL('psapi').GetProcessMemoryInfo
        get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not get_memory(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError()
        return {'rss_bytes': counters.WorkingSetSize, 'peak_rss_bytes': counters.PeakWorkingSetSize}
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'peak_rss_bytes': peak if sys.platform == 'darwin' else peak * 1024}


def measured(action):
    wall, cpu = time.perf_counter(), time.process_time()
    value = action()
    return value, {'wall_seconds': time.perf_counter() - wall,
                   'cpu_seconds': time.process_time() - cpu, **rss()}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def reader(args):
    from runtime_db import RuntimeDatabase
    if args.variant == 'before':
        module = types.ModuleType('baseline_match_api')
        source = subprocess.check_output(['git', 'show', f'{BASE}:src/match_api.py'], cwd=ROOT)
        exec(compile(source, 'baseline_match_api.py', 'exec'), module.__dict__)
    else:
        import match_api as module
    db = RuntimeDatabase(args.database)
    views = module.MatchViews(db)
    initial = rss()
    recent, cold = measured(lambda: views.get('recent'))
    all_games, all_time = measured(lambda: views.get('all'))
    key = all_games['live'][0]['_detail_key']
    detail, detail_time = measured(lambda: views.get('detail', key, views.revision))
    hashes = {k: digest(v) for k, v in [('recent', recent), ('all', all_games), ('detail', detail)]}
    del recent, all_games, detail
    gc.collect()
    _, warm = measured(lambda: [views.get_bytes('recent') for _ in range(30)])
    # Same fixture, new revision: exercise cache replacement without generation noise.
    subprocess.check_call([sys.executable, str(Path(__file__).resolve()), '--variant',
                           'revision', '--database', str(args.database)], cwd=ROOT)
    _, refresh = measured(lambda: views.get('recent'))
    print(json.dumps({'variant': args.variant, 'initial': initial, 'cold': cold,
                      'all': all_time, 'detail': detail_time, 'warm_30': warm,
                      'refresh': refresh, 'hashes': hashes}))


def fixture(args):
    if args.input:
        return json.loads(args.input.read_text(encoding='utf-8'))
    from match_api import KST
    from datetime import datetime
    today = datetime.now(KST)
    games = []
    for i in range(args.games):
        history = [{'id': j, 'name': f'player-{i}-{j}', 'scores': list(range(20)),
                    'notes': f'synthetic-{i}-{j}' * 8} for j in range(args.history)]
        games.append({'home': f'Home-{i}', 'away': f'Away-{i}', 'year': today.year,
                      'date': today.strftime('%m.%d') + ' 18:00', 'round': 1,
                      'options': [{'selection_id': str(i), '적중': True, '배당': 1.8}],
                      'prediction_record': {'revision': f'frozen-{i}'},
                      '선발': {'home_detail': {'name': f'Pitcher-{i}', 'history': history}}})
    return {'generated_at': 'synthetic', 'live': games, 'past': [],
            'prediction_performance': {'records': list(range(args.games))}}


def writer(args):
    if args.variant == 'before-write':
        module = types.ModuleType('baseline_runtime_db')
        module.__file__ = str(ROOT / 'src/runtime_db.py')
        source = subprocess.check_output(['git', 'show', f'{BASE}:src/runtime_db.py'], cwd=ROOT)
        exec(compile(source, module.__file__, 'exec'), module.__dict__)
    else:
        import runtime_db as module
    payload = fixture(args)
    db = module.RuntimeDatabase(args.database)
    initial = rss()
    _, first = measured(lambda: db.store_artifact('picks_v2', payload))
    _, replacement = measured(lambda: db.store_artifact('picks_v2', payload))
    print(json.dumps({'variant': args.variant, 'initial_with_fixture': initial,
                      'first_store': first, 'replacement_store': replacement}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--games', type=int, default=600)
    parser.add_argument('--history', type=int, default=200)
    parser.add_argument('--input', type=Path, help='Local archived fixture, NOT a live production copy')
    parser.add_argument('--variant', choices=['before', 'after', 'fallback', 'before-write', 'after-write', 'revision'])
    parser.add_argument('--database', type=Path)
    args = parser.parse_args()
    if args.input:
        args.input = args.input.resolve()
    from runtime_db import RuntimeDatabase
    if args.variant == 'revision':
        with RuntimeDatabase(args.database).transaction() as con:
            con.execute("UPDATE artifacts SET stored_at='benchmark-next' WHERE name='picks_v2'")
            con.execute("UPDATE match_projection SET revision='benchmark-next'")
        return
    if args.variant in ('before-write', 'after-write'):
        return writer(args)
    if args.variant:
        return reader(args)
    with tempfile.TemporaryDirectory(prefix='match-projection-bench-') as directory:
        path = Path(directory) / 'synthetic.sqlite3'
        writes = []
        for variant, target in [('before-write', Path(directory) / 'baseline.sqlite3'), ('after-write', path)]:
            writes.append(json.loads(subprocess.check_output([
                sys.executable, str(Path(__file__).resolve()), '--variant', variant,
                '--database', str(target), '--games', str(args.games), '--history', str(args.history)] +
                (['--input', str(args.input)] if args.input else []),
                cwd=ROOT, text=True)))
        db = RuntimeDatabase(path)
        size = db.artifact_metadata('picks_v2')['payload_bytes']
        results = []
        for variant in ('before', 'after', 'fallback'):
            with db.transaction() as con:
                con.execute("UPDATE artifacts SET stored_at='benchmark' WHERE name='picks_v2'")
                con.execute("UPDATE match_projection SET revision='benchmark'")
                if variant == 'fallback':
                    con.execute("DELETE FROM match_projection WHERE name='picks_v2'")
            results.append(json.loads(subprocess.check_output([
                sys.executable, str(Path(__file__).resolve()), '--variant', variant,
                '--database', str(path)], cwd=ROOT, text=True)))
        assert all(result['hashes'] == results[0]['hashes'] for result in results), 'response mismatch'
        if args.input:
            source = args.input.read_bytes()
            metadata = {'kind': 'archived repository fixture; NOT live production',
                        'path': str(args.input), 'file_bytes': len(source),
                        'sha256': hashlib.sha256(source).hexdigest(),
                        'generated_at': json.loads(source).get('generated_at')}
        else:
            metadata = {'kind': 'synthetic', 'games': args.games,
                        'history_per_game': args.history, 'generated_at': 'synthetic'}
        print(json.dumps({'fixture': metadata, 'base': BASE, 'stored_payload_characters': size,
                          'writes': writes, 'results': results}, indent=2))


if __name__ == '__main__':
    main()
