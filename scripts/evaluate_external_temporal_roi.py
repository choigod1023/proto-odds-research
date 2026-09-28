"""Offline EPL surrogate experiment. Never imports production storage or writes it.

Frozen protocol: 2016-19 warmup, 2019-23 fit, 2023-24 temperature
calibration, 2024-25 final test. Fixed L2=0.01; no test-driven tuning.
Historical features exclude the preceding seven days. Nonclosing Bet365
prices have no exact observation time: execution/Proto transfer is unproven.
One disjoint two-leg ticket per date, one unit budget on every test date.
No qualifying pair means cash. All H/D/A outcomes are eligible.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import Counter, defaultdict, deque
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from pathlib import Path
import sys
import urllib.request

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.special import softmax

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.devig import shin

REV = 'd69530c645c10c7844ee416a6af56a4b392e37be'
BASE = f'https://raw.githubusercontent.com/AnishKhetani/premier-league-data/{REV}/data/processed/'
POLICIES = ('p60_150_220', 'p60_under220', 'ev03_under220', 'ev03_all_odds')


def payout(a, b):
    return float((Decimal(str(a))*Decimal(str(b))).quantize(
        Decimal('.01'), rounding=ROUND_DOWN).quantize(Decimal('.1'), rounding=ROUND_CEILING))


def download():
    tables, hashes = {}, {}
    for name in ('results.csv', 'results_with_odds.csv'):
        raw = urllib.request.urlopen(BASE+name, timeout=60).read()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
        assert len({r['match_id'] for r in rows}) == len(rows), 'Duplicate match ID'
        tables[name] = {r['match_id']: r for r in rows}
    assert tables['results.csv'].keys() == tables['results_with_odds.csv'].keys()
    merged = []
    for key, result in tables['results.csv'].items():
        if not '2016-17' <= result['season'] <= '2024-25':
            continue
        odds = tables['results_with_odds.csv'][key]
        assert all(result[k] == odds[k] for k in ('date', 'season', 'home_team', 'away_team'))
        row = dict(result)
        row['day'] = date.fromisoformat(row['date'])
        row['y'] = 'HDA'.index(row['ftr'])
        row['goals'] = (int(row['fthg']), int(row['ftag']))
        try:
            prices = [float(odds['bet365_1x2_'+side]) for side in ('home','draw','away')]
            row['prices'] = prices if all(math.isfinite(v) and v > 1 for v in prices) else None
        except (ValueError, TypeError):
            row['prices'] = None
        merged.append(row)
    counts = Counter(r['season'] for r in merged)
    assert all(n == 380 for n in counts.values()), counts
    return sorted(merged, key=lambda r:(r['day'],r['match_id'])), hashes, dict(counts)


def features(rows):
    ratings = defaultdict(lambda:1500.)
    form = defaultdict(lambda:deque(maxlen=5))
    pending = deque()
    output = []
    for row in rows:
        while pending and pending[0]['day'] < row['day']-timedelta(days=7):
            old = pending.popleft()
            h,a = old['home_team'],old['away_team']
            hg,ag = old['goals']
            expected = 1/(1+10**(-(ratings[h]+45-ratings[a])/400))
            delta = 24*((1 if hg>ag else .5 if hg==ag else 0)-expected)
            ratings[h] += delta
            ratings[a] -= delta
            form[h].append(hg-ag)
            form[a].append(ag-hg)
        if row['prices']:
            market = np.array(shin(row['prices']))
            h,a = row['home_team'],row['away_team']
            gap = (ratings[h]-ratings[a])/400
            recent = (np.mean(form[h]) if form[h] else 0)-(np.mean(form[a]) if form[a] else 0)
            x = [1.,gap,recent/3,np.log(market[0]/market[2]),np.log(market[1]/market[2])]
            output.append({**row,'x':x,'market':market})
        pending.append(row)
    return output


def fit(train):
    x = np.array([r['x'] for r in train]); y = np.array([r['y'] for r in train])
    target = np.eye(3)[y]
    def objective(flat):
        w = flat.reshape(x.shape[1],3)
        p = softmax(x@w,axis=1)
        loss = -np.log(p[np.arange(len(y)),y]).mean()+.01*np.square(w[1:]).sum()/2
        grad = x.T@(p-target)/len(y)
        grad[1:] += .01*w[1:]
        return loss,grad.ravel()
    result = minimize(objective,np.zeros(x.shape[1]*3),jac=True,method='L-BFGS-B')
    assert result.success, result.message
    return result.x.reshape(x.shape[1],3)


def qualifying(row, probs, policy):
    options = []
    for i,(p,o) in enumerate(zip(probs,row['prices'])):
        ev = p*o-1
        valid = (p>=.6 and 1.5<=o<2.2) if policy==POLICIES[0] else (
            p>=.6 and o<2.2) if policy==POLICIES[1] else (
            ev>=.03 and o<2.2) if policy==POLICIES[2] else ev>=.03
        if valid:
            options.append((float(ev),float(p),-i,i,o))
    return max(options) if options else None


def evaluate(rows, probs, policy):
    days = defaultdict(list)
    for row,p in zip(rows,probs):
        days[row['date']]
        option = qualifying(row,p,policy)
        if option:
            days[row['date']].append((option,row['match_id'],row))
    profits,rawprofits,hits,expected = [],[],0,[]
    tickets, underdog_legs,low_legs = [],0,0
    for day, choices in sorted(days.items()):
        choices.sort(key=lambda v:(-v[0][0],-v[0][1],v[1]))
        if len(choices)<2:
            profits.append(0.); rawprofits.append(0.); continue
        legs = choices[:2]
        assert legs[0][1] != legs[1][1]
        won = all(option[3]==r['y'] for option,_,r in legs)
        prices = [v[0][4] for v in legs]
        paid = payout(*prices)
        profits.append(paid*won-1)
        rawprofits.append(float(np.prod(prices))*won-1)
        expected.append(float(np.prod([v[0][1] for v in legs]))*paid-1)
        hits += won
        low_legs += sum(o<1.5 for o in prices)
        underdog_legs += sum(op[4]>min(r['prices']) for op,_,r in legs)
        tickets.append({'date':day,'ids':[v[1] for v in legs],'outcomes':[v[0][3] for v in legs]})
    n = len(tickets)
    bet_days = {t['date'] for t in tickets}
    stakes = np.array([int(d in bet_days) for d in sorted(days)])
    rng = np.random.default_rng(20260928)
    idx = rng.integers(0,len(days),(5000,len(days)))
    bs_stakes = stakes[idx].sum(axis=1)
    bs_profit = np.array(profits)[idx].sum(axis=1)
    ci = np.percentile(bs_profit[bs_stakes>0]/bs_stakes[bs_stakes>0],[2.5,97.5]).tolist() if n else None
    return {'tickets':n,'wins':int(hits),'hit_rate':hits/n if n else None,
            'roi_raw':sum(rawprofits)/n if n else None,'roi_rounded':sum(profits)/n if n else None,
            'roi_95ci_date_bootstrap':ci,'profit_units':sum(profits),
            'budget_days':len(days),'return_on_daily_budget':sum(profits)/len(days),
            'model_expected_roi_independence_assumption':np.mean(expected).item() if n else None,
            'low_odds_legs':low_legs,'non_favorite_legs':underdog_legs}


def run():
    rows,hashes,counts = download()
    data = features(rows)
    train = [r for r in data if '2019-20'<=r['season']<='2022-23']
    calibration = [r for r in data if r['season']=='2023-24']
    test = [r for r in data if r['season']=='2024-25']
    w = fit(train)
    cx = np.array([r['x'] for r in calibration]); cy = np.array([r['y'] for r in calibration])
    def loss(t):
        p = softmax(cx@w/t,axis=1)
        return -np.log(p[np.arange(len(cy)),cy]).mean()
    opt = minimize_scalar(loss,bounds=(.5,2.),method='bounded')
    assert opt.success
    learned = softmax(np.array([r['x'] for r in test])@w/opt.x,axis=1)
    market = np.array([r['market'] for r in test]); y = np.array([r['y'] for r in test])
    report = {'source_commit':REV,'sha256':hashes,'season_counts':counts,
              'usable_split_counts':[len(train),len(calibration),len(test)],
              'excluded_missing_odds':len(rows)-len(data),'temperature':float(opt.x),'models':{}}
    for name,p in [('shin_market',market),('learned_calibrated',learned)]:
        report['models'][name] = {'brier_multiclass':float(np.square(p-np.eye(3)[y]).sum(axis=1).mean()),
            'logloss':float(-np.log(p[np.arange(len(y)),y]).mean()),
            'policies':{policy:evaluate(test,p,policy) for policy in POLICIES}}
    return report


if __name__=='__main__':
    print(json.dumps(run(),indent=2,allow_nan=False))
