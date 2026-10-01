"""Nonclosing bookmaker information experiment; target B365 excluded from sources."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
import math
import os
import platform
from pathlib import Path
from datetime import datetime
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ[name]='1'
import numpy as np
from src.devig import shin
from scripts.local_score_model_tournament import (POLICIES, choose_tickets, settle, betting_metrics,
                                                  betting_ci, loss_ci, match_metrics)
REV = 'e82cf59161e82e0fbcdc49ad02819e45c821a5c8'
LEAGUES = {'D1':'bundesliga1', 'SP1':'laliga', 'E0':'premier_league', 'I1':'seriea', 'F1':'ligue1'}
BOOKS = ('BW', 'IW', 'PS', 'WH', 'VC', 'BF', '1XB')
MODELS = ('b365','consensus_mean','consensus_median','ps')
PROTOCOL = 'docs/research/2026-10-01-market-information-protocol.md'
PROTOCOL_COMMIT = '8427619c'
PROTOCOL_SHA256 = '78c92694b5b625e55f809ee2b31aeee9cd8bbda02dd886ff703b91669e07a23f'


def text_sha256(path):
    return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def source_specs():
    return [(league, season) for league in LEAGUES for season in (range(19,25) if league in ('D1','SP1') else (23,24))]


def prices(record, book):
    try:
        values = [float(record[book+side]) for side in 'HDA']
        return values if all(math.isfinite(v) and v > 1 for v in values) else None
    except (KeyError, ValueError, TypeError):
        return None


def fetch_sources():
    def one(spec):
        league, season = spec
        url=f'https://raw.githubusercontent.com/sosthene14/footballdataset/{REV}/datasets/{LEAGUES[league]}/{season:02}{season+1:02}_{league}.csv'
        raw=urllib.request.urlopen(url,timeout=30).read()
        return league,season,raw,dict(url=url,sha256=hashlib.sha256(raw).hexdigest())
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(one,source_specs()))


def price_audit(raw):
    """Does not read FTR/goals or compute outcome statistics."""
    reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    columns=[b+s for b in ('B365',)+BOOKS for s in 'HDA' if b+s in reader.fieldnames]
    n=0; coverage={b:0 for b in ('B365',)+BOOKS}; common=0
    for r in reader:
        if not r.get('HomeTeam'):
            continue
        n+=1
        complete={b:prices(r,b) is not None for b in coverage}
        for b,available in complete.items(): coverage[b]+=available
        common+=complete['B365'] and complete['PS'] and sum(complete[b] for b in BOOKS)>=3
    return dict(rows=n,columns=columns,complete_prices=coverage,common=common,common_coverage=common/n if n else 0.)


def parse_source(raw,league,season):
    """Outcome parsing is only used after the protocol guard, never by --audit."""
    rows=[]
    for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))):
        if not r.get('HomeTeam'): continue
        if r['Div'] != league: raise ValueError('League mismatch')
        day=datetime.strptime(r['Date'],'%d/%m/%Y' if len(r['Date'].split('/')[-1])==4 else '%d/%m/%y').date()
        if not datetime(2000+season,7,1).date() <= day < datetime(2001+season,7,1).date():
            if not (season==19 and day.year==2020 and day<datetime(2020,9,1).date()):
                raise ValueError('Season mismatch')
        hg,ag=int(r['FTHG']),int(r['FTAG'])
        y=0 if hg>ag else 1 if hg==ag else 2
        if min(hg,ag)<0 or r['FTR']!='HDA'[y]: raise ValueError('Invalid outcome')
        rows.append(dict(match_id=f"{league}:{day}:{r['HomeTeam']}:{r['AwayTeam']}",
                         league=league,season=season,date=day.isoformat(),y=y,prices=prices(r,'B365'),
                         books={book:value for book in BOOKS if (value:=prices(r,book)) is not None}))
    expected=306 if league in ('D1','F1') else 380
    if len(rows)!=expected or len({r['match_id'] for r in rows})!=len(rows):
        raise ValueError('Coverage/duplicate failure')
    return rows


def available(row):
    return row['prices'] is not None and 'PS' in row['books'] and sum(b in row['books'] for b in BOOKS)>=3


def enforce_coverage(rows):
    if not rows or sum(available(r) for r in rows)/len(rows)<.95:
        raise ValueError('Common market coverage below frozen 95%')


def predict(rows):
    if not rows or not all(available(r) for r in rows): raise ValueError('Unavailable common market')
    p={name:[] for name in MODELS}
    for row in rows:
        external=np.array([shin(row['books'][b]) for b in BOOKS if b in row['books']])
        med=np.median(external,axis=0); med/=med.sum()
        p['b365'].append(shin(row['prices']))
        p['consensus_mean'].append(external.mean(0))
        p['consensus_median'].append(med)
        p['ps'].append(shin(row['books']['PS']))
    return {k:np.array(v) for k,v in p.items()}


def probability_metrics(rows,p):
    metrics=match_metrics(rows,p)
    choices=p.argmax(1)
    wins=np.array([r['y']==i for r,i in zip(rows,choices)])
    odds=np.array([r['prices'][i] for r,i in zip(rows,choices)])
    metrics['single_pick']=dict(picks=len(rows),wins=int(wins.sum()),profit=float((wins*odds-1).sum()),
                                roi=float((wins*odds-1).mean()),low_odds_picks=int((odds<1.5).sum()))
    return metrics


def ledger_summary(ledger):
    return {**betting_metrics(ledger), 'cash':sum(not r['stake'] for r in ledger),
            'selection_sha256':hashlib.sha256(json.dumps([(r['league'],r['date'],[(a['match_id'],a['outcome']) for a in r['legs']]) for r in ledger],separators=(',',':')).encode()).hexdigest()}


def evaluate(all_rows,rows,p):
    pregame=[{k:r[k] for k in ('match_id','league','date','prices')} for r in rows]
    metrics={name:probability_metrics(rows,probs) for name,probs in p.items()}
    ledgers={f'{name}/{policy}':settle(all_rows,choose_tickets(pregame,probs,policy))
             for name,probs in p.items() for policy in POLICIES}
    summaries={name:ledger_summary(ledger) for name,ledger in ledgers.items()}
    for name,s in summaries.items():
        baseline=summaries['b365/'+name.split('/')[1]]
        s['budget_delta_same_policy_b365']=s['budget_return']-baseline['budget_return']
    return metrics,summaries,ledgers


def choose_inner(stage,metrics,trials):
    if stage!='inner': raise ValueError('Only inner selection allowed')
    eligible=[name for name,s in trials.items() if s['tickets']>=20]
    chosen=min(eligible,key=lambda name:(-trials[name]['budget_return'],metrics[name.split('/')[0]]['logloss'],name)) if eligible else 'b365/highestprob'
    return dict(policy=chosen,probability=min(metrics,key=lambda name:(metrics[name]['logloss'],name)),eligible=len(eligible))


def check_dates(inner,outer):
    if not inner or not outer or max(r['date'] for r in inner)>=min(r['date'] for r in outer):
        raise ValueError('Inner/outer date batch overlap')


def describe(rows):
    return dict(raw=len(rows),common=sum(available(r) for r in rows),first=min(r['date'] for r in rows),last=max(r['date'] for r in rows))


def run():
    start=time.perf_counter()
    if text_sha256(ROOT/PROTOCOL) != PROTOCOL_SHA256:
        raise ValueError('Frozen protocol changed')
    all_rows=[]; sources=[]
    for league,season,raw,meta in fetch_sources():
        audit=price_audit(raw)
        if audit['common_coverage']<.95: raise ValueError('Price audit coverage changed')
        rows=parse_source(raw,league,season); enforce_coverage(rows)
        all_rows.extend(rows); sources.append(dict(league=league,season=season,**meta,**audit))
    if len({r['match_id'] for r in all_rows})!=len(all_rows): raise ValueError('Cross source duplicates')
    all_rows.sort(key=lambda r:(r['date'],r['match_id']))
    folds=[]; pooled={group:dict(rows=[],p={m:[] for m in MODELS},selected_probability=[],ledgers={k:[] for k in ('baseline','fixed_mean','selected','selected_same_policy_b365','full_b365')}) for group in ('core','external')}
    for season in (23,24):
        inner_all=[r for r in all_rows if r['league'] in ('D1','SP1') and r['season']==season-1]
        inner=[r for r in inner_all if available(r)]
        im,its,_=evaluate(inner_all,inner,predict(inner))
        winner=choose_inner('inner',im,its)
        print(f'fold {season}: inner locked {winner}',flush=True)
        fold=dict(season=season,inner=describe(inner_all),inner_models=im,inner_trials=its,winner=winner,outer={})
        for group,leagues in [('core',('D1','SP1')),('external',('E0','I1','F1'))]:
            outer_all=[r for r in all_rows if r['league'] in leagues and r['season']==season]
            check_dates(inner_all,outer_all)
            outer=[r for r in outer_all if available(r)]; p=predict(outer)
            om,ots,ledgers=evaluate(outer_all,outer,p)
            records={name:ledgers[key] for name,key in [('baseline','b365/highestprob'),('fixed_mean','consensus_mean/highestprob'),('selected',winner['policy']),('selected_same_policy_b365','b365/'+winner['policy'].split('/')[1])]}
            full=[r for r in outer_all if r['prices'] is not None]
            records['full_b365']=settle(outer_all,choose_tickets(full,np.array([shin(r['prices']) for r in full]),'highestprob'))
            actual={r['match_id']:r for r in outer_all}
            for rs in records.values():
                for record in rs:
                    for leg in record['legs']:
                        leg['y']=actual[leg['match_id']]['y']
            forecasts=[dict(match_id=r['match_id'],league=r['league'],date=r['date'],y=r['y'],
                            prices=r['prices'],probabilities={m:p[m][i].tolist() for m in MODELS})
                       for i,r in enumerate(outer)]
            fold['outer'][group]=dict(coverage=describe(outer_all),models=om,trials=ots,
                                      forecasts=forecasts,
                                      settlement_rows=[{k:r[k] for k in ('match_id','league','date','y','prices')} for r in outer_all],
                                      policy={name:ledger_summary(rs) for name,rs in records.items()},records=records,
                                      primary_mean_logloss_ci=loss_ci(outer,p['consensus_mean'],p['b365']),
                                      selected_probability_ci=loss_ci(outer,p[winner['probability']],p['b365']),
                                      betting_ci={name:betting_ci(records[name],records['baseline']) for name in ('fixed_mean','selected')},
                                      selected_same_policy_ci=betting_ci(records['selected'],records['selected_same_policy_b365']),
                                      leagues={lg:dict(matches=sum(r['league']==lg for r in outer),models={m:probability_metrics([r for r in outer if r['league']==lg],probs[[r['league']==lg for r in outer]]) for m,probs in p.items()},policy={n:ledger_summary([r for r in rs if r['league']==lg]) for n,rs in records.items()}) for lg in leagues})
            store=pooled[group]; store['rows'].extend(outer)
            for m in MODELS: store['p'][m].extend(p[m])
            store['selected_probability'].extend(p[winner['probability']])
            for name,rs in records.items(): store['ledgers'][name].extend(rs)
        folds.append(fold)
    output={}
    for group,s in pooled.items():
        rows=s['rows']; p={m:np.array(v) for m,v in s['p'].items()}; ledgers=s['ledgers']; chosen=np.array(s['selected_probability'])
        output[group]=dict(models={m:probability_metrics(rows,v) for m,v in p.items()},selected_probability=probability_metrics(rows,chosen),
                           probability_ci={m:loss_ci(rows,v,p['b365']) for m,v in p.items()},selected_probability_ci=loss_ci(rows,chosen,p['b365']),
                           policy={name:ledger_summary(rs) for name,rs in ledgers.items()},
                           betting_ci={name:betting_ci(ledgers[name],ledgers['baseline']) for name in ('baseline','fixed_mean','selected')},
                           selected_same_policy_ci=betting_ci(ledgers['selected'],ledgers['selected_same_policy_b365']))
    return dict(protocol_commit=PROTOCOL_COMMIT,source_revision=REV,sources=sources,models=MODELS,policies=POLICIES,unique_trials=20,
                total_trial_evaluations=120,folds=folds,pooled=output,elapsed_seconds=time.perf_counter()-start,
                code_sha256=text_sha256(__file__),protocol_sha256=text_sha256(ROOT/PROTOCOL),text_hash_normalization='CRLF to LF; UTF-8 bytes; POSIX relative dependency keys',
                dependency_sha256={path:text_sha256(ROOT/path) for path in ('scripts/local_score_model_tournament.py','src/devig.py')},
                environment=dict(python=platform.python_version(),numpy=np.__version__,thread_environment={name:os.environ.get(name) for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS')},thread_limit_note='Environment requests one thread before NumPy import; effective native pool size not measured.'),seed=20261001,bootstrap=5000,
                limitations=['Previously exposed data; external leagues excluded from current inner selection, not fresh confirmation.',
                             'Nonclosing quotes lack synchronized observation times; not executable or Proto returns.',
                             'Source composition changes by season; no B365/closing/exchange/max/average sources in consensus.',
                             'Two-leg probability independence assumed; weekly bootstrap and distinct games do not establish it.'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--audit',action='store_true'); parser.add_argument('--output',type=Path); args=parser.parse_args()
    if args.audit:
        for league,season,raw,meta in fetch_sources(): print(json.dumps(dict(league=league,season=season,**meta,**price_audit(raw))))
    else:
        if args.output is None: parser.error('--output required')
        result=run(); args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        print(json.dumps(dict(elapsed=result['elapsed_seconds'],pooled={g:dict(models=v['models'],policy=v['policy'],mean_ci=v['probability_ci']['consensus_mean']) for g,v in result['pooled'].items()})))
