"""Local-only A/B using an existing compressed serving snapshot, never a live DB.

python scripts/measure_lineup_response_reuse.py --serving-cache /local/responses.zip
The reconstructed fixture preserves detail values, not original section membership
outside the recent view. Timings are not production latency estimates.
"""
import argparse
from datetime import datetime, timedelta
import gzip
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from isolated_match_responses import IsolatedMatchResponses
from match_api import KST, MatchViews, PreparedMatchResponses
from match_response_snapshot import load
from match_response_worker import refresh_lineups
from runtime_db import RuntimeDatabase
import zlib


def measure(cache, repeats=3):
    if repeats < 1:
        raise ValueError('repeats must be positive')
    now = datetime.now(KST)
    ready, _, _, games = load(cache, now.date().isoformat())
    recent = json.loads(gzip.decompress(ready['recent'][1]))
    payload = {k: v for k, v in recent.items() if k not in ('live', 'past', 'view', 'result_games')}
    live_keys = {g['_detail_key'] for g in recent.get('live', [])}
    payload.update(live=[], past=[])
    for key, compressed in games.items():
        game = json.loads(zlib.decompress(compressed))
        game.pop('baseball_current', None)
        payload['live' if key in live_keys else 'past'].append(game)
    if 'performance' in ready:
        payload['prediction_performance'] = json.loads(gzip.decompress(
            ready['performance'][1])).get('prediction_performance')
    full, reused = [], []
    with tempfile.TemporaryDirectory(prefix='lineup-reuse-local-') as directory:
        db = RuntimeDatabase(Path(directory) / 'runtime.sqlite3')
        db.store_artifact('picks_v2', payload)
        source = {'generated_at': now.isoformat(), 'games': [{
            'league': 'KBO', 'home_team': 'measurement-only', 'away_team': 'measurement-only',
            'game_datetime': now.isoformat(), 'updated_at': now.isoformat()}]}
        db.put_document('kbo_pregame', source)
        previous = IsolatedMatchResponses(MatchViews(db), Path(directory) / 'responses.zip')
        previous.refresh()
        for n in range(repeats):
            source['generated_at'] = (now + timedelta(seconds=n+1)).isoformat()
            db.put_document('kbo_pregame', source)
            start = time.perf_counter()
            baseline = PreparedMatchResponses(MatchViews(db))
            baseline.refresh()
            full.append(time.perf_counter() - start)
            start = time.perf_counter()
            result = refresh_lineups(db, previous.snapshot_path, ['recent', 'performance'])
            reused.append(time.perf_counter() - start)
            assert result is not None
            for scope, (_, body) in result[0].items():
                assert json.loads(gzip.decompress(body)) == json.loads(
                    baseline.get_bytes(scope, compressed=False))
            assert result[3] == baseline.views.games
    return {'fixture': 'reconstructed serving cache; simulated KBO timestamp-only poll',
            'games': len(games), 'full_seconds': full, 'reuse_seconds': reused,
            'full_median_seconds': statistics.median(full),
            'reuse_median_seconds': statistics.median(reused),
            'reduction_percent': (1-statistics.median(reused)/statistics.median(full))*100,
            'all_decoded_scopes_and_details_identical': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serving-cache', required=True, type=Path)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(measure(args.serving_cache, args.repeats), ensure_ascii=False, indent=2))
