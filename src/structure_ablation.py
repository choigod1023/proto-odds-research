"""Exploratory structural refactor: chronological selection, no live promotion."""
from __future__ import annotations
import argparse
from itertools import combinations_with_replacement
import json
from pathlib import Path
import numpy as np
import pandas as pd
from external_feature_validation import collect, prepare, attach_features, LEGACY, evaluate
from sport_hybrid_validation import fit, predict

NAMES = ('legacy', 'league_experts', 'hurdle', 'quadratic')
WEIGHTS = (1., .75, .5, .25, 0.)  # Ties prefer market.


def quadratic(data):
    d = data.copy()
    cols = list(LEGACY)
    for a, b in combinations_with_replacement(LEGACY, 2):
        name = f'product_{a}_{b}'
        d[name] = d[a] * d[b]
        cols.append(name)
    return d, cols


def binary(data, draw):
    d = data.copy()
    p = np.array(d.market.tolist())
    q = p[:, 1] if draw else p[:, 0] / (p[:, 0] + p[:, 2])
    d['market'] = np.column_stack((1-q, q)).tolist()
    if 'y' in d:
        d['y'] = (d.y == (1 if draw else 0)).astype(int)
    return d


def train_model(name, data):
    if name == 'legacy':
        return fit(data, LEGACY, True)
    if name == 'quadratic':
        d, cols = quadratic(data)
        return fit(d, cols, True)
    if name == 'league_experts':
        return (fit(data, LEGACY, True),
                {k:fit(g, LEGACY, True) for k,g in data.groupby('league') if len(g)>=300})
    if name == 'hurdle':
        return fit(binary(data, True), LEGACY, True), fit(binary(data[data.y!=1], False), LEGACY, True)
    raise ValueError(name)


def forecast(name, model, data):
    if name == 'legacy': return predict(model, data)
    if name == 'quadratic': return predict(model, quadratic(data)[0])
    if name == 'league_experts':
        global_model, local = model
        p = predict(global_model, data)
        for league in data.league.unique():
            mask = data.league.eq(league).to_numpy()
            if league in local:
                p[mask] = .5*p[mask] + .5*predict(local[league], data.loc[mask])
        return p
    if name == 'hurdle':
        draw = predict(model[0], binary(data, True))[:,1]
        home = predict(model[1], binary(data, False))[:,1]
        return np.column_stack(((1-draw)*home, draw, (1-draw)*(1-home)))
    raise ValueError(name)


def logloss(p, y):
    return float(-np.log(np.clip(p[np.arange(len(y)), np.asarray(y,int)], 1e-12, 1)).mean())


def select(predictions, market, labels):
    choices = {}
    for name, p in predictions.items():
        w = min(WEIGHTS, key=lambda v:logloss(v*market+(1-v)*p, labels))
        choices[name] = dict(market_weight=w, loss=logloss(w*market+(1-w)*p, labels))
    winner = min(choices, key=lambda name:choices[name]['loss'])
    return choices, winner


def split(data, season):
    train = data[data.season < season-1]
    valid = data[data.season == season-1]
    test = data[data.season == season]
    if any(d.empty for d in (train,valid,test)) or not train.date.max()<valid.date.min()<=valid.date.max()<test.date.min():
        raise ValueError('Empty or overlapping chronological partitions')
    return train,valid,test


def concentration(data, p, weekly):
    """Audit the same paper rule; expose dependence on rare large payouts."""
    a=data.copy(); odds=np.array(a.odds.tolist())
    ev=p*odds-1; pick=ev.argmax(1)
    a['edge']=ev[np.arange(len(a)),pick]
    a['price']=odds[np.arange(len(a)),pick]
    a['hit']=pick==a.y.to_numpy()
    months, weeks, tickets = {}, {}, []
    for day,rows in a.groupby('date',sort=True):
        month=str(day.to_period('M')); week=tuple(day.isocalendar()[:2])
        months.setdefault(month,0.)
        if months[month]-10000 < -50000 or weeks.get(week,0)>=weekly: continue
        pair=rows[rows.edge>0].sort_values(['edge','league','home_team','away_team'],ascending=[False,True,True,True]).head(2)
        if len(pair)<2: continue
        price=float(pair.price.prod()); hit=bool(pair.hit.all())
        profit=10000*(price*hit-1)
        months[month]+=profit; weeks[week]=weeks.get(week,0)+1
        tickets.append(dict(date=str(day.date()),odds=price,hit=hit,profit=profit))
    profits=[t['profit'] for t in tickets]
    return dict(tickets=len(tickets), wins=sum(t['hit'] for t in tickets),
                net=round(sum(profits)), net_without_best_ticket=round(sum(profits)-max(profits,default=0)),
                positive_months=sum(v>0 for v in months.values()),months=len(months),
                largest_tickets=sorted(tickets,key=lambda t:t['profit'],reverse=True)[:3])


def run(cache):
    raw, manifest = collect(cache)
    matches, quality = prepare(raw)
    data = attach_features(matches)
    chunks, predictions, folds = [], {}, []
    for season in (2023,2024,2025):
        train,valid,test = split(data, season)
        vp = {n:forecast(n,train_model(n,train),valid) for n in NAMES}
        choices,winner = select(vp,np.array(valid.market.tolist()),valid.y.to_numpy())
        all_train = data[data.season<season]
        tp = {n:forecast(n,train_model(n,all_train),test) for n in NAMES}
        market = np.array(test.market.tolist())
        for n in NAMES:
            w = choices[n]['market_weight']
            tp[n+'_selected_blend'] = w*market+(1-w)*tp[n]
        tp['selector'] = tp[winner+'_selected_blend']
        tp['market'] = market
        for n,p in tp.items():
            if not np.isfinite(p).all() or (p<0).any() or not np.allclose(p.sum(1),1):
                raise ValueError('Invalid probabilities')
            predictions.setdefault(n,[]).append(p)
        chunks.append(test)
        folds.append(dict(season=season,fit_rows=len(train),selection_rows=len(valid),test_rows=len(test),
                          selection=choices,winner=winner))
    out = pd.concat(chunks,ignore_index=True)
    ps = {n:np.concatenate(p) for n,p in predictions.items()}
    audits={n:{str(w):concentration(out,p,w) for w in (2,3)} for n,p in ps.items()}
    metrics={n:evaluate(out,p,ps['legacy']) for n,p in ps.items()}
    for n in ps:
        for w in ('2','3'):
            if any(audits[n][w][k]!=metrics[n]['paper'][w][k] for k in ('net','tickets')):
                raise ValueError('Paper audit mismatch')
    return dict(manifest=manifest,quality=quality,folds=folds,paper_concentration=audits,
                limitation='Previously inspected external sample; exploratory, not independent confirmation or live saved picks',
                models=metrics)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=run(args.cache)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({n:{k:r[k] for k in ('accuracy','roi','log_loss')} for n,r in result['models'].items()}))
