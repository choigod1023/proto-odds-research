"""Fixed 75:25 historical sensitivity check, NOT a promotion backtest.

Existing public-article rows use a reconstructed research baseline, not frozen
production forecasts. One author's selected outcome is a vote, not 100% confidence.
Soccer retains its draw class. No weight search or production writes.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys

SPORTS = ('bs', 'bk', 'sc', 'vl')


def vector(values):
    values = [float(v) for v in values]
    if len(values) not in (2, 3) or any(not math.isfinite(v) or v < 0 or v > 1 for v in values):
        raise ValueError('Invalid probability vector')
    if abs(sum(values) - 1) > 1e-6:
        raise ValueError('Probabilities must sum to one')
    return values


def decisions(row):
    p = vector(row['probability'])
    k = 3 if row['sport'] == 'sc' else 2
    if len(p) != k or len(row['odds']) != k:
        raise ValueError('Market dimension mismatch')
    if type(row['y']) is not int or row['y'] not in range(k):
        raise ValueError('Invalid result')
    if any(not math.isfinite(o) or o <= 1 for o in row['odds']):
        raise ValueError('Invalid odds')
    tip = row['choices']['tipster_only']
    if type(tip) is not int or tip not in range(k):
        raise ValueError('Invalid tipster selection')
    q = vector(row.get('tipster_distribution', [float(i == tip) for i in range(k)]))
    if len(q) != k:
        raise ValueError('Tipster dimension mismatch')
    probabilities = {'baseline': p, 'tipster': q,
                     'hybrid75_25': [.75*a + .25*b for a, b in zip(p, q)]}
    # Fixed index tie-break, never odds or realized result.
    return {name: max(range(k), key=lambda i: values[i]) for name, values in probabilities.items()}


def metrics(rows, name):
    if not rows:
        return {'n': 0, 'hit_rate_pct': None, 'roi_pct': None}
    hits = 0
    units = 0
    for row in rows:
        side = decisions(row)[name]
        hit = side == row['y']
        hits += hit
        units += row['odds'][side] * hit - 1
    return {'n': len(rows), 'hits': hits, 'hit_rate_pct': 100*hits/len(rows),
            'roi_pct': 100*units/len(rows), 'net_units': units}


def evaluate(rows):
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        if row['sport'] not in SPORTS:
            raise ValueError('Unsupported sport')
        key = (row['sport'], row['date'], row['home'], row['away'])
        if key in seen:
            raise ValueError('Duplicate physical game')
        seen.add(key)
        decisions(row)
        groups[row['sport']].append(row)
    names = ('baseline', 'tipster', 'hybrid75_25')
    return {sport: {'scores': {n: metrics(groups[sport], n) for n in names},
                   'leave_one_date_out': {day: {n: metrics([r for r in groups[sport] if r['date'] != day], n)
                                               for n in names} for day in sorted({r['date'] for r in groups[sport]})}}
            for sport in SPORTS}


def run(public_report, games):
    # Heavy dataset joins/training only run offline, never on collector.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from sport_hybrid_validation import load_data, fit, predict
    from model_v2 import SPORT_FEATURES
    original = json.loads(public_report.read_text(encoding='utf8'))
    rows = original['ledger']
    data, quality = load_data(games)
    if hashlib.sha256(games.read_bytes()).hexdigest() != original['games_sha256']:
        raise ValueError('Historical dataset differs from baseline report')
    # New article found by sport/date/team search; not selected for its result.
    # Search index returned the full recommendation; direct site access failed.
    article = {'sport': 'vl', 'date': '2025-04-05', 'home': '대한항공', 'away': '현대캐피',
               'url': 'https://allstv24.com/sports_analysis/10549',
               'displayed_publication': '2025-04-04 21:11 (site timezone unverified)',
               'author': '올스분석2', 'observed_date_kst': '2026-10-25',
               'retrieval': 'search-index full article; direct page unavailable',
               'choices': {'tipster_only': 1}}
    joined = data[(data.sport == 'vl') & (data.date.astype(str) == article['date']) &
                  (data.home_team == article['home']) & (data.away_team == article['away'])]
    if len(joined) != 1:
        raise ValueError('Volleyball event not uniquely matched')
    train = data[(data.sport == 'vl') & (data.year < 2025)]
    if train.date.max() >= joined.date.min():
        raise ValueError('Training leakage')
    cols = list(dict.fromkeys(['elo_diff', *SPORT_FEATURES['vl']]))
    p = predict(fit(train, cols, True), joined)[0].tolist()
    row = joined.iloc[0]
    rows.append(dict(article, probability=p, odds=list(row.odds), y=int(row.y)))
    # Verify all old event/result/odds joins against the pinned underlying data.
    for r in rows:
        match = data[(data.sport == r['sport']) & (data.date.astype(str) == r['date']) &
                     (data.home_team == r['home']) & (data.away_team == r['away'])]
        if len(match) != 1 or int(match.iloc[0].y) != r['y'] or list(match.iloc[0].odds) != r['odds']:
            raise ValueError('Underlying result/odds mismatch')
        r['kickoff_local'] = str(match.iloc[0].kickoff)
        r['league'] = match.iloc[0].league
        r['timing_status'] = 'postevent_backfill_not_pregame_certified'
        r['result_status'] = 'local_score_consistent_not_independently_official_verified'
        r['decisions'] = decisions(r)
    return {'status': 'exploratory_only_no_promotion', 'fixed_tipster_weight': .25,
            'baseline': 'reconstructed market-residual research model, NOT frozen production',
            'vote_encoding': 'single-author one-hot; NOT a calibrated probability or the MLB multi-author cohort',
            'monthly_loss_limit_krw': 50000, 'strict_eligible': 0,
            'two_leg': {'roi_pct': None, 'reason': 'Pregame availability/price timestamps and settlement timestamps unproven; no fictitious replay'},
            'quality': quality, 'volleyball_training': {'n': len(train), 'end': str(train.date.max())},
            'hashes': {str(f.resolve()): hashlib.sha256(f.read_bytes()).hexdigest() for f in (public_report, games, Path(__file__))},
            'sports': evaluate(rows), 'ledger': rows,
            'limitations': ['Tiny convenience sample; selected historical articles can have hindsight edits.',
                            'No independent final holdout; no monthly income or future ROI inference.',
                            'No betting, production change, or promotion; monthly loss cap unchanged.',
                            'New volleyball article outcome was already visible during source retrieval.',
                            'One author per event differs from MLB crowd: isolate sport, period and source effects.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-report', type=Path, required=True)
    parser.add_argument('--games', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run(args.public_report, args.games)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({s: r['scores'] for s, r in report['sports'].items()}, ensure_ascii=False, indent=2))
