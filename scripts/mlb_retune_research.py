"""Frozen, offline MLB run-generation research; no production imports or writes."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone, date
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit
from scipy.stats import nbinom, skellam

UTC = timezone.utc
KST = timezone(timedelta(hours=9))
DEFAULT = Path(__file__).resolve().parents[1]/'experiments/baseball-league-retune-20261029/mlb'
EN = 'Los Angeles Dodgers|Arizona Diamondbacks|New York Yankees|San Francisco Giants|Miami Marlins|New York Mets|Seattle Mariners|Cleveland Guardians|Cincinnati Reds|Pittsburgh Pirates|Los Angeles Angels|Kansas City Royals|Minnesota Twins|Texas Rangers|Philadelphia Phillies|Houston Astros|Chicago White Sox|San Diego Padres|Colorado Rockies|Boston Red Sox|Baltimore Orioles|St. Louis Cardinals|Toronto Blue Jays|Chicago Cubs|Milwaukee Brewers|Washington Nationals|Atlanta Braves|Tampa Bay Rays|Detroit Tigers|Athletics'.split('|')
KO = 'LA다저스 애리조나 뉴욕양키스 샌프란시스코 마이애미 뉴욕메츠 시애틀 클리블랜드 신시내티 피츠버그 LA에인절스 캔자스시티 미네소타 텍사스 필라델피아 휴스턴 시카고W 샌디에이고 콜로라도 보스턴 볼티모어 세인트루이스 토론토 시카고컵스 밀워키 워싱턴 애틀랜타 탬파베이 디트로이트 애슬레틱스'.split()
NAMES = ['intercept', 'home', 'log_attack', 'log_opponent_defense', 'venue_attack_deviation',
         'venue_defense_deviation', 'opponent_games3', 'opponent_long_minutes3', 'density_x_defense14']


def stamp(s):
    d = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('timezone required')
    return d


def read(p):
    op = gzip.open if p.suffix == '.gz' else open
    with op(p, 'rt', encoding='utf8') as f:
        return [json.loads(s) for s in f if s.strip()]


def write(p, value, lines=False):
    p.parent.mkdir(parents=True, exist_ok=True)
    text = ''.join(json.dumps(r, ensure_ascii=False, allow_nan=False)+'\n' for r in value) if lines else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    p.write_text(text, encoding='utf8', newline='\n')


def digest(p):
    b = p.read_bytes()
    return hashlib.sha256(b.replace(b'\r\n', b'\n') if p.suffix in {'.json', '.jsonl', '.md', '.py'} else b).hexdigest()


def canonical(s):
    aliases = dict(zip(KO, EN))
    aliases.update({s:s for s in EN})
    aliases.update({'Oakland Athletics':'Athletics', '오클랜드':'Athletics'})
    return aliases.get(s)


def market(prices):
    """Two-way Shin algebra: p(home)=(1+1/oh-1/oa)/2, also additive."""
    if len(prices) != 2 or not all(math.isfinite(x) and x > 1 for x in prices):
        raise ValueError('invalid two-way odds')
    return (1+1/prices[0]-1/prices[1])/2


def completion(g):
    info = g.get('gameInfo', {})
    if (g.get('status', {}).get('codedGameState') != 'F' or g.get('resumeDate') or g.get('resumedFrom')
            or not info.get('firstPitch') or not info.get('gameDurationMinutes')):
        return None
    return stamp(info['firstPitch'])+timedelta(minutes=float(info['gameDurationMinutes'])+float(info.get('delayDurationMinutes', 0))+60)


def prepare(folder, predecessors):
    """One-time source extraction. Subsequent run is isolated from predecessors."""
    if (folder/'sources/manifest.json').exists():
        raise ValueError('sources already frozen; use run, do not silently replace')
    src = folder/'sources'
    src.mkdir(parents=True, exist_ok=True)
    roots = {n:predecessors/n/'experiments'/n for n in [
        'baseball-feature-partitions-20261029', 'baseball-league-fit-20261029',
        'baseball-event-form-bullpen-20261029', 'baseball-bullpen-external-periods-20261029']}
    provenance = []

    def extract(wt, name, out, predicate=lambda r:True, transform=lambda r:r):
        p = roots[wt]/name
        rows = [transform(r) for r in read(p) if predicate(r)]
        write(src/out, rows, lines=True)
        provenance.append(dict(source=str(p), source_sha256=digest(p), output=out, rows=len(rows)))

    extract('baseball-feature-partitions-20261029', 'other-period-price-facts.jsonl', 'archived-prices.jsonl', lambda r:r['league']=='MLB')
    extract('baseball-league-fit-20261029', 'mlb-official-final.jsonl', 'official-final.jsonl')
    extract('baseball-bullpen-external-periods-20261029', 'mlb-external-features.jsonl', 'external230.jsonl')
    cutoffs = {r['gamePk']:r['proof']['cutoff_at'] for r in read(roots['baseball-event-form-bullpen-20261029']/'event-features.jsonl') if r['role']=='evaluation'}
    extract('baseball-event-form-bullpen-20261029', 'cohort-inputs.jsonl', 'secondary116.jsonl',
            lambda r:r['role']=='evaluation', lambda r:dict(r, cutoff_at=cutoffs[r['gamePk']]))
    for year in (2024, 2025, 2026):
        p = roots['baseball-event-form-bullpen-20261029']/f'sources/schedule-{year}.jsonl.gz'
        shutil.copyfile(p, src/p.name)
        provenance.append(dict(source=str(p), source_sha256=digest(p), output=p.name))
    # Exactly one public primary-source request, never per-player API expansion.
    import requests
    response = requests.get('https://statsapi.mlb.com/api/v1/schedule', params=dict(
        sportId=1, startDate='2023-01-01', endDate='2023-12-31', gameTypes='R,F,D,L,W', hydrate='gameInfo'), timeout=60)
    response.raise_for_status()
    raw = response.json()
    p = src/'schedule-2023.jsonl.gz'
    with gzip.open(p, 'wt', encoding='utf8', newline='\n') as f:
        f.write(json.dumps(raw, ensure_ascii=False)+'\n')
    provenance.append(dict(source=response.url, response_sha256=hashlib.sha256(response.content).hexdigest(),
                           collected_at=datetime.now(UTC).isoformat(), output=p.name))
    write(src/'manifest.json', dict(provenance=provenance, files={p.name:digest(p) for p in src.iterdir() if p.is_file()}))


def load(folder):
    src = folder/'sources'
    manifest = json.loads((src/'manifest.json').read_text(encoding='utf8'))
    for name, expected in manifest['files'].items():
        assert digest(src/name) == expected, name
    games = {}
    for year in (2023, 2024, 2025, 2026):
        raw = read(src/f'schedule-{year}.jsonl.gz')[0]
        for g in raw.get('games', []) or [g for d in raw.get('dates', []) for g in d['games']]:
            games[g['gamePk']] = g
    official = {r['gamePk']:r for r in read(src/'official-final.jsonl')}
    history = []
    guard_counts = Counter()
    for pk, g in games.items():
        end = completion(g)
        if end is None:
            guard_counts['history_missing_or_unsafe_completion'] += 1
            continue
        if pk not in official:
            guard_counts['history_not_in_preserved_official_finals'] += 1
            continue
        o = official[pk]
        h, a = g['teams']['home'], g['teams']['away']
        assert (h['score'], a['score']) == (o['home_score'], o['away_score'])
        assert canonical(h['team']['name']) == canonical(o['home'])
        assert canonical(a['team']['name']) == canonical(o['away'])
        history.append(dict(gamePk=pk, home=canonical(o['home']), away=canonical(o['away']),
            hg=h['score'], ag=a['score'], start=stamp(g['gameDate']), day=g['officialDate'],
            end=end, duration=float(g['gameInfo']['gameDurationMinutes']), season=int(g['officialDate'][:4])))
    history.sort(key=lambda r:(r['start'], r['gamePk']))
    pair = defaultdict(list)
    for o in official.values():
        key = (stamp(o['start_at']).astimezone(KST).date().isoformat(), canonical(o['home']), canonical(o['away']))
        pair[key].append(o)

    exclusions = []

    def make(o, r, cutoff, eval_set, kind, prices, order):
        g = games.get(o['gamePk'])
        if not g or canonical(o['home']) is None or canonical(o['away']) is None:
            raise ValueError('unmapped_game')
        if (r['hg'], r['ag']) != (o['home_score'], o['away_score']):
            raise ValueError('score_disagrees')
        if cutoff >= stamp(g['gameDate']):
            raise ValueError('decision_not_before_scheduled_start')
        first = g.get('gameInfo', {}).get('firstPitch')
        if first and cutoff >= stamp(first):
            raise ValueError('decision_not_before_actual_firstpitch')
        ph = prices[order.index('home')]
        pa = prices[order.index('away')]
        mp = market([ph, pa])
        base = mp
        if kind == 'research_market90_vote10':
            t = r['target']
            assert t['voters'] > 0 and sum(t['vote_counts'].values()) == t['voters']
            base = .9*mp+.1*t['vote_counts'].get('home', 0)/t['voters']
        return dict(league='MLB', gamePk=o['gamePk'], fixture_id=f"MLB:{o['gamePk']}", date=r['date'],
            home=canonical(o['home']), away=canonical(o['away']), hg=r['hg'], ag=r['ag'],
            prices=prices, order=order, market_probability=mp, baseline_probability=base, baseline_kind=kind,
            eval_set=eval_set, role=eval_set, cutoff=cutoff.isoformat(), official_day=g['officialDate'],
            start=stamp(g['gameDate']).isoformat())

    rows = defaultdict(list)
    for r in read(src/'archived-prices.jsonl'):
        try:
            matches = pair[(r['date'], canonical(r['home']), canonical(r['away']))]
            if len(matches) != 1:
                raise ValueError('ambiguous_or_absent_team_date_join')
            o = matches[0]
            cutoff = min(datetime.fromisoformat(r['date']).replace(tzinfo=KST), stamp(o['start_at'])-timedelta(minutes=10))
            role = 'train2023' if r['date'][:4]=='2023' else 'validation2024'
            rows[role].append(make(o, r, cutoff, role, 'market_only', r['historical_prices'], ['home', 'away']))
        except ValueError as e:
            exclusions.append(dict(set='archive', date=r['date'], home=r['home'], away=r['away'], reason=str(e)))
    for filename, cohort, kind in [('external230.jsonl', 'external230', 'market_only'),
                                    ('secondary116.jsonl', 'secondary116', 'research_market90_vote10')]:
        for r in read(src/filename):
            try:
                t = r.get('target', {})
                prices = t.get('prices', r.get('historical_prices'))
                order = t.get('order', ['home', 'away'])
                cutoff = stamp(r.get('decision_at', r.get('cutoff_at')))
                rows[cohort].append(make(official[r['gamePk']], r, cutoff, cohort, kind, prices, order))
            except ValueError as e:
                exclusions.append(dict(set=cohort, gamePk=r['gamePk'], reason=str(e)))
    for name in rows:
        rows[name].sort(key=lambda r:(r['date'], r['gamePk']))
        assert len({r['gamePk'] for r in rows[name]}) == len(rows[name])
    assert not {r['gamePk'] for r in rows['external230']} & {r['gamePk'] for r in rows['secondary116']}
    score_rows = []
    for h in history:
        if h['season'] not in (2023, 2024):
            continue
        o = official[h['gamePk']]
        d = h['start'].astimezone(KST).date().isoformat()
        cutoff = min(datetime.fromisoformat(d).replace(tzinfo=KST), h['start']-timedelta(minutes=10))
        score_rows.append(dict(gamePk=h['gamePk'], home=h['home'], away=h['away'], hg=h['hg'], ag=h['ag'],
            cutoff=cutoff.isoformat(), official_day=h['day'], season=h['season']))
    audit = dict(history_n=len(history), history_source_guards=dict(guard_counts), exclusions=exclusions,
        retained={n:len(rs) for n,rs in rows.items()}, score_training={str(y):sum(r['season']==y for r in score_rows) for y in (2023,2024)},
        date_ranges={n:[rs[0]['date'],rs[-1]['date']] for n,rs in rows.items()},
        secondary_years=dict(Counter(r['date'][:4] for r in rows['secondary116'])))
    return history, score_rows, rows, audit


class FeatureBuilder:
    def __init__(self, history):
        self.history = history

    def features(self, target, half_life):
        cutoff = stamp(target['cutoff'])
        year = int(target['official_day'][:4])
        past = [g for g in self.history if g['gamePk'] != target['gamePk'] and
                g['end'] < cutoff and g['day'] < target['official_day'] and year-1 <= g['season'] <= year]
        # Only already completed games define each season's run environment.
        env = {}
        for season in (year-1, year):
            gs = [g for g in past if g['season']==season]
            env[season] = (sum(g['hg']+g['ag'] for g in gs)+60*4.5)/(2*len(gs)+60)
        current = [g for g in past if g['season']==year]
        mu = (sum(g['hg']+g['ag'] for g in current)+60*env[year-1])/(2*len(current)+60)
        proof = dict(latest_completion=max((g['end'] for g in past), default=None),
            cutoff=target['cutoff'], environment=env, mu=mu,
            sides={})

        def team(team, venue):
            gs = [g for g in past if team in (g['home'], g['away'])]
            w = [2**(-(cutoff-g['start']).total_seconds()/86400/half_life)*(.5 if g['season']<year else 1) for g in gs]
            attack, defense, va, vd, vw = 20., 20., 30., 30., 30.
            for g, weight in zip(gs, w):
                home = g['home']==team
                rs, ra = (g['hg'],g['ag']) if home else (g['ag'],g['hg'])
                attack += weight*rs/env[g['season']]
                defense += weight*ra/env[g['season']]
                if home == venue:
                    va += weight*rs/env[g['season']]
                    vd += weight*ra/env[g['season']]
                    vw += weight
            a, d = attack/(20+sum(w)), defense/(20+sum(w))
            recent3 = [g for g in gs if 0 < (date.fromisoformat(target['official_day'])-date.fromisoformat(g['day'])).days <= 3]
            recent14 = [g for g in gs if (cutoff-g['start']).total_seconds() <= 14*86400]
            ra14 = (sum((g['ag'] if g['home']==team else g['hg'])/env[g['season']] for g in recent14)+10)/(len(recent14)+10)
            proof['sides'][team] = dict(source_game_ids=[g['gamePk'] for g in gs], games3=len(recent3),
                long_minutes3=sum(max(0,g['duration']-180)/180 for g in recent3), defense14=ra14,
                attack=a, defense=d, venue_attack=va/vw, venue_defense=vd/vw)
            return a, d, math.log(va/vw/a), math.log(vd/vw/d), len(recent3), sum(max(0,g['duration']-180)/180 for g in recent3), ra14

        h, a = team(target['home'], True), team(target['away'], False)
        x = [[1,1,math.log(h[0]),math.log(a[1]),h[2],a[3],a[4]/3,a[5],a[4]/3*math.log(a[6])],
             [1,0,math.log(a[0]),math.log(h[1]),a[2],h[3],h[4]/3,h[5],h[4]/3*math.log(h[6])]]
        proof['latest_completion'] = proof['latest_completion'].isoformat() if proof['latest_completion'] else None
        return np.array(x), mu, proof


def fit_score(x, mu, y):
    x = x.reshape(-1, len(NAMES)); mu = np.repeat(mu, 2); y = y.reshape(-1)
    penalty = np.ones(len(NAMES))*.02; penalty[0] = 0

    def objective(beta):
        eta = np.log(mu)+x@beta
        lam = np.exp(eta)
        return float(np.mean(lam-y*eta)+.5*np.sum(penalty*beta**2)), x.T@(lam-y)/len(y)+penalty*beta

    opt = minimize(objective, np.zeros(len(NAMES)), jac=True, method='L-BFGS-B', options=dict(maxiter=2000, ftol=1e-12, gtol=1e-7))
    if not opt.success:
        raise ValueError(opt.message)
    lam = np.exp(np.log(mu)+x@opt.x)
    alpha = float(np.clip(np.sum((y-lam)**2-lam)/np.sum(lam**2), .01, 2))
    return dict(beta=opt.x.tolist(), names=NAMES, ridge=.02, alpha=alpha, training_games=len(y)//2,
                objective=float(opt.fun), gradient_max=float(np.max(np.abs(opt.jac))))


def run_probability(lam, family, alpha):
    h, a = lam[:,0], lam[:,1]
    tie_share = h/(h+a)
    if family=='pyth':
        return expit(1.83*np.log(h/a))
    if family=='poisson':
        return skellam.sf(0,h,a)+skellam.pmf(0,h,a)*tie_share
    support = np.arange(101)[:,None]
    n = 1/alpha
    hp = nbinom.pmf(support, n, n/(n+h))
    ap = nbinom.pmf(support, n, n/(n+a))
    away_less = np.vstack([np.zeros(len(h)), np.cumsum(ap,axis=0)[:-1]])
    win = np.sum(hp*away_less,axis=0)
    tie = np.sum(hp*ap,axis=0)
    omitted = np.maximum(0, 1-hp.sum(axis=0)*ap.sum(axis=0))
    return win+(tie+omitted)*tie_share


def fit_residual(score, rows):
    market_p = np.array([r['market_probability'] for r in rows])
    x = np.c_[np.ones(len(rows)), logit(np.clip(score,1e-8,1-1e-8))-logit(market_p)]
    y = np.array([int(r['hg']>r['ag']) for r in rows]); valid = np.array([r['hg']!=r['ag'] for r in rows])
    x,y,offset = x[valid],y[valid],logit(market_p[valid])

    def objective(b):
        eta = offset+x@b
        penalty = np.array([0,.02])*b
        return float(np.mean(np.logaddexp(0,eta)-y*eta)+.01*b[1]**2), x.T@(expit(eta)-y)/len(y)+penalty

    opt = minimize(objective, np.zeros(2), jac=True, method='L-BFGS-B', options=dict(ftol=1e-12, gtol=1e-8))
    if not opt.success:
        raise ValueError(opt.message)
    return dict(beta=opt.x.tolist(), ridge=.02, training_games=len(y))


def apply_link(score, base, link, residual):
    if link=='pure': return score
    if link=='market75_score25': return .75*base+.25*score
    if link=='market50_score50': return .5*base+.5*score
    b = residual['beta']
    return expit(logit(base)+b[0]+b[1]*(logit(np.clip(score,1e-8,1-1e-8))-logit(base)))


def units(rows, p):
    y = np.array([r['hg']>r['ag'] for r in rows])
    settled = np.array([r['hg']!=r['ag'] for r in rows])
    correct = (np.array(p)>=.5)==y
    odds = np.array([r['prices'][r['order'].index('home' if q>=.5 else 'away')] for r,q in zip(rows,p)])
    return np.where(settled, np.where(correct,odds-1,-1),0), correct & settled, settled


def measure(rows, p):
    if not rows: return dict(n=0)
    profit, hit, settled = units(rows,p)
    y = np.array([r['hg']>r['ag'] for r in rows],dtype=float)[settled]; q = np.clip(np.array(p)[settled],1e-10,1-1e-10)
    monthly = defaultdict(float)
    for r,net in zip(rows,profit): monthly[r['date'][:7]] += float(net)
    return dict(n=len(rows), coverage=1., wins=int(hit.sum()), pushes=int((~settled).sum()),
        accuracy=float(hit.sum()/settled.sum()), roi=float(profit.mean()), net_krw=round(float(profit.sum())*10000),
        logloss=float(np.mean(-y*np.log(q)-(1-y)*np.log1p(-q))), brier=float(np.mean((q-y)**2)),
        monthly_units=dict(monthly))


def choose(metrics):
    baseline = metrics['market_null']
    eligible = [n for n,m in metrics.items() if m['accuracy']>=baseline['accuracy'] and m['roi']>=baseline['roi']]
    return dict(primary=min(eligible,key=lambda n:(metrics[n]['logloss'],metrics[n]['brier'],n)),
        hit_leader=min(metrics,key=lambda n:(-metrics[n]['accuracy'],metrics[n]['brier'],n)),
        profit_leader=min(metrics,key=lambda n:(-metrics[n]['roi'],metrics[n]['logloss'],n)))


def bootstrap(rows, p, base):
    net,hit,settled = units(rows,p); bn,bh,_ = units(rows,base)
    y = np.array([r['hg']>r['ag'] for r in rows],dtype=float); p=np.clip(p,1e-10,1-1e-10); base=np.clip(base,1e-10,1-1e-10)
    loss = (-y*np.log(p)-(1-y)*np.log1p(-p))-(-y*np.log(base)-(1-y)*np.log1p(-base))
    brier = (p-y)**2-(base-y)**2
    groups = defaultdict(lambda:np.zeros(6))
    for i,r in enumerate(rows):
        d=date.fromisoformat(r['date']); week=(d-timedelta(days=d.weekday())).isoformat()
        groups[week] += [int(hit[i])-int(bh[i]),net[i]-bn[i],loss[i]*settled[i],brier[i]*settled[i],settled[i],1]
    a=np.array(list(groups.values())); rng=np.random.default_rng(20261029)
    sums=a[rng.integers(len(a),size=(5000,len(a)))].sum(axis=1)
    return dict(weeks=len(a),resamples=5000,seed=20261029,
        accuracy_delta95=np.quantile(sums[:,0]/sums[:,4],[.025,.975]).tolist(),
        roi_delta95=np.quantile(sums[:,1]/sums[:,5],[.025,.975]).tolist(),
        logloss_delta95=np.quantile(sums[:,2]/sums[:,4],[.025,.975]).tolist(),
        brier_delta95=np.quantile(sums[:,3]/sums[:,4],[.025,.975]).tolist())


def run(folder):
    assert (folder/'PROTOCOL.md').exists()
    history, score_rows, cohorts, audit = load(folder)
    builder=FeatureBuilder(history)
    training=cohorts['train2023']; validation=cohorts['validation2024']; development=training+validation
    # Feature building is outcome-independent: target scores never enter the feature builder.
    allrows=score_rows+training+validation+cohorts['external230']+cohorts['secondary116']
    cache={}; feature_ledger=[]
    for half in (45,180):
        for r in allrows:
            key=(r['gamePk'],r['cutoff'],half)
            if key in cache: continue
            x,mu,proof=builder.features(r,half); cache[key]=(x,mu)
            feature_ledger.append(dict(gamePk=r['gamePk'],cutoff=r['cutoff'],half_life=half,
                x=x.tolist(),mu=mu,proof=proof))
        print('features complete',half,flush=True)
    # mtime=0 makes the compressed fact artifact byte-for-byte reproducible.
    payload=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in feature_ledger).encode('utf8')
    (folder/'feature-facts.jsonl.gz').write_bytes(gzip.compress(payload,mtime=0))

    def arrays(rows,half):
        values=[cache[(r['gamePk'],r['cutoff'],half)] for r in rows]
        return np.array([v[0] for v in values]),np.array([v[1] for v in values])

    configs={}; vp={}; candidate_meta={}
    for half in (45,180):
        fitrows=[r for r in score_rows if r['season']==2023]
        x,mu=arrays(fitrows,half)
        config=fit_score(x,mu,np.array([[r['hg'],r['ag']] for r in fitrows]))
        configs[str(half)]=dict(selection_score=config)
        for family in ('poisson','negbin','pyth'):
            def predict_score(rows):
                x,m=arrays(rows,half)
                lam=np.exp(np.log(m)[:,None]+x@np.array(config['beta']))
                return run_probability(lam,family,config['alpha'])
            residual=fit_residual(predict_score(training),training)
            configs[str(half)][family]=dict(selection_residual=residual)
            score=predict_score(validation); base=np.array([r['market_probability'] for r in validation])
            for link in ('pure','market75_score25','market50_score50','residual'):
                name=f'{family}:h{half}:{link}'
                vp[name]=apply_link(score,base,link,residual)
                candidate_meta[name]=dict(family=family,half_life=half,link=link)
    vp['market_null']=np.array([r['market_probability'] for r in validation])
    vm={n:measure(validation,p) for n,p in vp.items()}; selected=choose(vm)
    # Freeze names and selection coefficients before final fitting or any evaluation metric.
    frozen=dict(protocol_sha256=digest(folder/'PROTOCOL.md'), selected=selected,
        validation=vm, selection_configs=configs, rule='eligible accuracy>=market AND roi>=market; min(logloss,brier,name)')
    write(folder/'frozen-selection.json', frozen)
    validation_ledger=[dict(r,candidate_probabilities={n:float(p[i]) for n,p in vp.items()}) for i,r in enumerate(validation)]
    write(folder/'validation_predictions.jsonl',validation_ledger,lines=True)
    print('selection frozen',json.dumps(selected),flush=True)

    ep={c:{} for c in ('external230','secondary116')}
    for half in (45,180):
        x,mu=arrays(score_rows,half)
        config=fit_score(x,mu,np.array([[r['hg'],r['ag']] for r in score_rows]))
        configs[str(half)]['final_score']=config
        for family in ('poisson','negbin','pyth'):
            def predict_score(rows):
                x,m=arrays(rows,half)
                return run_probability(np.exp(np.log(m)[:,None]+x@np.array(config['beta'])),family,config['alpha'])
            residual=fit_residual(predict_score(development),development)
            configs[str(half)][family]['final_residual']=residual
            for cohort in ep:
                rs=cohorts[cohort]; score=predict_score(rs); base=np.array([r['baseline_probability'] for r in rs])
                for link in ('pure','market75_score25','market50_score50','residual'):
                    ep[cohort][f'{family}:h{half}:{link}']=apply_link(score,base,link,residual)
    ledger=[]; tables={}; candidate_eval={}
    for cohort,probs in ep.items():
        rs=cohorts[cohort]; base=np.array([r['baseline_probability'] for r in rs]); probs['market_null']=base
        split='2026-08-10' if cohort=='external230' else '2026-07-01'
        periods={'all':np.ones(len(rs),dtype=bool),'before_split':np.array([r['date']<split for r in rs]),
                 'split_onward':np.array([r['date']>=split for r in rs])}
        candidate_eval[cohort]={n:measure(rs,p) for n,p in probs.items()}
        tables[cohort]=dict(split_date=split, baseline_kind=rs[0]['baseline_kind'],periods={})
        for period,mask in periods.items():
            subset=[r for r,k in zip(rs,mask) if k]
            out=dict(baseline=measure(subset,base[mask]),policies={})
            for role,name in selected.items():
                pred=probs[name]
                out['policies'][role]=dict(candidate=name,metrics=measure(subset,pred[mask]),
                    pick_changes=int(np.sum((pred[mask]>=.5)!=(base[mask]>=.5))),
                    paired_bootstrap=bootstrap(subset,pred[mask],base[mask]))
            out['all_candidates']={n:measure(subset,p[mask]) for n,p in probs.items()}
            tables[cohort]['periods'][period]=out
        for i,r in enumerate(rs):
            ledger.append(dict(r,candidate_probabilities={n:float(p[i]) for n,p in probs.items()},
                policies={role:dict(candidate=name,probability=float(probs[name][i])) for role,name in selected.items()}))
    write(folder/'predictions.jsonl',ledger,lines=True)
    result=dict(protocol_sha256=digest(folder/'PROTOCOL.md'),frozen_selection_sha256=digest(folder/'frozen-selection.json'),
        candidate_count=25, concrete_candidate_count=24, selected=selected,selection_rule=frozen['rule'],
        audit=audit,validation=vm,
        validation_pick_changes={n:int(np.sum((p>=.5)!=(vp['market_null']>=.5))) for n,p in vp.items()},
        evaluation=candidate_eval,periods=tables,configs=configs,candidate_metadata=candidate_meta,
        input_sha256={p.name:digest(p) for p in (folder/'sources').iterdir() if p.is_file()},
        notes=['Retrospective reused evaluation; not an untouched holdout.',
               'Archive price purchase timestamp unverified; date-level conservative cutoff.',
               'Schedule pressure is a bullpen proxy, not individual relief workload.',
               'Secondary116 comparison9:1 is research market/vote mixture, not operating voting model.'])
    write(folder/'results.json',result)
    print(json.dumps(dict(selected=selected,audit=audit,primary={c:t['periods']['all']['policies']['primary'] for c,t in tables.items()}),ensure_ascii=True),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['prepare','run']); p.add_argument('--folder',type=Path,default=DEFAULT)
    p.add_argument('--predecessors',type=Path,default=Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees'))
    a=p.parse_args()
    if a.command=='prepare': prepare(a.folder,a.predecessors)
    else: run(a.folder)
