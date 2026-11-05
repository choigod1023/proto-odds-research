"""Replay the fixed 44-game KBO handicap comparison from preserved archives.

All source files are read only. This is not proof of historical availability.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path

from scipy.stats import skellam

ROOT = Path(__file__).resolve().parents[1]
WORKER = Path('C:/Users/user/Documents/Codex/market-outcomes-validation')
MANIFEST = WORKER / 'experiments/pregame-proto-prices-20261104/manifest-v2.json'
COHORT = WORKER / 'experiments/m0-multimarket-20261105/cohort.json'
FORECAST = WORKER / 'experiments/m1-service-20261104/replay/replayed-709.json.gz'
EXPECTED = {
    'manifest': 'f91800d7a7daf09762f66c6ae16cbafcd557bc9ca3b26f7b7940d2eacbfa4cc6',
    'cohort': 'a9c20e35be4446474de045a8b9fedda300f607ba00aacc7e8183e9c9a3a6ecbf',
    'forecast': 'a597ed066534cce92cfb12606d7f7e3edcc7591b3a6c1d17e7777c8b04e8b505',
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def half_outcome(home, away, line):
    line = Decimal(str(line))
    if not line.is_finite() or line * 2 != (line * 2).to_integral_value() or line == line.to_integral_value():
        raise ValueError('only half-lines are supported')
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in (home, away)):
        raise ValueError('scores must be nonnegative integers')
    return 'home' if Decimal(home) + line > away else 'away'


def home_probability(home_rate, away_rate, line):
    half_outcome(0, 0, line)  # validate the line
    if any(not math.isfinite(x) or x <= 0 for x in (home_rate, away_rate)):
        raise ValueError('positive finite rates required')
    return float(skellam.sf(math.floor(-float(line)), home_rate, away_rate))


def choose(probability, home_odds, away_odds):
    if probability == 0.5 or home_odds == away_odds:
        raise ValueError('model or market tie; no selection')
    return ('home' if probability > .5 else 'away',
            'home' if home_odds < away_odds else 'away')


def metric(rows, field):
    hits = sum(r[field] == r['outcome'] for r in rows)
    gross = sum((Decimal(str(r['prices'][r[field]])) for r in rows if r[field] == r['outcome']), Decimal(0))
    net = gross - len(rows)
    return {'n': len(rows), 'hits': hits, 'losses': len(rows)-hits, 'refunds': 0,
            'gross_units': str(gross), 'net_units': str(net), 'roi': float(net / len(rows))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=MANIFEST)
    parser.add_argument('--cohort', type=Path, default=COHORT)
    parser.add_argument('--forecast', type=Path, default=FORECAST)
    parser.add_argument('--output', type=Path, default=ROOT / 'experiments/m0-handicap-20261105')
    args = parser.parse_args()
    sources = {}
    for name in EXPECTED:
        p = getattr(args, name)
        content = p.read_bytes()
        sha = digest(content)
        if sha != EXPECTED[name]:
            raise ValueError(f'{name} hash mismatch: {p}: {sha}')
        sources[name] = {'path': str(p), 'sha256_file': sha}
    manifest = json.loads(args.manifest.read_bytes())
    cohort = json.loads(args.cohort.read_bytes())
    with gzip.open(args.forecast, 'rt', encoding='utf8') as f:
        forecasts = json.load(f)
    if len(cohort) != 44 or len({r['fixture_id'] for r in cohort}) != 44:
        raise ValueError('fixed cohort must contain 44 unique fixtures')
    wanted = {(r['round'], r['fixture_id'][:8], r['fixture_id'][10:12], r['fixture_id'][8:10]): r for r in cohort}
    if len(wanted) != 44:
        raise ValueError('fixed cohort has an ambiguous round/date/team key')
    found, raw_sources, matrix, completed_general = defaultdict(list), [], Counter(), Counter()
    proof = []
    for source in manifest['sources']:
        if not source['round'].startswith('26'):
            continue
        path = Path(manifest['source_roots'][source['source_root_index']]) / source['body_file']
        raw_compressed = path.read_bytes()  # missing archives must fail, not silently shrink the sample
        raw = gzip.decompress(raw_compressed)
        if digest(raw) != source['body_sha256']:
            raise ValueError(f'archive hash mismatch: {path}')
        raw_sources.append({'round': source['round'], 'path': str(path), 'body_sha256': digest(raw),
                            'gzip_sha256': digest(raw_compressed), 'source_url': source['source_url']})
        data = json.loads(raw)['compSchedules']
        grouped = defaultdict(list)
        for raw_row in data['datas']:
            row = dict(zip(data['keys'], raw_row))
            if row.get('leagueCode') == 'BS001':
                grouped[(row['gameDate'], row['homeId'], row['awayId'])].append(row)
        for (timestamp, home_id, away_id), rows in grouped.items():
            winners = [r for r in rows if r.get('handi') == 0 and str(r.get('protoStatus')) == '4' and r.get('gameReject') == '0']
            if len(winners) != 1:
                continue
            winner = winners[0]
            score = str(winner.get('mchScore') or '').split(':')
            if len(score) != 2 or not all(x.isdigit() for x in score):
                continue
            home, away = map(int, score)
            general = 'home' if home > away else 'away' if home < away else 'draw'
            completed_general[(general, str(winner['gameResult']))] += 1
            date = datetime.fromtimestamp(timestamp / 1000, timezone(timedelta(hours=9))).strftime('%Y%m%d')
            key = (source['round'], date, home_id, away_id)
            for hc in (r for r in rows if r.get('handi') == 2):
                try:
                    line = Decimal(str(hc['winHandi']))
                    outcome = half_outcome(home, away, line)
                    if (str(hc['protoStatus']) != '4' or hc['gameReject'] != '0'
                        or hc['drawAllot'] != 0 or hc['gameSubject'] not in (None, '')
                        or Decimal(str(hc['loseHandi'])) != -line):
                        continue
                    parts = str(hc['mchScore']).split(':')
                    if len(parts) != 2 or Decimal(parts[0]) != home + line or Decimal(parts[1]) != away:
                        raise ValueError('adjusted score inconsistent')
                    if str(hc['gameResult']) != {'home': '0', 'away': '2'}[outcome]:
                        raise ValueError('result code inconsistent with adjusted score')
                    prices = {'home': float(hc['winAllot']), 'away': float(hc['loseAllot'])}
                    if min(prices.values()) <= 1 or not all(math.isfinite(x) for x in prices.values()):
                        raise ValueError('invalid odds')
                    matrix[(('negative' if line < 0 else 'positive'), outcome, str(hc['gameResult']))] += 1
                    if home == away:
                        proof.append({'round': source['round'], 'fixture_key': list(key), 'body_path': str(path),
                                      'body_sha256': source['body_sha256'], 'general_raw': winner, 'handicap_raw': hc})
                    if key not in wanted:
                        continue
                    original = wanted[key]
                    if original['score'] != [home, away]:
                        raise ValueError('score differs from fixed cohort')
                    if any(abs(float(winner[k]) - original['winner_prices'][side]) > 1e-9 for k, side in [('winAllot', 'home'), ('loseAllot', 'away')]):
                        raise ValueError('general winner prices differ from fixed cohort')
                    fid = original['fixture_id']
                    rates = forecasts[fid]['m0_actual']
                    p = home_probability(rates['home_rate'], rates['away_rate'], line)
                    model, market = choose(p, prices['home'], prices['away'])
                    found[fid].append({'fixture_id': fid, 'round': source['round'], 'date_kst': date,
                                       'home_id': home_id, 'away_id': away_id, 'general_game_no': winner['matchSeq'],
                                       'game_no': hc['matchSeq'], 'score': [home, away], 'home_line': float(line),
                                       'official_adjusted_score': hc['mchScore'], 'official_result_code': str(hc['gameResult']),
                                       'outcome': outcome, 'prices': prices, 'home_rate': rates['home_rate'],
                                       'away_rate': rates['away_rate'], 'model_home_probability': p,
                                       'model_selection': model, 'market_selection': market,
                                       'body_sha256': source['body_sha256']})
                except (KeyError, TypeError, ArithmeticError) as exc:
                    raise ValueError(f'malformed handicap row round={source["round"]} game={hc.get("matchSeq")}') from exc
    if len(raw_sources) != 76 or len(found) != 44 or any(len(v) != 1 for v in found.values()):
        raise ValueError('source count or fixed-cohort matching invariant failed')
    result = [found[r['fixture_id']][0] for r in cohort]
    if len(proof) != 1:
        raise ValueError('expected exactly one qualifying tied-score handicap row')
    case = proof[0]
    if (sum(matrix.values()) != 45 or case['round'] != '260040'
        or case['general_raw']['matchSeq'] != 192 or case['general_raw']['mchScore'] != '5:5'
        or str(case['general_raw']['gameResult']) != '4'
        or case['handicap_raw']['matchSeq'] != 194 or case['handicap_raw']['winHandi'] != -2.5
        or case['handicap_raw']['loseHandi'] != 2.5 or case['handicap_raw']['mchScore'] != '2.5:5'
        or str(case['handicap_raw']['gameResult']) != '2'):
        raise ValueError('observed raw proof or 45-row census changed')
    summary = {'model': metric(result, 'model_selection'), 'market_favorite': metric(result, 'market_selection')}
    if any(summary[k]['hits'] != 29 or summary[k]['net_units'] != '-5.83' for k in summary):
        raise ValueError('fixed comparison result changed')
    audit = {'input_sources': sources, 'archives_verified': len(raw_sources), 'raw_sources': raw_sources,
             'general_result_matrix': [{'outcome': k[0], 'code': k[1], 'count': v} for k, v in sorted(completed_general.items())],
             'handicap_result_matrix': [{'line_sign': k[0], 'outcome': k[1], 'code': k[2], 'count': v} for k, v in sorted(matrix.items())],
             'original_draw_proof': proof, 'fixed_cohort_size': len(result), 'comparisons': summary,
             'same_model_and_market_selection': sum(x['model_selection'] == x['market_selection'] for x in result),
             'retrospective': True, 'pregame_receipt_proven': False, 'execution_price_verified': False}
    args.output.mkdir(parents=True, exist_ok=True)
    for name, obj in [('audit.json', audit), ('cohort.json', result)]:
        (args.output / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf8', newline='\n')
    print(json.dumps({'archives': len(raw_sources), 'handicap_rows': sum(matrix.values()), 'comparisons': summary}, ensure_ascii=False))


if __name__ == '__main__':
    main()
