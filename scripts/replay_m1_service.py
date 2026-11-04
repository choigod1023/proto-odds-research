"""Replay the service inference module on preserved rows, never backdate a capture."""
import argparse
from collections import defaultdict
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.kbo_forward.features import adapter
from research.m1_service.model import forecast, metadata

SOURCE_PREDICTIONS_SHA = 'ba1ed43123a58535f2ee997de4181931449008b0e9bbbba4ba317245c7525fa9'
FRESH_SHA = '82a1e8630fafe2e790957900e981512fdfc8e70ba536f5ced45ac785f79dc706'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def index_gzip(path):
    raw = path.read_bytes(); rows = [json.loads(line) for line in gzip.decompress(raw).splitlines() if line]
    result = {r['fixture_id']: r for r in rows}
    if len(result) != len(rows):
        raise ValueError('duplicate preserved fixture')
    return result, sha(raw)


def normalize(base, raw):
    roster = {}
    for side in ('home', 'away'):
        lineup = raw['official_lineup_context']['lineups'][side]
        if [p['order'] for p in lineup] != list(range(1, 10)):
            raise ValueError('official batting order mismatch')
        roster[side] = {'hitters': [p['player_id'] for p in lineup],
                        'starter': raw['official_schedule_pitcher_context']['pitchers'][side]['player_id']}
    return {k: base[k] for k in ('fixture_id', 'event_date', 'season', 'home_id', 'away_id')} | {
        'features': {name: {'value': adapter.scalar(name, v['value'])} for name, v in raw['features'].items()},
        'roster': roster}


def stats(rows, name):
    active = [r for r in rows if r['models'][name]['pick'] is not None]
    decided = [r for r in active if r['winner'] != 'draw']
    net = sum((Decimal(r['models'][name]['net']) for r in active), Decimal(0))
    losses, briers = [], []
    for r in decided:
        p = r['models'][name]['home_probability']; y = float(r['winner'] == 'home')
        losses.append(-(y*math.log(max(p, 1e-15))+(1-y)*math.log(max(1-p, 1e-15))))
        briers.append(2*(p-y)**2)
    return {'bets': len(active), 'decided': len(decided), 'refunds': len(active)-len(decided),
            'hits': sum(r['models'][name]['pick'] == r['winner'] for r in decided),
            'net_units': str(net), 'roi': float(net/len(active)) if active else None,
            'decisive_log_loss': sum(losses)/len(losses) if losses else None,
            'decisive_brier_sum2': sum(briers)/len(briers) if briers else None}


def run(effects, actual, matched, output):
    expected_raw = (effects/'artifacts/predictions.json').read_bytes()
    if sha(expected_raw) != SOURCE_PREDICTIONS_SHA:
        raise ValueError('saved prediction freeze differs')
    expected = json.loads(expected_raw)['scenarios']['official_batting_and_starter']
    base, base_sha = index_gzip(effects/'inputs/features.jsonl.gz')
    raw_rows, fresh_sha = index_gzip(effects/'inputs/fresh-history-2026-official_batting_and_starter.jsonl.gz')
    if fresh_sha != FRESH_SHA or len(raw_rows) != 709:
        raise ValueError('preserved 709 official-input cohort differs')
    expected_idx = {name: {r['fixture_id']: r for r in expected[name]} for name in ('M0', 'M1')}
    actual_raw = (actual/'artifacts/predictions.json').read_bytes()
    expected_actual = {r['fixture_id']: r for r in json.loads(actual_raw)['predictions']['actual']['M0_actual']}
    predictions, max_error = {}, 0.
    for fid in sorted(raw_rows):
        row = normalize(base[fid], raw_rows[fid]); got = forecast(row)
        for name, alias in [('m0_projected', 'M0'), ('m1_projected', 'M1'), ('m0_actual', None)]:
            old = expected_idx[alias][fid] if alias else expected_actual[fid]
            error = max(abs(got[name]['probabilities_3way'][s]-old['probabilities_3way'][s]) for s in ('home', 'draw', 'away'))
            if error > 1e-10:
                raise ValueError('service probability replay differs: '+fid+':'+name)
            max_error = max(max_error, error)
        predictions[fid] = got
    output.mkdir(parents=True, exist_ok=True)
    frozen_bytes = (json.dumps(predictions, sort_keys=True, ensure_ascii=False, allow_nan=False)+'\n').encode('utf8')
    (output/'replayed-709.json.gz').write_bytes(gzip.compress(frozen_bytes, mtime=0))
    # Labels/prices are read only after predictions; period was already examined
    # in earlier research, so this ordering is not claimed as a blind experiment.
    results_raw = (effects/'artifacts/results.json').read_bytes()
    price_rows = json.loads(results_raw)['price_ledgers']['official_batting_and_starter']['extended_day_team_source_confirmed']
    matched_raw = matched.read_bytes(); subset = {r['fixture_id'] for r in json.loads(matched_raw)}
    if len(subset) != 103 or sha(matched_raw) != '34250a17437b3cfd6a19c915a092c19bfa1356ddf823b0bc537e9e6d88c43882':
        raise ValueError('corrected 103 subset differs')
    rows = []
    for price in price_rows:
        fid = price['fixture_id']; odds = price['price_source']['prices']; winner = price['winner']
        models = {}
        for name, p in predictions[fid].items():
            q = p['probabilities_2way']['home']; pick = None if abs(q-.5) <= 1e-12 else 'home' if q > .5 else 'away'
            net = None if pick is None else Decimal(0) if winner == 'draw' else Decimal(str(odds[pick]))-1 if winner == pick else Decimal(-1)
            models[name] = {'pick': pick, 'net': str(net) if net is not None else None,
                            'home_probability': q, 'odds': odds[pick] if pick else None}
        rows.append({'fixture_id': fid, 'date': price['date'], 'prices': odds, 'winner': winner,
                     'in_selected_103': fid in subset, 'models': models})
    if len(rows) != 667 or len({r['fixture_id'] for r in rows}) != 667:
        raise ValueError('667 price cohort differs')
    groups = {'all667': rows, 'selected103': [r for r in rows if r['in_selected_103']],
              'remaining564': [r for r in rows if not r['in_selected_103']]}
    out = {'model_metadata': metadata(), 'historical_pregame_source_receipt_proven': False,
           'evaluation': 'retrospective service-code replay, not live captures or new independent validation',
           'prediction_rows': len(predictions), 'price_rows': len(rows), 'max_probability_error': max_error,
           'inputs_sha256': {'features': base_sha, 'fresh': fresh_sha, 'effects_predictions': sha(expected_raw),
                              'actual_predictions': sha(actual_raw), 'prices_and_results': sha(results_raw),
                              'subset103': sha(matched_raw)}, 'prediction_json_sha256': sha(frozen_bytes),
           'metrics': {group: {name: stats(rs, name) for name in metadata()['models']} for group, rs in groups.items()},
           'rows': rows, 'promotion_allowed': False}
    (output/'service-replay.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8')
    print(json.dumps({k: out[k] for k in ('prediction_rows', 'price_rows', 'max_probability_error', 'metrics')}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--effects', type=Path, required=True); p.add_argument('--actual', type=Path, required=True)
    p.add_argument('--matched', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(); run(args.effects, args.actual, args.matched, args.output)
