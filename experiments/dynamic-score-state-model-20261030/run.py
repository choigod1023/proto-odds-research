"""Preregistered frozen test plus separately labelled causal prequential test."""
from collections import defaultdict, Counter
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path
import argparse

import numpy as np
from scipy.stats import binomtest
from model import Engine, CONFIGS, timestamp, due

HERE = Path(__file__).resolve().parent
LEAGUES = ['K리그1', 'K리그2', 'KBO', 'NPB', 'MLB']
FREEZE = datetime(2026, 1, 5, tzinfo=timezone.utc)


def read(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return [json.loads(line) for line in f]


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8', newline='\n')


def write_gz(path, rows):
    with path.open('wb') as f:
        with gzip.GzipFile(fileobj=f, mode='wb', mtime=0, filename='') as z:
            for r in rows:
                z.write((json.dumps(r, ensure_ascii=False, sort_keys=True)+'\n').encode())


def evaluate(p, outcome, odds=None, void=False):
    p = np.asarray(p)
    pick = int(np.argmax(p))
    won = int(pick == outcome and not void)
    return {'pick': pick, 'hit': won, 'void': void, 'p': p.tolist(),
        'profit_cents': None if odds is None else 0 if void else round(odds[pick]*100)-100 if won else -100,
        'logloss': None if void else float(-np.log(max(p[outcome], 1e-12))),
        'brier': None if void else float(sum((p[i]-int(i==outcome))**2 for i in range(len(p))))}


def metric(rows, key='candidate'):
    picks = [r for r in rows if r.get(key) is not None]
    scored = [r[key] for r in picks]
    live = [x for x in scored if not x['void']]
    cash = [x for x in scored if x['profit_cents'] is not None]
    n = len(live)
    out = {'opportunities': len(rows), 'bets': len(scored), 'coverage': len(scored)/len(rows) if rows else None,
        'nonvoid': n, 'wins': sum(x['hit'] for x in live), 'accuracy': sum(x['hit'] for x in live)/n if n else None,
        'roi': sum(x['profit_cents'] for x in cash)/(100*len(cash)) if cash else None,
        'logloss': sum(x['logloss'] for x in live)/n if n else None,
        'brier': sum(x['brier'] for x in live)/n if n else None,
        'class_counts': dict(Counter(str(x['pick']) for x in scored))}
    daily = defaultdict(int)
    months = defaultdict(int)
    for r in picks:
        if r[key]['profit_cents'] is not None:
            daily[r['day']] += r[key]['profit_cents']
            months[r['day'][:7]] += r[key]['profit_cents']
    balance = peak = drawdown = 0
    for day in sorted(daily):
        balance += daily[day]
        peak = max(peak, balance)
        drawdown = max(drawdown, peak-balance)
    out.update(max_daily_close_drawdown_units=drawdown/100, monthly_profit_units={k:v/100 for k,v in sorted(months.items())},
               positive_months=sum(v>0 for v in months.values()), negative_months=sum(v<0 for v in months.values()))
    if key in ('vote','home_constant'):
        out['logloss'] = out['brier'] = None
        out['probability_semantics'] = 'direction-only control; no calibrated probability or EV claimed'
    return out


def weekly_ci(rows, base):
    r = [x for x in rows if x.get(base) is not None and not x['candidate']['void'] and not x[base]['void']]
    if not r:
        return {'n': 0, 'weeks': 0, 'p': 1., 'valid': False, 'accuracy_delta': None, 'roi_delta': None,
                'accuracy_ci': [None,None], 'roi_ci': [None,None]}
    weeks = sorted({x['week'] for x in r})
    arr = np.array([[sum(x['week']==w for x in r),
        sum(x['candidate']['hit']-x[base]['hit'] for x in r if x['week']==w),
        sum(x['candidate']['profit_cents']-x[base]['profit_cents'] for x in r if x['week']==w)/100] for w in weeks], dtype=float)
    rng = np.random.default_rng(1030)
    draws = arr[rng.integers(0,len(arr),size=(2000,len(arr)))].sum(axis=1)
    diffs = draws[:,1:]/draws[:,0,None]
    better = sum(x['candidate']['hit']>x[base]['hit'] for x in r)
    worse = sum(x['candidate']['hit']<x[base]['hit'] for x in r)
    p = binomtest(better, better+worse, .5, alternative='greater').pvalue if better+worse else 1.
    return {'n': len(r), 'weeks': len(weeks), 'valid': len(weeks)>=8 and len(r)>=30,
        'accuracy_delta': float(arr[:,1].sum()/len(r)), 'roi_delta': float(arr[:,2].sum()/len(r)),
        'accuracy_ci': np.quantile(diffs[:,0],[.025,.975]).tolist(), 'roi_ci': np.quantile(diffs[:,1],[.025,.975]).tolist(),
        'p': float(p), 'model_only_correct': better, 'baseline_only_correct': worse,
        'warning': 'binomial assumes independent discordant fixtures; weekly CI separate; neither fixes retrospective exposure'}


def holm(ps):
    out = [1.]*len(ps)
    running = 0.
    for rank,i in enumerate(sorted(range(len(ps)),key=lambda i:ps[i])):
        running = max(running, min(1., (len(ps)-rank)*ps[i]))
        out[i] = running
    return out


def history_target(row):
    soccer = row['league'].startswith('K리그')
    void = not soccer and row['hg']==row['ag']
    outcome = 0 if row['hg']>row['ag'] else 1 if soccer and row['hg']==row['ag'] else 2 if soccer else 1
    at = datetime.fromisoformat(row['date']).replace(tzinfo=timezone.utc)
    return {**row, 'outcome': outcome, 'void': void, 'at': at, 'sides': ['home','draw','away'] if soccer else ['home','away']}


def selection_eligible(training_n, validation):
    return training_n>=100 and sum(not r['void'] for r in validation)>=50


def independent(rows):
    # Deliberately do not call evaluate()/metric() for settlement and summary.
    checked = 0
    for key in ('candidate','market','fixed9','vote','home_constant'):
        chosen = [r for r in rows if r.get(key) is not None]
        wins = count = 0
        cash = Decimal(0)
        for r in chosen:
            x = r[key]
            winner = x['pick']==r['outcome']
            if not x['void']:
                count += 1
                wins += int(winner)
                assert abs(-math.log(max(x['p'][r['outcome']],1e-12))-x['logloss']) < 1e-12
                independent_brier = sum((float(v)-(1 if i==r['outcome'] else 0))**2 for i,v in enumerate(x['p']))
                assert abs(independent_brier-x['brier']) < 1e-12
            payout = Decimal(1) if x['void'] else Decimal(str(r['odds'][x['pick']])) if winner else Decimal(0)
            net = int((payout-1)*100)
            assert net == x['profit_cents'] and int(winner and not x['void']) == x['hit']
            cash += payout-1
            checked += 1
        m = metric(rows, key)
        assert m['nonvoid']==count and m['wins']==wins
        if chosen:
            assert abs(float(cash/len(chosen))-m['roi'])<1e-12
    return checked


def run(output):
    output.mkdir(parents=True,exist_ok=True)
    history = read(HERE/'inputs/history.jsonl.gz')
    targets = read(HERE/'inputs/targets.jsonl.gz')
    selected, models, validation, history_results, contrasts, eligibility = {}, {}, {}, {}, [], {}
    allrows = []
    for league in LEAGUES:
        h = [r for r in history if r['league']==league]
        train = [r for r in h if r['date'][:4]<'2025']
        val = [history_target(r) for r in h if r['date'][:4]=='2025']
        test = [history_target(r) for r in h if r['date'][:4]=='2026']
        eligibility[league] = {'training_n':len(train),'validation_n':len(val),
            'validation_nonvoid':sum(not r['void'] for r in val),
            'eligible_to_select':selection_eligible(len(train),val)}
        validation[league] = {}
        for name, kind, q in CONFIGS:
            engine = Engine([r for r in h if r['date'][:4]<'2026'],league,kind,q)
            vs = []
            for t in sorted(val,key=lambda r:(r['at'],r['home'],r['away'])):
                p,_ = engine.predict(t['home'],t['away'],t['at'])
                vs.append({'day':t['date'],'candidate':evaluate(p,t['outcome'],void=t['void'])})
            validation[league][name] = metric(vs)
            engine.advance(FREEZE)
            models[league,name] = engine.state.export()
            for mode in ('frozen','prequential'):
                run_engine = Engine(h,league,kind,q)
                run_engine.state = engine.state.clone()
                run_engine.cursor = sum(due(r)<=FREEZE for r in run_engine.history)
                hs = []
                for t in sorted(test,key=lambda r:(r['at'],r['home'],r['away'])):
                    p,_ = run_engine.predict(t['home'],t['away'],t['at'],online=mode=='prequential')
                    hs.append({'day':t['date'],'candidate':evaluate(p,t['outcome'],void=t['void'])})
                history_results[league,name,mode] = metric(hs)
                run_engine = Engine(h,league,kind,q)
                run_engine.state = engine.state.clone()
                run_engine.cursor = sum(due(r)<=FREEZE for r in run_engine.history)
                ts = [r for r in targets if r['league']==league and r['kickoff'][:4]=='2026']
                for t in sorted(ts,key=lambda r:(timestamp(r['decision_at']),r['event_id'],r['cohort'])):
                    at = timestamp(t['decision_at'])
                    p,audit = run_engine.predict(t['home'],t['away'],at,online=mode=='prequential')
                    week = at.date().isocalendar()
                    row = {k:t[k] for k in ('event_id','league','cohort','home','away','kickoff','decision_at','sides','odds','outcome','actual_votes')}
                    row.update(model=name,mode=mode,day=at.date().isoformat(),week=f'{week.year}-W{week.week:02}',audit=audit,
                        candidate=evaluate(p,t['outcome'],t['odds']),market=evaluate(t['market_p'],t['outcome'],t['odds']),
                        home_constant=evaluate([1.]+[0.]*(len(p)-1),t['outcome'],t['odds']),fixed9=None,vote=None)
                    if t['actual_votes']:
                        fixed = [.9*a+.1*b for a,b in zip(t['market_p'],t['vote_p'])]
                        v = evaluate(fixed,t['outcome'],t['odds'])
                        # Preserve original four-decimal ranking/tie behavior.
                        original = t['sides'].index(t['fixed_pick'])
                        vv = evaluate([round(z,4) for z in fixed],t['outcome'],t['odds'])
                        assert vv['pick']==original, 'fixed9 baseline changed'
                        v['pick'],v['hit'],v['profit_cents'] = vv['pick'],vv['hit'],vv['profit_cents']
                        row['fixed9'] = v
                        if not t.get('votes_tie') and t.get('vote_pick') in t['sides']:
                            vec = [int(s==t['vote_pick']) for s in t['sides']]
                            row['vote'] = evaluate(vec,t['outcome'],t['odds'])
                    allrows.append(row)
        selected[league] = min((c[0] for c in CONFIGS),key=lambda n:(validation[league][n]['logloss'],[c[0] for c in CONFIGS].index(n))) if eligibility[league]['eligible_to_select'] else None
        print('completed',league,flush=True)
    summaries = []
    for league in LEAGUES:
        for name,kind,q in CONFIGS:
            for mode in ('frozen','prequential'):
                rows = [r for r in allrows if r['league']==league and r['model']==name and r['mode']==mode and r['actual_votes']]
                summaries.append({'league':league,'model':name,'mode':mode,'selected':selected[league]==name,
                    'metrics':{k:metric(rows,k) for k in ('candidate','market','fixed9','vote','home_constant')},
                    'disagreements_market':sum(r['candidate']['pick']!=r['market']['pick'] for r in rows),
                    'home_constant_agreement':all(r['candidate']['pick']==0 for r in rows) if rows else None})
                for base in ('market','fixed9'):
                    contrasts.append({'league':league,'model':name,'mode':mode,'baseline':base,**weekly_ci(rows,base)})
    assert len(contrasts)==80
    corrected = holm([r['p'] for r in contrasts])
    for r,p in zip(contrasts,corrected):
        r['p_holm80'] = p
        r['improved'] = bool(r['valid'] and p<.05 and r['accuracy_ci'][0]>0 and r['accuracy_delta']>0)
    verify_count = independent(allrows)
    result = {'scope':'all prior data retrospective, NOT blind; no pregame receipt certification', 'selected':selected,'eligibility':eligibility,
        'probability_semantics':{'candidate':'uncalibrated research posterior predictive approximation',
            'market':'saved market-implied reference, not certified calibrated/executable',
            'fixed9':'original actual-vote blend with original ranking; uncalibrated',
            'vote':'direction control only, probability losses withheld'},
        'validation2025':validation,'training_counts':dict(Counter(r['league'] for r in history if r['date'][:4]<'2025')),
        'history2026':[{'league':l,'model':n,'mode':m,**v} for (l,n,m),v in history_results.items()],
        'actual_vote_2026':summaries,'contrasts':contrasts,'independent_settlements':verify_count,
        'market_proxy_2026':[{'league':l,'model':selected[l],'mode':m,'candidate':metric([r for r in allrows if r['league']==l and r['model']==selected[l] and r['mode']==m and not r['actual_votes']]),'market':metric([r for r in allrows if r['league']==l and r['model']==selected[l] and r['mode']==m and not r['actual_votes']],'market')} for l in LEAGUES for m in ('frozen','prequential')],
        'certified_pregame_rows':0,'family_size':80,'new_parameters_after_test':False}
    save(output/'results.json',result)
    save(output/'trained-states.json',{l:{n:models[l,n] for n,_,_ in CONFIGS} for l in LEAGUES})
    write_gz(output/'predictions.jsonl.gz',allrows)
    files = [*HERE.glob('*.py'), * (HERE/'inputs').glob('*'), *output.glob('*')]
    save(output/'hashes.json',{str(p.resolve().relative_to(HERE.parents[1]).as_posix()):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.name!='hashes.json'})
    print('settlements',verify_count,'improved',sum(r['improved'] for r in contrasts))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,default=HERE/'results')
    run(p.parse_args().output)
