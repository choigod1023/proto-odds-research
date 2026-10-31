"""Frozen-rule loss validation. Saved-price retrospective arithmetic, not trading."""
from collections import defaultdict,Counter
from datetime import datetime,timezone
from decimal import Decimal
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
SOURCE=ROOT/'experiments/dynamic-score-state-model-20261030'
EXPECTED_PRED='41fca09ed45b3be5a5c06bb369143233b85198e0d3d43aacd67225b9d8fc12bd'
EXPECTED_TARGET='71af2473a3969c80af386af8d2ebe13d7585f200ea243a2ad8dac2469e6778d0'
LEAGUES=['K리그1','K리그2','KBO','NPB','MLB']
POLICIES=['market_all','score_all','score_favorites','score_underdogs','market_same_underdog_events']


def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def at(x):
    d=datetime.fromisoformat(x.replace('Z','+00:00'))
    if d.utcoffset() is None:raise ValueError('timezone required')
    return d.astimezone(timezone.utc)
def read(path):
    with gzip.open(path,'rt',encoding='utf8') as f:return [json.loads(x) for x in f]
def save(path,x):Path(path).write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8',newline='\n')
def write_gz(path,rows):
    with Path(path).open('wb') as f:
        with gzip.GzipFile(fileobj=f,mode='wb',filename='',mtime=0) as z:
            for r in rows:z.write((canonical(r)+'\n').encode())


def classify(pick,market):
    maximum=max(market);tops=sum(abs(p-maximum)<=1e-12 for p in market)
    return 'underdog' if market[pick]<maximum-1e-12 else 'favorite' if tops==1 else 'top_tie'


def payout(pick,outcome,odds,void=False):
    if not 0<=pick<len(odds) or not all(type(x) in (int,float) and np.isfinite(x) and x>1 for x in odds):raise ValueError('bad price or pick')
    return Decimal(0) if void else Decimal(str(odds[pick]))-1 if pick==outcome else Decimal(-1)


def pack(target,prediction,mode,void=False):
    n=len(target['sides']);p=np.asarray(prediction,dtype=float);m=np.asarray(target['market_p'],dtype=float)
    if p.shape!=(n,) or m.shape!=(n,) or not np.isfinite(p).all() or not np.isfinite(m).all() or (p<0).any() or (m<0).any() or abs(p.sum()-1)>1e-9 or abs(m.sum()-1)>1e-9:
        raise ValueError('invalid vector')
    y=target['outcome']
    if type(y) is not int or not 0<=y<n:raise ValueError('unsupported or missing outcome')
    selected=int(np.argmax(p));market=int(np.argmax(m));t=at(target['decision_at'])
    return {'id':target['league']+'|'+target['event_id']+'|'+target['cohort'],
            'event_id':target['event_id'],'cohort':'actual_saved_quote' if target['actual_votes'] else 'archive_price_proxy',
            'source_cohort':target['cohort'],'price_evidence':target['price_evidence'],
            'league':target['league'],'mode':mode,'year':t.year,'day':t.date().isoformat(),
            'month':t.strftime('%Y-%m'),'week':t.strftime('%G-W%V'),
            'period':'earlier' if t.year==2026 and t<at('2026-07-01T00:00:00Z') else 'discovery' if t.year==2026 else 'older',
            'decision_at':target['decision_at'],'kickoff_at':target['kickoff'],
            'probabilities':p.tolist(),'market_probabilities':m.tolist(),'odds':target['odds'],
            'outcome':y,'void':void,'score_pick':selected,'market_pick':market,
            'group':classify(selected,m),'score_net':str(payout(selected,y,target['odds'],void)),
            'market_net':str(payout(market,y,target['odds'],void)),
            'score_hit':int(selected==y and not void),'market_hit':int(market==y and not void),
            'receipt_certified':False,'execution_price_certified':False}


def applies(row,policy):
    return policy in ('score_all','market_all') or policy=='score_favorites' and row['group']=='favorite' or policy in ('score_underdogs','market_same_underdog_events') and row['group']=='underdog'


def metrics(rows,policy):
    if policy not in POLICIES:raise ValueError('unknown policy')
    bets=[r for r in rows if applies(r,policy)]
    market=policy.startswith('market');netkey='market_net' if market else 'score_net';hitkey='market_hit' if market else 'score_hit'
    total=sum((Decimal(r[netkey]) for r in bets),Decimal(0));n=len(bets);nonvoid=sum(not r['void'] for r in bets)
    daily={r['day']:Decimal(0) for r in rows};monthly={r['month']:Decimal(0) for r in rows}
    for row in bets:
        daily[row['day']]+=Decimal(row[netkey]);monthly[row['month']]+=Decimal(row[netkey])
    peak=balance=dd=Decimal(0)
    for day in sorted(daily):
        balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
    return {'opportunities':len(rows),'bets':n,'coverage':n/len(rows) if rows else None,
            'nonvoid_bets':nonvoid,'hits':sum(r[hitkey] for r in bets),
            'hit_rate':sum(r[hitkey] for r in bets)/nonvoid if nonvoid else None,
            'net_units':str(total),'turnover_roi':float(total/n) if n else None,
            'net_per_opportunity':float(total/len(rows)) if rows else None,
            'max_daily_close_drawdown_units':float(dd),
            'monthly_net_units':{m:float(v) for m,v in sorted(monthly.items())},
            'negative_months':sum(v<0 for v in monthly.values()),'positive_months':sum(v>0 for v in monthly.values()),
            'zero_months':sum(v==0 for v in monthly.values()),
            'meaning':'flat unit, unbet cash zero; no wallet/monthly cap replay; daily-close DD ordered by UTC decision day'}


def paired(rows,under_only=False):
    eligible=[r for r in rows if r['group']=='underdog'] if under_only else rows
    if not eligible:return {'n':0,'weeks':0,'delta_roi':None,'ci95':[None,None]}
    grouped=defaultdict(lambda:np.zeros(2))
    for r in eligible:grouped[r['week']]+=np.array([1.,float(Decimal(r['score_net'])-Decimal(r['market_net']))])
    data=np.array([grouped[w] for w in sorted(grouped)]);rng=np.random.default_rng(20261031)
    draw=data[rng.integers(0,len(data),size=(2000,len(data)))].sum(axis=1)
    return {'n':len(eligible),'weeks':len(data),'delta_roi':float(data[:,1].sum()/len(eligible)),
            'ci95':np.quantile(draw[:,1]/draw[:,0],[.025,.975]).tolist(),
            'limitation':'week-cluster descriptive interval, not adjusted for repeated retrospective selection; no independent causal or confirmatory claim'}


def analyze(rows):
    result={'policies':{p:metrics(rows,p) for p in POLICIES},
            'paired_all':paired(rows),'paired_underdogs':paired(rows,True)}
    result['leave_one_month_out']={m:{'paired_all':paired([r for r in rows if r['month']!=m]),
            'score_underdogs':metrics([r for r in rows if r['month']!=m],'score_underdogs')}
            for m in sorted({r['month'] for r in rows})}
    return result


def base_module():
    spec=importlib.util.spec_from_file_location('underdog_original_score_engine',SOURCE/'model.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def replay_older(history,targets):
    # The inherited due() replaces tzinfo: reject timestamps rather than silently
    # interpreting them as date-only observations.
    for row in history:
        if len(row['date'])!=10 or datetime.fromisoformat(row['date']).date().isoformat()!=row['date']:
            raise ValueError('history requires date-only values')
    base=base_module();records=[];audit=[]
    for year,league in sorted({(at(r['decision_at']).year,r['league']) for r in targets}):
        prior=[r for r in history if r['league']==league and int(r['date'][:4])<year]
        freeze=datetime(year,1,5,tzinfo=timezone.utc)
        engine=base.Engine(prior,league,'score',0.)
        engine.advance(freeze)
        state_before=engine.state.fingerprint()
        selected=sorted([r for r in targets if at(r['decision_at']).year==year and r['league']==league],key=lambda r:(at(r['decision_at']),r['event_id']))
        excluded=[]
        for target in selected:
            if at(target['decision_at'])<freeze:
                excluded.append(target['event_id']);continue
            pred,_=engine.predict(target['home'],target['away'],at(target['decision_at']),online=False)
            records.append(pack(target,pred,'frozen'))
        if engine.state.fingerprint()!=state_before:raise AssertionError('frozen older state changed')
        audit.append({'year':year,'league':league,'training_rows':len(prior),'max_training_date':max((r['date'] for r in prior),default=None),
                      'freeze':freeze.isoformat(),'state_sha256':state_before,'excluded_before_freeze':excluded,'frozen_state_preserved':True})
    return records,audit


def run():
    pred_path=SOURCE/'results/predictions.jsonl.gz';target_path=SOURCE/'inputs/targets.jsonl.gz';history_path=SOURCE/'inputs/history.jsonl.gz'
    if sha(pred_path)!=EXPECTED_PRED or sha(target_path)!=EXPECTED_TARGET:raise ValueError('source hash changed')
    targets=read(target_path);history=read(history_path);source={}
    for r in read(pred_path):
        if r['model']=='score-static':
            key=(r['league'],r['event_id'],r['cohort'],r['mode'])
            if key in source:raise ValueError('duplicate saved prediction')
            source[key]=r
    records=[]
    for target in targets:
        if at(target['decision_at']).year!=2026:continue
        for mode in ('frozen','prequential'):
            old=source[(target['league'],target['event_id'],target['cohort'],mode)]
            for field in ('odds','outcome','decision_at','kickoff','sides'):
                if target[field]!=old[field]:raise ValueError('fixture/price mismatch')
            row=pack(target,old['candidate']['p'],mode,old['candidate']['void'])
            if Decimal(row['score_net'])!=Decimal(old['candidate']['profit_cents'])/100 or Decimal(row['market_net'])!=Decimal(old['market']['profit_cents'])/100:
                raise AssertionError('source settlement mismatch')
            records.append(row)
    older,audit=replay_older(history,[r for r in targets if at(r['decision_at']).year<2026])
    records+=older
    result={}
    for cohort,year,mode in sorted({(r['cohort'],r['year'],r['mode']) for r in records}):
        base=[r for r in records if (r['cohort'],r['year'],r['mode'])==(cohort,year,mode)]
        key=f'{cohort}/{year}/{mode}';result[key]={}
        periods=['earlier','discovery','all'] if year==2026 else ['older']
        for period in periods:
            rows=base if period=='all' else [r for r in base if r['period']==period]
            result[key][period]={'all_leagues':analyze(rows),
                'by_league':{l:analyze([r for r in rows if r['league']==l]) for l in LEAGUES}}
    # Exact source-cohort duplicates and cross-cohort fixture overlap are descriptive;
    # do not sum actual/proxy modes or periods as independent observations.
    signatures=defaultdict(set)
    for target in targets:
        signatures['actual' if target['actual_votes'] else 'proxy'].add((target['league'],at(target['kickoff']).isoformat(),target['home'],target['away']))
    actual=[r for r in records if r['cohort']=='actual_saved_quote' and r['year']==2026 and r['mode']=='frozen']
    if len(actual)!=445 or sum(r['period']=='discovery' for r in actual)!=122:raise ValueError('source cohort membership changed')
    save(HERE/'results.json',result)
    write_gz(HERE/'ledger.jsonl.gz',records)
    save(HERE/'audit.json',{'input_hashes':{p.relative_to(ROOT).as_posix():sha(p) for p in (pred_path,target_path,history_path)},
        'source_model_lf_sha256':hashlib.sha256((SOURCE/'model.py').read_bytes().replace(b'\r\n',b'\n')).hexdigest(),
        'older_year_states':audit,'ledger_rows':len(records),
        'cohort_counts_mode_frozen':{f'{c}/{y}':sum((r['cohort'],r['year'],r['mode'])==(c,y,'frozen') for r in records) for c,y in sorted({(r['cohort'],r['year']) for r in records})},
        'exact_fixture_cross_cohort_overlap':len(signatures['actual']&signatures['proxy']),
        'new_independent_pregame_certified_events':0,'warning':'All retrospective; never sum modes or all-period aggregates with their constituent rows. Older samples tiny, proxy quotes not execution-certified. Outcome-time is a historical proxy.'})
    selected=result['actual_saved_quote/2026/frozen']
    compact={period:{'policies':row['all_leagues']['policies'],'paired_all':row['all_leagues']['paired_all'],'paired_underdogs':row['all_leagues']['paired_underdogs']} for period,row in selected.items()}
    print(json.dumps(compact,ensure_ascii=False))
    return result


if __name__=='__main__':run()
