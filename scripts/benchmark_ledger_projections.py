"""Local projection microbenchmark; never collect, predict, settle or alter a ledger.

Copies an existing JSONL to a temporary directory, verifies it fully in each
fresh child, and compares exactly the same scoped/full projection outputs.
The chosen candidate subset simulates a partial official-result feed; it is not
evidence of production latency or an ROI experiment.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def measure(ledger, baseline, rounds, candidate_count):
    def offline(*args, **kwargs):
        raise AssertionError('network access is prohibited in the projection benchmark')
    socket.socket = offline
    os.environ.pop('PROODD_DB_PATH', None)
    if baseline:
        code = subprocess.check_output(['git', 'show', baseline + ':src/prediction_runtime.py'], cwd=ROOT)
        module = types.ModuleType('baseline_prediction_runtime')
        exec(compile(code, 'baseline_prediction_runtime.py', 'exec'), module.__dict__)
    else:
        import prediction_runtime as module
    runtime = module.PredictionRuntime(ledger)
    start = time.perf_counter()
    records = runtime.records()  # Full schema/hash/sequence verification, outside the timed projection loop.
    verify_seconds = time.perf_counter() - start
    events = sorted({r['event_id'] for r in records if r.get('record_type') == 'prediction'})
    candidates = set(events[:candidate_count])
    views = (runtime.ui_records, runtime.market_records, runtime.featured_records, runtime.candidate_records)
    started, cpu_started = time.perf_counter(), time.process_time()
    for _ in range(rounds):
        scoped = [({k: v for k, v in view().items() if k in candidates}
                   if baseline else view(events=candidates)) for view in views]
        # Existing refresh publishes two winner/market summaries plus the
        # featured view after recording. Those still cover the complete ledger.
        publication = [view() for view in (runtime.ui_records, runtime.market_records,
                                          runtime.ui_records, runtime.market_records,
                                          runtime.featured_records)]
    cpu = time.process_time() - cpu_started
    elapsed = time.perf_counter() - started
    digest = hashlib.sha256(json.dumps([scoped, publication], sort_keys=True,
                                       ensure_ascii=False).encode()).hexdigest()
    return dict(variant=baseline or 'scoped_cached', records=len(records), events=len(events),
                candidate_events=len(candidates), rounds=rounds, verify_seconds=verify_seconds,
                wall_seconds_per_cycle=elapsed/rounds, cpu_seconds_per_cycle=cpu/rounds,
                semantic_sha256=digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, default=ROOT / 'data/raw/prediction_ledger/pregame.jsonl')
    parser.add_argument('--baseline', default='origin/main')
    parser.add_argument('--rounds', type=int, default=20)
    parser.add_argument('--candidate-count', type=int, default=10)
    parser.add_argument('--trials', type=int, default=3)
    parser.add_argument('--variant')
    args = parser.parse_args()
    if args.rounds < 1 or args.candidate_count < 0 or args.trials < 1:
        parser.error('rounds/trials must be positive and candidate-count nonnegative')
    if args.variant:
        print(json.dumps(measure(args.ledger, None if args.variant == 'scoped_cached'
                                 else args.variant, args.rounds, args.candidate_count)))
        return
    original_hash = hashlib.sha256(args.ledger.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='ledger-projection-benchmark-') as directory:
        ledger = Path(directory) / 'ledger.jsonl'
        shutil.copyfile(args.ledger, ledger)
        rows = []
        for trial in range(args.trials):
            variants = [args.baseline, 'scoped_cached']
            if trial % 2:
                variants.reverse()
            for variant in variants:
                output = subprocess.check_output([sys.executable, __file__, '--ledger', str(ledger),
                    '--variant', variant, '--rounds', str(args.rounds),
                    '--candidate-count', str(args.candidate_count)], text=True)
                row = dict(json.loads(output), trial=trial)
                rows.append(row)
                print(json.dumps(row), flush=True)
        if len({r['semantic_sha256'] for r in rows}) != 1:
            raise RuntimeError('projection outputs changed')
        for variant in (args.baseline, 'scoped_cached'):
            group = [r for r in rows if r['variant'] == variant]
            print(json.dumps(dict(variant=variant, median_cpu_seconds=statistics.median(
                r['cpu_seconds_per_cycle'] for r in group), median_wall_seconds=statistics.median(
                r['wall_seconds_per_cycle'] for r in group))), flush=True)
    if hashlib.sha256(args.ledger.read_bytes()).hexdigest() != original_hash:
        raise RuntimeError('input ledger changed')


if __name__ == '__main__':
    main()
