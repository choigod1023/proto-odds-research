"""One frozen 2025 diagnostic. No betting, deployment or source network calls."""
from collections import defaultdict
from collections.abc import Mapping
import csv
from datetime import date, timedelta
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import re
import numpy as np

ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
REGISTRATION_COMMIT='881e9ff9b57ffec4b6740849ffba5e28d6f23a4d'
sys.path.insert(0,str(ROOT))
from src.devig import shin
from src.research_kleague_dc import fit,predict
from src.research_kleague_dates import parse_raw


def save(path,obj):
    Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8',newline='\n')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def official(row):
    match=re.fullmatch(r'kleague:(2023|2024|2025):1:(\d+)',str(row.get('fixture_id','')))
    scores=row.get('scores')
    if (match is None or row.get('league')!='KLEAGUE1' or row.get('source_status')!='FE'
        or row.get('source_end_yn')!='Y' or not isinstance(scores,Mapping)
        or any(type(scores.get(k)) is not int or scores[k]<0 for k in ('home','away'))
        or not row.get('home_team_id') or not row.get('away_team_id')
        or row['home_team_id']==row['away_team_id']):
        raise ValueError('invalid or nonfinal official fixture')
    day=date.fromisoformat(row['day'])
    if day.isoformat()!=row['day'] or str(day.year)!=match[1]:
        raise ValueError('official fixture year/date mismatch')
    return dict(id=row['fixture_id'],day=row['day'],home_id=row['home_team_id'],away_id=row['away_team_id'],
                hg=row['scores']['home'],ag=row['scores']['away'])


def require_coverage(normalized, links, prior, evaluation_official):
    if (len(normalized)!=227 or len(links)!=227 or len(set(links.values()))!=227
        or set(normalized)!=set(links.values()) or len(prior)!=456 or len(evaluation_official)!=228
        or len({r['fixture_id'] for r in evaluation_official})!=228
        or not set(links)<={r['fixture_id'] for r in evaluation_official}):
        raise ValueError('incomplete or ambiguous official/evaluation coverage')


def load_inputs(raw,training):
    raw,training=Path(raw).resolve(),Path(training).resolve()
    old=ROOT/'experiments/verified-cohort-20261101/kleague1'
    normalized=ROOT/'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz'
    source_hashes={str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else p.name:digest(p)
                   for p in [raw,normalized,old/'fixtures.jsonl',old/'comparison.json',training]}
    if digest(raw)!='9ffddb2dcbc4297c32e6ad3826f392e1842e52e10b509e4cbe47eed1f84b9da4':
        raise ValueError('raw source changed')
    if digest(normalized)!='4bf3abf8bb82838ee72975e1ef486a471b357a03ee4d8627808780f046dae51a':
        raise ValueError('normalized source changed')
    comparisons=json.loads((old/'comparison.json').read_text(encoding='utf8'))['details']
    links={r['fixture_id']:r['domestic_id'] for r in comparisons if r['link']=='unique'}
    with gzip.open(normalized,'rt',encoding='utf8') as stream:
        nd={r['id']:r for r in map(json.loads,stream) if r['league']=='K리그1' and r['day'].startswith('2025-')}
    if len(nd)!=227 or len(links)!=227 or len(set(links.values()))!=227 or set(nd)!=set(links.values()):
        raise ValueError('changed evaluation cohort')
    raw_index=defaultdict(list)
    with raw.open(encoding='utf-8-sig',newline='') as stream:
        for r in csv.DictReader(stream):
            if r['year']=='2025' and (p:=parse_raw(r)) is not None:
                raw_index[(p['day'],p['home'],p['away'])].append(r)
    history=[json.loads(line) for line in training.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    earlier=[official(r) for r in history]
    if not earlier or any(r['day'][:4] not in ('2023','2024') for r in earlier):
        raise ValueError('training source year mismatch')
    all2025=[json.loads(line) for line in (old/'fixtures.jsonl').read_text(encoding='utf8').splitlines()]
    require_coverage(nd,links,earlier,all2025)
    complete=earlier+[official(r) for r in all2025]
    if len({r['id'] for r in complete})!=len(complete):
        raise ValueError('duplicate official training/evaluation identities')
    evaluation=[]
    for f in all2025:
        if f['fixture_id'] not in links:continue
        n=nd[links[f['fixture_id']]];candidates=raw_index[(n['day'],n['home'],n['away'])]
        if len(candidates)!=1:raise ValueError('raw fixture link not unique')
        r=candidates[0];prices=[Decimal(x) for x in r['odds'].split(',')]
        if len(prices)!=3 or prices!=[Decimal(str(x)) for x in n['odds']]:raise ValueError('raw/normalized price mismatch')
        y=0 if f['scores']['home']>f['scores']['away'] else 1 if f['scores']['home']==f['scores']['away'] else 2
        if y!=n['y'] or f['day']!=n['day'] or r['is_void']!='False':raise ValueError('label/date/void mismatch')
        evaluation.append({**official(f),'home':n['home'],'away':n['away'],'prices':[str(x) for x in prices],'y':y})
    if len(evaluation)!=227 or {r['id'] for r in evaluation}!=set(links):
        raise ValueError('incomplete or duplicated final evaluation cohort')
    return complete,sorted(evaluation,key=lambda r:(r['day'],r['id'])),source_hashes


def forecasts(history,evaluation):
    result=[];states=[]
    for month in sorted({r['day'][:7] for r in evaluation}):
        reference=month+'-01';cutoff=(date.fromisoformat(reference)-timedelta(days=7)).isoformat()
        training=[r for r in history if r['day']<=cutoff]
        state=fit(training,reference);states.append(state)
        for r in (r for r in evaluation if r['day'].startswith(month)):
            market=shin([float(x) for x in r['prices']]);candidate,extra=predict(state,r['home_id'],r['away_id'])
            item={**r,'fit_month':month,'training_cutoff':cutoff,'model_detail':extra,'forecasts':{}}
            for name,p in [('market',market),('time_weighted_dc',candidate)]:
                p=np.asarray(p)
                if not np.isfinite(p).all() or min(p)<=0 or abs(p.sum()-1)>1e-9:raise ValueError('invalid probabilities')
                leaders=np.flatnonzero(np.abs(p-max(p))<=1e-12);pick=int(leaders[0]) if len(leaders)==1 else None
                net=Decimal(0) if pick is None else Decimal(r['prices'][pick])-1 if pick==r['y'] else Decimal(-1)
                item['forecasts'][name]=dict(p=p.tolist(),pick=pick,hit=pick==r['y'],net=str(net))
            result.append(item)
    return sorted(result,key=lambda r:(r['day'],r['id'])),states


def metrics(rows,model):
    daily=defaultdict(Decimal);monthly=defaultdict(Decimal);nets=[];hits=bets=0
    for r in rows:
        p=r['forecasts'][model];net=Decimal(p['net']);nets.append(net);daily[r['day']]+=net;monthly[r['day'][:7]]+=net
        bets+=p['pick'] is not None;hits+=p['hit']
    balance=peak=dd=Decimal(0)
    for d in sorted(daily):balance+=daily[d];peak=max(peak,balance);dd=max(dd,peak-balance)
    probabilities=np.array([r['forecasts'][model]['p'] for r in rows]);labels=np.array([r['y'] for r in rows])
    return dict(games=len(rows),bets=bets,hits=hits,accuracy=hits/bets if bets else None,coverage=bets/len(rows),
                net_units=str(sum(nets,Decimal(0))),roi=float(sum(nets,Decimal(0))/bets) if bets else None,
                logloss=float(-np.log(probabilities[np.arange(len(rows)),labels]).mean()),
                brier=float(((probabilities-np.eye(3)[labels])**2).sum(1).mean()),daily_close_drawdown_units=str(dd),
                monthly_net={m:str(n) for m,n in sorted(monthly.items())})


def bootstrap(rows):
    blocks=defaultdict(lambda:np.zeros(4))
    for r in rows:
        a=r['forecasts']['market'];b=r['forecasts']['time_weighted_dc'];week=date.fromisoformat(r['day']).isocalendar()[:2]
        blocks[week]+=np.array([1,float(Decimal(b['net'])-Decimal(a['net'])),int(b['hit'])-int(a['hit']),
                                np.log(b['p'][r['y']])-np.log(a['p'][r['y']])])
    matrix=np.array(list(blocks.values()));rng=np.random.default_rng(20261101)
    sample=matrix[rng.integers(0,len(matrix),size=(10000,len(matrix)))].sum(1)
    return dict(weeks=len(matrix),roi_delta_per_game_ci95=np.quantile(sample[:,1]/sample[:,0],[.025,.975]).tolist(),
                accuracy_delta_per_game_ci95=np.quantile(sample[:,2]/sample[:,0],[.025,.975]).tolist(),
                logloss_improvement_ci95=np.quantile(sample[:,3]/sample[:,0],[.025,.975]).tolist(),
                note='Paired observed fixture-week blocks; exploratory, not formal promotion test. ROI delta per opportunity differs from per-bet ROI if coverage differs.')


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--raw',type=Path,required=True);parser.add_argument('--training',type=Path,required=True)
    args=parser.parse_args();history,evaluation,hashes=load_inputs(args.raw,args.training)
    frozen=HERE/'input-hashes.json'
    if not frozen.exists() or json.loads(frozen.read_text(encoding='utf8'))!=hashes:
        raise ValueError('freeze input hashes in Git before evaluation')
    for path in (frozen,HERE/'protocol.json'):
        committed=subprocess.check_output(['git','show',REGISTRATION_COMMIT+':'+path.relative_to(ROOT).as_posix()],cwd=ROOT)
        if json.loads(committed)!=json.loads(path.read_text(encoding='utf8')):
            raise ValueError('uncommitted protocol or input-hash change')
    model=ROOT/'src/research_kleague_dc.py'
    for code in (model,ROOT/'src/devig.py'):
        committed=subprocess.check_output(['git','show',REGISTRATION_COMMIT+':'+code.relative_to(ROOT).as_posix()],cwd=ROOT)
        if committed.replace(b'\r\n',b'\n')!=code.read_bytes().replace(b'\r\n',b'\n'):
            raise ValueError('model/baseline differs from pre-evaluation registration')
    ledger,states=forecasts(history,evaluation)
    with (HERE/'ledger.jsonl').open('w',encoding='utf8',newline='\n') as out:
        for r in ledger:out.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n')
    save(HERE/'states.json',states)
    report=dict(status='preliminary_retrospective_diagnostic_not_validation',evaluation_games=len(ledger),
        range=[ledger[0]['day'],ledger[-1]['day']],models={m:metrics(ledger,m) for m in ('market','time_weighted_dc')},
        changed_picks=sum(r['forecasts']['market']['pick']!=r['forecasts']['time_weighted_dc']['pick'] for r in ledger),
        uncertainty=bootstrap(ledger),input_hashes=hashes,protocol_sha256=digest(HERE/'protocol.json'),
        unavailable=['actual tipster operating replay','9:1 historical hybrid','KBO player model'],
        price_receipt_verified=False,pregame_player_inputs_used=False,formal_minimum_300_met=False,adopted=False,
        registration_commit=subprocess.check_output(['git','rev-parse',REGISTRATION_COMMIT],cwd=ROOT,text=True).strip(),
        model_lf_sha256=hashlib.sha256(model.read_bytes().replace(b'\r\n',b'\n')).hexdigest())
    save(HERE/'results.json',report);print(json.dumps({k:report[k] for k in ('evaluation_games','models','changed_picks','uncertainty')},ensure_ascii=False))


if __name__=='__main__':main()
