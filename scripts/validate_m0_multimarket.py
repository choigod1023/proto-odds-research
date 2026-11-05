"""Audit preserved M0 replay and bounded, hash-verified Betman archives.

This is retrospective, not evidence of pregame receipt or execution.
"""
import collections
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.market_outcomes import normalize_market, probabilities, settle

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'experiments/m0-multimarket-20261105'
MANIFEST = ROOT / 'experiments/pregame-proto-prices-20261104/manifest-v2.json'
REPLAY = ROOT / 'experiments/m1-service-20261104/replay/service-replay.json'
FORECAST = ROOT / 'experiments/m1-service-20261104/replay/replayed-709.json.gz'


def main():
    manifest = json.loads(MANIFEST.read_text(encoding='utf8'))
    replay = json.loads(REPLAY.read_text(encoding='utf8'))
    with gzip.open(FORECAST, 'rt', encoding='utf8') as fh:
        predictions = json.load(fh)
    known = {r['fixture_id']: r for r in replay['rows']}
    index = collections.defaultdict(list)
    for fid in predictions:
        # KBO game id format is YYYYMMDD + away(2) + home(2) + sequence + season.
        if len(fid) == 17 and fid[:8].isdigit() and fid[-4:] == '2026':
            index[(fid[:8], fid[10:12], fid[8:10])].append(fid)
    observations = collections.defaultdict(list)
    checked = missing = 0
    for source in manifest['sources']:
        if not source['round'].startswith('26'):
            continue
        path = Path(manifest['source_roots'][source['source_root_index']]) / source['body_file']
        if not path.exists():
            missing += 1
            continue
        raw = gzip.decompress(path.read_bytes())
        if hashlib.sha256(raw).hexdigest() != source['body_sha256']:
            raise ValueError('archive hash mismatch: '+str(path))
        checked += 1
        data = json.loads(raw)['compSchedules']
        for raw_row in data['datas']:
            row = dict(zip(data['keys'], raw_row))
            if row.get('leagueCode') != 'BS001':
                continue
            date = dt.datetime.fromtimestamp(row['gameDate']/1000, dt.timezone(dt.timedelta(hours=9))).strftime('%Y%m%d')
            key = (date, row.get('homeId'), row.get('awayId'))
            ids = index.get(key, [])
            if len(ids) == 1 and ids[0] in known:
                observations[(ids[0], source['round'])].append(row)
    counts = collections.Counter()
    possible = set()
    candidates = collections.defaultdict(list)
    for (fid, rnd), rows in observations.items():
        winners = [r for r in rows if r.get('handi') == 0]
        totals = [r for r in rows if r.get('handi') == 9]
        handicaps = [r for r in rows if r.get('handi') == 2]
        counts['raw_total_rows'] += len(totals)
        counts['raw_handicap_rows'] += len(handicaps)
        if totals: possible.add(fid)
        if len(winners) != 1:
            counts['winner_rows_not_unique_event_rounds'] += 1
            continue
        winner = winners[0]
        scores = str(winner.get('mchScore') or '').split(':')
        if len(scores) != 2 or not all(s.isdigit() for s in scores):
            counts['winner_score_missing_event_rounds'] += 1
            continue
        h, a = map(int, scores)
        actual = 'home' if h > a else 'away' if h < a else 'draw'
        if actual != known[fid]['winner']:
            counts['winner_mismatch'] += 1
            continue
        counts['matched_result_event_rounds'] += 1
        if totals: counts['matched_result_total_event_rounds'] += 1
        if handicaps: counts['matched_result_handicap_event_rounds'] += 1
        if len(totals) == 1:
            total = totals[0]
            try:
                line = float(total['winHandi'])
                prices = {'under': float(total['winAllot']), 'over': float(total['loseAllot'])}
                wp = known[fid]['prices']
                winner_prices_match = abs(float(winner['winAllot'])-float(wp['home'])) < 1e-9 and abs(float(winner['loseAllot'])-float(wp['away'])) < 1e-9
                outcome = 'under' if h+a < line else 'over' if h+a > line else None
                expected = {'under':'0', 'over':'2'}.get(outcome)
                if (not winner_prices_match or expected is None or str(total['gameResult']) != expected
                    or total.get('gameSubject') not in (None, '') or total.get('gameReject') != '0'
                    or str(total.get('protoStatus')) != '4' or str(winner.get('protoStatus')) != '4'
                    or float(total['loseHandi']) != line or line*2 != int(line*2) or line == int(line)
                    or min(prices.values()) <= 1 or prices['under'] == prices['over']):
                    counts['total_fails_price_result_or_terms_event_rounds'] += 1
                    continue
                options = [dict(market='total', game_no=str(total['matchSeq']), line=str(line),
                                period='full_including_extra', settlement_rule='total_half',
                                unit='runs', selection=side) for side in prices]
                terms = normalize_market(options, 'baseball', 'KBO')
                m = predictions[fid]['m0_actual']
                probs = probabilities(terms, m['home_rate'], m['away_rate'])
                labels = {'under':'언더', 'over':'오버'}
                if probs['언더']['p_win'] == probs['오버']['p_win']: continue
                selection = max(prices, key=lambda side: probs[labels[side]]['p_win'])
                baseline = min(prices, key=prices.get)
                state = settle(terms, selection, h, a, result_period=terms['period'])['state']
                candidates[fid].append({'fixture_id':fid, 'round':rnd, 'game_no':terms['game_no'],
                    'line':line, 'score':[h,a], 'outcome':outcome, 'prices':prices,
                    'selection':selection, 'model_probability':probs[labels[selection]]['p_win'],
                    'baseline':baseline, 'state':state,
                    'winner_selection':known[fid]['models']['m0_actual']['pick'],
                    'winner_prices':wp, 'winner_outcome':actual})
            except (KeyError, TypeError, ValueError):
                counts['invalid_total_terms'] += 1
    cohort = [rows[0] for rows in candidates.values() if len(rows) == 1]
    def metric(select, prices, result):
        hits = sum(select(r) == result(r) for r in cohort)
        net = sum((prices(r)[select(r)]-1 if select(r) == result(r) else -1) for r in cohort)
        return {'n':len(cohort), 'hits':hits, 'refunds':0, 'accuracy':hits/len(cohort) if cohort else None,
                'net_units':round(net, 6), 'roi':net/len(cohort) if cohort else None}
    # Winner refunds require separate denominator and are computed below.
    winner_rows = [r for r in cohort if r['winner_selection'] is not None]
    winner_decided = [r for r in winner_rows if r['winner_outcome'] != 'draw']
    winner_hits = sum(r['winner_selection'] == r['winner_outcome'] for r in winner_decided)
    winner_net = sum(0 if r['winner_outcome'] == 'draw' else r['winner_prices'][r['winner_selection']]-1 if r['winner_selection'] == r['winner_outcome'] else -1 for r in winner_rows)
    comparisons = {'m0_total':metric(lambda r:r['selection'], lambda r:r['prices'], lambda r:r['outcome']),
                   'market_favorite_total':metric(lambda r:r['baseline'], lambda r:r['prices'], lambda r:r['outcome']),
                   'm0_winner':{'n':len(winner_rows), 'decided':len(winner_decided), 'hits':winner_hits,
                       'refunds':len(winner_rows)-len(winner_decided),
                       'accuracy':winner_hits/len(winner_decided) if winner_decided else None,
                       'net_units':round(winner_net,6), 'roi':winner_net/len(winner_rows) if winner_rows else None}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'cohort.json').write_text(json.dumps(cohort, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    result = {'forecast_rows': len(predictions), 'winner_cohort_rows': len(known),
              'archive_2026_sources_verified': checked, 'archive_2026_sources_missing': missing,
              'raw_audit': dict(counts), 'unique_winner_cohort_fixtures_with_raw_total': len(possible),
              'eligible_total_fixtures': len(cohort), 'multiple_matching_rounds_excluded': sum(len(v)>1 for v in candidates.values()),
              'comparisons': comparisons, 'handicap_roi': None,
              'handicap_reason': 'KBO two-way handicap settlement rule is unverified; held',
              'retrospective': True, 'pregame_receipt_proven': False}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT/'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__': main()
