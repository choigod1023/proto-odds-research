"""Fixed offline, same-fixture hit-rate comparison. Never changes production."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from devig import market_probabilities

START, END = '2025-10-29', '2026-10-28'
MODELS = {'market': 1., 'market90_vote10': .9, 'market75_vote25': .75,
          'market50_vote50': .5, 'tipster_vote': 0.}
LEAGUES = ('K리그1', 'K리그2', 'KBO', 'NPB', 'MLB', 'KBL', 'NBA', '배구')


def interval(days, nums, dens, repetitions=2000):
    if not len(days) == len(nums) == len(dens):
        raise ValueError('bootstrap vectors must have the same length')
    groups = defaultdict(lambda: [0., 0.])
    for day, num, den in zip(days, nums, dens):
        d = date.fromisoformat(day)
        groups[(d-timedelta(days=d.weekday())).isoformat()][0] += num
        groups[(d-timedelta(days=d.weekday())).isoformat()][1] += den
    if len(groups) < 2:
        return dict(low=None, high=None, observed_weeks=len(groups))
    data = np.array([groups[k] for k in sorted(groups)])
    rng = np.random.default_rng(20261029)
    sums = data[rng.integers(len(data), size=(repetitions, len(data)))].sum(axis=1)
    valid = sums[:, 1] > 0
    if not valid.any():
        return dict(low=None, high=None, observed_weeks=len(groups))
    ratios = sums[valid, 0]/sums[valid, 1]
    lo, hi = np.quantile(ratios, [.025, .975])
    return dict(low=float(lo), high=float(hi), observed_weeks=len(groups),
                repetitions=repetitions, method='observed-week cluster bootstrap; retrospective')


def valid_prices(value, size):
    if not isinstance(value, list) or len(value) != size:
        raise ValueError('invalid full price vector')
    if any(isinstance(v, bool) or not isinstance(v, (float, int))
           or not math.isfinite(v) or v <= 1 for v in value):
        raise ValueError('invalid full price vector')
    return tuple(float(v) for v in value)


def events_from_rows(rows):
    groups, excluded = defaultdict(list), Counter()
    for row in rows:
        if not START <= row.get('match_date', '') <= END:
            excluded['outside_window'] += 1
            continue
        try:
            date.fromisoformat(row['match_date'])
            if row.get('market') != 'moneyline' or row['league'] not in LEAGUES:
                raise ValueError('unsupported market/league')
            if row['league'] not in {'K리그1', 'K리그2'} and row.get('period') != 'full_game_including_overtime':
                raise ValueError('unverified settlement scope')
            if row.get('result_evidence') != 'official_exact_fixture' or not row.get('result_source_url'):
                raise ValueError('missing official result')
            if not row.get('fixture_id') or any(type(row.get(k)) is not int or row[k] < 0
                                                for k in ('home_score', 'away_score')):
                raise ValueError('invalid fixture/score')
            groups[(row['league'], row['fixture_id'])].append(row)
        except (KeyError, TypeError, ValueError) as exc:
            excluded[str(exc)] += 1
    events = []
    for (league, fixture_id), batch in sorted(groups.items()):
        outcomes = {(r['home_score'], r['away_score'], r['match_date']) for r in batch}
        if len(outcomes) != 1:
            excluded['inconsistent official fixture'] += 1
            continue
        order = ['home', 'draw', 'away'] if league in {'K리그1', 'K리그2'} else ['home', 'away']
        try:
            quotes = {valid_prices(r['proto_odds'], len(order)) for r in batch
                      if r.get('proto_offer_evidence') == 'archived_exact_fixture'}
        except ValueError:
            excluded['invalid full price vector'] += 1
            continue
        if len(quotes) != 1:
            excluded['missing or conflicting prices'] += 1
            continue
        votes = defaultdict(set)
        for r in batch:
            if r.get('side') in order:
                votes[(r['source'], r.get('author') or '<publisher>')].add(r['side'])
        directions = [next(iter(v)) for v in votes.values() if len(v) == 1]
        if not directions:
            excluded['no unambiguous voters'] += 1
            continue
        counts = Counter(directions)
        prices = list(next(iter(quotes)))
        probabilities = market_probabilities(prices)
        shares = [counts[s]/len(directions) for s in order]
        picks = {}
        for name, weight in MODELS.items():
            if name == 'tipster_vote':
                leaders = [s for s in order if counts[s] == max(counts.values())]
                picks[name] = leaders[0] if len(leaders) == 1 else None
            else:
                vector = probabilities if weight == 1 else [
                    round(weight*p+(1-weight)*v, 4) for p, v in zip(probabilities, shares)]
                picks[name] = order[max(range(len(order)), key=vector.__getitem__)]
        h, a, day = next(iter(outcomes))
        events.append(dict(league=league, fixture_id=fixture_id, match_date=day,
            kickoff=batch[0].get('kickoff'), home=batch[0].get('home'), away=batch[0].get('away'),
            winner='home' if h > a else 'away' if a > h else 'draw',
            order=order, prices=prices, picks=picks, voters=len(directions), vote_counts=dict(counts)))
    return events, dict(excluded), len(groups)


def result(event, model):
    pick = event['picks'][model]
    if pick is None:
        return None
    if event['winner'] == 'draw' and len(event['order']) == 2:
        return 'push'
    return 'win' if pick == event['winner'] else 'loss'


def unit_net(event, model):
    state = result(event, model)
    if state is None:
        return None
    return (event['prices'][event['order'].index(event['picks'][model])]-1
            if state == 'win' else -1. if state == 'loss' else 0.)


def capped(events, model):
    days = defaultdict(list)
    for e in events:
        if result(e, model) is not None:
            days[e['match_date']].append(e)
    monthly, ledger = defaultdict(int), []
    for day, batch in sorted(days.items()):
        month = day[:7]
        chosen = []
        for e in sorted(batch, key=lambda x: (x.get('kickoff') or day, x['league'], x['fixture_id'])):
            if monthly[month]-10000*(len(chosen)+1) >= -50000:
                chosen.append(e)
        for e in chosen:
            state = result(e, model)
            price = e['prices'][e['order'].index(e['picks'][model])]
            net = (int((Decimal(str(price))*10000).to_integral_value(rounding=ROUND_DOWN))-10000
                   if state == 'win' else -10000 if state == 'loss' else 0)
            monthly[month] += net
            ledger.append(dict(fixture_id=e['fixture_id'], league=e['league'], date=day, net_krw=net))
        assert monthly[month] >= -50000
    return dict(bets=len(ledger), profit_krw=sum(monthly.values()), monthly_net=dict(monthly),
                monthly_loss_limit_krw=50000, stake_krw=10000,
                settlement_timing='next-day proxy; same-day exposure fully reserved', ledger=ledger)


def describe(events, model, repetitions=2000, with_interval=True):
    chosen = [e for e in events if result(e, model) is not None]
    scored = [e for e in chosen if result(e, model) != 'push']
    wins = sum(result(e, model) == 'win' for e in scored)
    return dict(pool_events=len(events), selected=len(chosen), abstained=len(events)-len(chosen),
        coverage=len(chosen)/len(events) if events else None, wins=wins, losses=len(scored)-wins,
        pushes=len(chosen)-len(scored), hit_rate=wins/len(scored) if scored else None,
        hit_interval=interval([e['match_date'] for e in scored],
            [int(result(e, model) == 'win') for e in scored], [1]*len(scored), repetitions)
            if with_interval else None,
        unit_roi=sum(unit_net(e, model) for e in chosen)/len(chosen) if chosen else None)


def compare(events, repetitions=2000):
    common = [e for e in events if all(result(e, m) is not None for m in MODELS)]
    differences = {}
    for model in MODELS:
        if model == 'market':
            continue
        shared = [e for e in common if result(e, 'market') != 'push']
        delta = [int(result(e, model) == 'win')-int(result(e, 'market') == 'win') for e in shared]
        differences[model] = dict(n=len(shared), hit_rate_difference=sum(delta)/len(delta) if delta else None,
            changed_picks=sum(e['picks'][model] != e['picks']['market'] for e in shared),
            market_only_correct=delta.count(-1), model_only_correct=delta.count(1),
            interval=interval([e['match_date'] for e in shared], delta, [1]*len(delta), repetitions))
    return dict(priced_pool=len(events), common_selected=len(common),
        model_specific={m:describe(events, m, repetitions) for m in MODELS},
        common={m:describe(common, m, repetitions) for m in MODELS},
        differences_vs_market=differences)


def evaluate(rows, repetitions=2000):
    events, rejected, official = events_from_rows(rows)
    dates = sorted(e['match_date'] for e in events)
    months = sorted({e['match_date'][:7] for e in events})
    midpoint = (date.fromisoformat(START)+timedelta(days=182)).isoformat()
    return dict(requested_window=[START, END], input_rows=len(rows),
        official_moneyline_fixtures=official, first_priced_date=dates[0] if dates else None,
        last_priced_date=dates[-1] if dates else None, observed_months=months,
        rows_claiming_independent_pregame_evidence=sum(r.get('cutoff_proven') is True
            and bool(r.get('independent_pregame_evidence_url')) for r in rows),
        exclusions=rejected, comparison=compare(events, repetitions),
        by_league={league:compare([e for e in events if e['league'] == league], repetitions)
                   for league in LEAGUES},
        monthly={month:{m:describe([e for e in events if e['match_date'].startswith(month)],
                                  m, repetitions, False) for m in MODELS} for month in months},
        halves={name:compare([e for e in events if (e['match_date'] < midpoint) == early], repetitions)
                for name, early in [('earlier', True), ('later', False)]},
        capped_paper={m:capped(events, m) for m in MODELS}, event_ledger=events,
        operating_model_replay=False, production_changes=False,
        caveat='Previously seen retrospective available-record sample; price purchase time and pregame versions unproven.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    rows = [json.loads(line) for line in raw.decode('utf8').splitlines() if line.strip()]
    report = evaluate(rows)
    report['input_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
    print(json.dumps({k:report[k] for k in ('requested_window', 'official_moneyline_fixtures',
        'first_priced_date', 'last_priced_date', 'exclusions')}, ensure_ascii=False))
