"""Apply the fixed modal comparison rule to an audited historical KBO cohort."""
import argparse
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from market_outcomes import normalize_market, probabilities
from market_pick_frontier import FINAL, MODELS, choose_final, score_pick


def replay(cohort, forecasts):
    fixtures = defaultdict(list)
    seen = set()
    for row in cohort:
        fid, market = row['fixture_id'], row['market']
        if market not in {'total', 'handicap'} or (fid, market) in seen:
            raise ValueError('expected one audited half-line product per fixture and market')
        seen.add((fid, market))
        labels = {'under': '언더', 'over': '오버', 'home': '핸디홈', 'away': '핸디원정'}
        options = {labels[side]: {'market': market, 'game_no': str(row['game_no']), 'line': row['line'],
            'period': 'full_including_extra', 'settlement_rule': market + '_half', 'unit': 'runs',
            'selection': labels[side], 'selection_id': f'{market}:{side}',
            'offer_id': f'{row["round"]}:{row["game_no"]}:{side}', '배당': odds}
            for side, odds in row['prices'].items()}
        terms = normalize_market(list(options.values()), 'bs', 'KBO')
        arms = {}
        for model in MODELS:
            name = 'frontier_' + model
            rates = forecasts[fid][model]
            if rates['fixture_id'] != fid or rates['event_date'] != row['date']:
                raise ValueError('frozen prediction fixture mismatch')
            arm = score_pick(name, probabilities(terms, rates['home_rate'], rates['away_rate']), options)
            if arm:
                arms[name] = arm
        fixtures[fid].append({'comparisons': arms, 'row': row})
    # Outcomes have no role until all model arms and the one fixture pick are fixed.
    selected = []
    for items in fixtures.values():
        choose_final(items)
        for item in items:
            final = item['comparisons'].get(FINAL)
            if final:
                row = item['row']
                inverse = {'언더': 'under', '오버': 'over', '핸디홈': 'home', '핸디원정': 'away'}
                selected.append((row, inverse[final['selection']], final))
    def stats(rows, market=False):
        hits = 0
        nets = []
        for row, pick, _ in rows:
            if market:
                pick = row['market_selection']
            win = pick == row['outcome']
            hits += win
            nets.append(row['prices'][pick]-1 if win else -1)
        n = len(rows)
        return {'n': n, 'hits': hits, 'hit_rate': hits/n if n else None,
                'net_units': round(sum(nets), 6), 'roi': sum(nets)/n if n else None}
    groups = {'all': selected}
    for row, pick, final in selected:
        groups.setdefault(row['market'], []).append((row, pick, final))
    return {'fixtures': len(fixtures), 'products': len(cohort), 'abstained': len(fixtures)-len(selected),
            'comparison': {name: {'final': stats(rows), 'same_product_market': stats(rows, market=True)}
                           for name, rows in groups.items()},
            'retrospective': True, 'pregame_price_receipt_proven': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', required=True, type=Path)
    parser.add_argument('--forecasts', required=True, type=Path)
    args = parser.parse_args()
    cohort, forecasts = args.cohort.read_bytes(), args.forecasts.read_bytes()
    result = replay(json.loads(cohort)['strict_resolved'], json.loads(gzip.decompress(forecasts)))
    print(json.dumps(dict(result, cohort_sha256=hashlib.sha256(cohort).hexdigest(),
                          forecasts_sha256=hashlib.sha256(forecasts).hexdigest()), indent=2))


if __name__ == '__main__':
    main()
