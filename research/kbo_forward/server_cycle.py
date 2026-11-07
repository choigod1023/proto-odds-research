"""Existing supervised Python cycle plus a recoverable compact M0 index.

No AI service, fitting, wallet operation or production recommendation write.
Original captures stay in the append-only research ledger.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.kbo_forward.runner import run_once, atomic_json
from research.m1_service.observer import read, canonical, digest, moment


def progress_writer(state):
    """Keep the current stage and the last interrupted cycle on the volume."""
    started = datetime.now(timezone.utc).isoformat()
    path = state / 'progress.json'

    def mark(stage, game_id=None):
        row = {'run_started_at': started, 'at': datetime.now(timezone.utc).isoformat(),
               'stage': stage, 'official_game_id': game_id}
        print(json.dumps({'kbo_m0_progress': row}), flush=True)
        try:
            if stage == 'cycle_started' and path.is_file():
                previous = json.loads(path.read_bytes())
                if previous.get('stage') != 'cycle_finished':
                    atomic_json(state / 'last-interrupted-progress.json', previous)
            atomic_json(path, row)
        except (OSError, ValueError) as exc:
            print(json.dumps({'kbo_m0_progress_write_error': type(exc).__name__}), flush=True)

    return mark


def publish_index(state):
    state = Path(state).resolve()
    rows = read(state/'predictions.jsonl')
    latest = {}
    for row in rows:
        if row.get('type') != 'capture' or row.get('schema') != 'm1-pregame-rehearsal-v1' or 'm0_actual' not in row.get('models', {}):
            continue
        ev = row['input']['event']
        latest[(str(ev['season']), ev['official_game_id'])] = row
    chosen = sorted(latest.values(), key=lambda r: moment(r['captured_at']), reverse=True)[:20]
    index = []
    for row in chosen:
        claimed = row['hash']
        if digest(canonical({k:v for k,v in row.items() if k != 'hash'})) != claimed:
            raise ValueError('M0 capture hash differs')
        raw = canonical(row)+b'\n'
        if len(raw) > 8*1024*1024:
            raise ValueError('M0 source packet exceeds provider bound')
        relative = Path('capture-index')/(claimed+'.json')
        path = state/relative; path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError('immutable M0 capture archive differs')
        else:
            temporary = path.with_suffix('.json.tmp')
            with temporary.open('wb') as out:
                out.write(raw); out.flush(); os.fsync(out.fileno())
            os.replace(temporary, path)
        index.append({'hash':claimed, 'event':row['input']['event'], 'file':relative.as_posix(),
                      'file_sha256':digest(raw)})
    payload = {'schema':'m0-capture-index-v1', 'captures':index,
               'scope':'compact references; source packet replay required, not full ledger chain authentication'}
    target = state/'capture-index.json'
    if not target.exists() or json.loads(target.read_bytes()) != payload:
        atomic_json(target, payload)
    return len(index)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--m1-state', type=Path, required=True)
    args = parser.parse_args()
    progress = progress_writer(args.state)
    result = run_once(args.state, m1_state=args.m1_state, progress=progress,
                      operating_prices=args.state.parent / 'kbo-operating-price-v1')
    if result['status'] == 'another_run_active':
        return 0
    progress('publish_index')
    count = publish_index(args.m1_state)  # Also repairs a missing index after a prior interrupted publication.
    print(json.dumps({'status':result['status'], 'm0_indexed_fixtures':count,
                      'captures':result.get('captured_this_run'), 'requests':result.get('requests')}))
    progress('cycle_finished')
    return 0 if result['status']=='ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
