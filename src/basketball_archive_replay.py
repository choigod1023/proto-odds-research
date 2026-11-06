"""Retrospective basketball archive replay; inputs are archived_products tuples.

2025 fixtures only fit population moments; 2026 fixtures are evaluated without
updates. Team residuals use fixed 10-game zero-mean shrinkage. This is not a
prospectively frozen forecast and archive prices are not executable quotes.
"""
from collections import Counter, defaultdict
from math import erf, log, sqrt, isfinite

LEAGUES = ('KBL', 'NBA')
SHRINK = 10


def fixture(item):
    k = item[2]
    return (k[0], k[1], k[4], k[5])


def prepare(products):
    rows = [p for p in products if p[2][0] in LEAGUES and p[3].kind in ('total', 'handicap') and p[2][1].year in (2025, 2026)]
    scores = defaultdict(set)
    for p in rows:
        scores[fixture(p)].add(tuple(p[6]))
    bad = {k for k, v in scores.items() if len(v) != 1}
    return rows, scores, bad


def fit(products):
    _, scores, bad = prepare(p for p in products if p[2][1].year == 2025)
    groups = defaultdict(list)
    for k, values in scores.items():
        if k not in bad:
            h, a = next(iter(values))
            groups[k[0]].append((k, h+a, h-a))
    models = {}
    for league, games in groups.items():
        if len(games) < 2:
            continue
        means = [sum(g[i] for g in games)/len(games) for i in (1, 2)]
        sd = [sqrt(sum((g[i]-means[i-1])**2 for g in games)/len(games)) for i in (1, 2)]
        if min(sd) <= 0: continue
        residuals = defaultdict(lambda: [0., 0., 0])
        for key, total, margin in games:
            for team, sign in ((key[2], 1), (key[3], -1)):
                v = residuals[team]; v[0] += total-means[0]; v[1] += sign*(margin-means[1]); v[2] += 1
        models[league] = {'n': len(games), 'means': means, 'sd': sd,
                          'teams': {t: (v[0]/(v[2]+SHRINK), v[1]/(v[2]+SHRINK)) for t,v in residuals.items()}}
    return models


def probability(model, key, market, variant):
    i = 0 if market.kind == 'total' else 1
    mean = model['means'][i]
    if variant == 'team':
        home, away = (model['teams'].get(t, (0., 0.)) for t in key[2:4])
        mean += (home[0]+away[0])/2 if i == 0 else (home[1]-away[1])/2
    threshold = float(market.line) if i == 0 else -float(market.line)
    if not isfinite(threshold) or abs(threshold*2-round(threshold*2)) > 1e-8 or round(threshold*2)%2 != 1:
        raise ValueError('only half-point lines supported')
    cdf = .5*(1+erf((threshold-mean)/(model['sd'][i]*sqrt(2))))
    return cdf if i == 0 else 1-cdf  # under or home


def replay(products):
    products = list(products)
    models = fit(products)
    rows, scores, bad = prepare(products)
    quotes = defaultdict(list)
    for p in rows:
        if p[2][1].year == 2026:
            quotes[(fixture(p), p[3].kind)].append(p)
    groups = defaultdict(lambda: {'counts': Counter(), 'arms': defaultdict(list)})
    for (key, kind), candidates in sorted(quotes.items(), key=lambda x: x[0][0][1]):
        result = groups[(key[0],kind)]; c = result['counts']; c['fixture_market_groups'] += 1
        if key in bad: c['conflicting_score'] += 1; continue
        if len(candidates) != 1: c['ambiguous_quotes'] += 1; continue
        if key[0] not in models: c['missing_training'] += 1; continue
        p = candidates[0]; market, prices, outcome = p[3],p[4],p[5]
        side, other = ('under','over') if kind == 'total' else ('home','away')
        if outcome not in (side,other) or set(prices) != {side,other}: c['invalid_outcome_or_prices'] += 1; continue
        try:
            if any(not isfinite(float(prices[s])) or float(prices[s]) <= 1 for s in (side,other)): raise ValueError()
            static = probability(models[key[0]],key,market,'static')
            team = probability(models[key[0]],key,market,'team')
        except (ValueError, TypeError, OverflowError): c['invalid_line_or_price'] += 1; continue
        implied = 1/prices[side]; market_p = implied/(implied+1/prices[other]); y = int(outcome==side)
        for name, prob in (('static',static),('team',team),('market',market_p)):
            selection = side if prob >= .5 else other
            net = prices[selection] - 1 if selection == outcome else -1.
            result['arms'][name].append((min(1-1e-15,max(1e-15,prob)), y, net))
        c['evaluated'] += 1
    output = {}
    for (league,kind), result in sorted(groups.items()):
        arms = {}
        for name, vals in result['arms'].items():
            n=len(vals)
            hits = sum((p>=.5)==bool(y) for p,y,_ in vals)
            arms[name]={'n':n,'hits':hits,'accuracy':hits/n,
                        'roi':sum(net for _,_,net in vals)/n,
                        'brier':sum((p-y)**2 for p,y,_ in vals)/n,
                        'log_loss':sum(-log(p if y else 1-p) for p,y,_ in vals)/n}
        output[league+'/'+kind]={'counts':dict(result['counts']),'arms':arms}
    return {'training': {k:{'fixtures':v['n'],'means_total_margin':v['means'],'sd_total_margin':v['sd'],'teams':len(v['teams'])} for k,v in models.items()},'groups':output}
