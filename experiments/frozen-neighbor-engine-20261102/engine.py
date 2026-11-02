"""Frozen research-only three-model evaluator; no acquisition or DC fitting."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib, json, math
from pathlib import Path
import numpy as np
from src.devig import shin

BANK_SHA='158cd931f3fa576b97678dfcc0b44042b9ef1a5826183853fb6d9b0beb314f05'
NAMES=('market','global','neighbor')

@dataclass(frozen=True)
class Bank:
    rows: tuple
    sha: str


def vector(v):
    if not isinstance(v,(list,tuple)) or len(v)!=3 or any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in v) or not math.isclose(sum(v),1,abs_tol=1e-8):
        raise ValueError('invalid probability vector')
    return tuple(float(x) for x in v)


def load_bank(path):
    raw=Path(path).read_bytes().replace(b'\r\n',b'\n')
    sha=hashlib.sha256(raw).hexdigest()
    if sha!=BANK_SHA: raise ValueError('bank source hash mismatch')
    # Parse only the 2024 bank, never the historical 2025 outcome rows.
    data=[json.loads(s) for s in raw.splitlines() if b'"split": "meta_history_2024"' in s]
    if len(data)!=228 or len({r['id'] for r in data})!=228: raise ValueError('bank identity/count')
    rows=[]
    for r in data:
        day=date.fromisoformat(r['day'])
        if day.year!=2024 or date.fromisoformat(r['dc_training_cutoff'])>day-timedelta(days=7) or type(r['y']) is not int or r['y'] not in (0,1,2): raise ValueError('bank chronology/outcome')
        rows.append((r['id'],day,vector(r['market_p']),vector(r['dc_p']),r['y']))
    return Bank(tuple(rows),sha)


def forecast(bank,features):
    if not isinstance(bank,Bank) or bank.sha!=BANK_SHA or len(bank.rows)!=228: raise ValueError('unverified bank')
    if set(features)!={'market_p','dc_p'}: raise ValueError('forecast features must exclude outcomes and metadata')
    market=vector(features['market_p']);dc=vector(features['dc_p'])
    def adjusted(rows,denominator):
        p=[max(1e-6,market[j]+sum((int(r[4]==j)-r[2][j]) for r in rows)/denominator) for j in range(3)]
        return [x/sum(p) for x in p]
    nearest=sorted(bank.rows,key=lambda r:(float(np.linalg.norm(np.asarray(r[2]+r[3])-np.asarray(market+dc))),r[0]))[:60]
    return {'market':list(market),'global':adjusted(bank.rows,228*90/60),'neighbor':adjusted(nearest,90)}


def check_price(prices):
    if not isinstance(prices,list) or len(prices)!=3 or any(not isinstance(x,str) for x in prices): raise ValueError('three decimal-string prices required')
    try: p=[Decimal(x) for x in prices]
    except InvalidOperation as e: raise ValueError('bad price') from e
    if any(not x.is_finite() or x<=1 for x in p): raise ValueError('invalid price')
    return p


def parse_day(x):
    try:return date.fromisoformat(x)
    except (TypeError,ValueError) as e:raise ValueError('invalid date') from e


def validate(rows,bank):
    if not isinstance(bank,Bank) or bank.sha!=BANK_SHA or len(bank.rows)!=228: raise ValueError('unverified bank')
    ids=set(); keys=set(); eligible=[];excluded=Counter();last=max(r[1] for r in bank.rows);bankids={r[0] for r in bank.rows}
    if not isinstance(rows,list): raise ValueError('opportunities must be a list')
    for r in rows:
        if not isinstance(r,dict) or not isinstance(r.get('id'),str) or not r['id'].strip() or r['id'] in ids or r['id'] in bankids: raise ValueError('duplicate/invalid event identity')
        ids.add(r['id'])
        if r.get('eligibility')=='excluded':
            if set(r)!={'id','eligibility','reason'} or not isinstance(r['reason'],str) or not r['reason'].strip(): raise ValueError('exclusion reason required')
            excluded[r['reason']]+=1;continue
        allowed={'id','eligibility','day','kickoff','home_id','away_id','competition','market','selection_order','prices','price_source_sha256','price_time_kind','price_time','identity_evidence','dc_p','dc_reference','dc_training_cutoff','dc_model','dc_training_sha256','result_status','home_score','away_score','official_result_evidence'}
        if set(r)!=allowed or r['eligibility']!='eligible':raise ValueError('invalid opportunity fields')
        day=parse_day(r['day']);ref=parse_day(r['dc_reference']);cut=parse_day(r['dc_training_cutoff'])
        if day.year<2026 or day<=last or ref!=day.replace(day=1) or cut!=ref-timedelta(days=7):raise ValueError('future bank overlap or DC cutoff')
        try: kickoff=datetime.fromisoformat(r['kickoff'])
        except (TypeError,ValueError) as e:raise ValueError('invalid kickoff') from e
        if kickoff.tzinfo is None or kickoff.utcoffset() is None or kickoff.date()!=day:raise ValueError('kickoff date/timezone required')
        if not all(isinstance(r[x],str) and r[x].strip() for x in ('home_id','away_id','identity_evidence','official_result_evidence')) or r['home_id']==r['away_id']:raise ValueError('team/identity evidence')
        key=(r['kickoff'],r['home_id'],r['away_id'])
        if key in keys:raise ValueError('duplicate fixture identity')
        keys.add(key)
        if r['competition']!='KLEAGUE1' or r['market']!='full_time_1x2' or r['selection_order']!=['home','draw','away']:raise ValueError('invalid market')
        for x in ('price_source_sha256','dc_training_sha256'):
            if not isinstance(r[x],str) or len(r[x])!=64 or any(c not in '0123456789abcdef' for c in r[x]):raise ValueError('source SHA required')
        if r['price_time_kind']=='record_date_only':
            if parse_day(r['price_time'])>day:raise ValueError('post-match record date')
        elif r['price_time_kind']=='observed_at':
            try: pt=datetime.fromisoformat(r['price_time'])
            except (TypeError,ValueError) as e:raise ValueError('invalid observed time') from e
            if pt.tzinfo is None or pt.utcoffset() is None or pt>kickoff-timedelta(minutes=30):raise ValueError('late price observation')
        else:raise ValueError('missing price timing kind')
        if r['dc_model']!='kleague_dc_half180_l2_10_monthly_lag7_rho_fixed':raise ValueError('missing fixed DC provenance')
        vector(r['dc_p']);check_price(r['prices'])
        if r['result_status']!='official_final' or any(type(r[x]) is not int or r[x]<0 for x in ('home_score','away_score')):raise ValueError('missing official final score')
        eligible.append(r)
    return eligible,dict(excluded)


def evaluate(opportunities,bank):
    eligible,excluded=validate(opportunities,bank);ledger=[]
    for r in sorted(eligible,key=lambda x:(x['day'],x['kickoff'],x['id'])):
        market=shin([float(x) for x in r['prices']])
        predictions=forecast(bank,{'market_p':market,'dc_p':r['dc_p']})
        y=0 if r['home_score']>r['away_score'] else 1 if r['home_score']==r['away_score'] else 2
        forecasts={}
        for name,p in predictions.items():
            pick=max(range(3),key=lambda i:p[i]);hit=pick==y
            net=Decimal(r['prices'][pick])-1 if hit else Decimal(-1)
            forecasts[name]={'p':p,'pick':pick,'hit':hit,'net':str(net)}
        ledger.append({'id':r['id'],'day':r['day'],'result':y,'prices':r['prices'],'forecasts':forecasts})
    n=len(ledger);result={'opportunities':len(opportunities),'eligible':n,'excluded':excluded,'models':{},'pairs':{},'price_receipt_and_executability':'unverified unless separately evidenced','dc_availability':'assumed; provenance assertion requires independent audit','accounting':'hypothetical flat unit; not actual wallet'}
    for name in NAMES:
        if not n:
            result['models'][name]={'bets':0,'coverage_eligible':None,'coverage_opportunities':None if not opportunities else 0,'hits':0,'accuracy':None,'roi':None,'logloss':None,'brier':None,'net_units':'0','monthly_units':{},'daily_close_max_drawdown_units':None};continue
        daily=defaultdict(Decimal);monthly=defaultdict(Decimal);net=Decimal(0);hits=0;ll=0;brier=0
        for line in ledger:
            f=line['forecasts'][name];v=Decimal(f['net']);net+=v;hits+=f['hit'];daily[line['day']]+=v;monthly[line['day'][:7]]+=v;y=line['result'];ll-=math.log(f['p'][y]);brier+=sum((f['p'][j]-int(y==j))**2 for j in range(3))
        balance=peak=dd=Decimal(0)
        for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
        result['models'][name]={'bets':n,'coverage_eligible':1.0,'coverage_opportunities':n/len(opportunities),'hits':hits,'accuracy':hits/n,'roi':float(net/n),'net_units':str(net),'logloss':ll/n,'brier':brier/n,'monthly_units':{k:str(v) for k,v in sorted(monthly.items())},'daily_close_max_drawdown_units':str(dd)}
    for left,right in (('neighbor','market'),('neighbor','global'),('global','market')):
        groups=defaultdict(lambda:np.zeros(3));changes=Counter()
        for line in ledger:
            a=line['forecasts'][left];b=line['forecasts'][right];d=date.fromisoformat(line['day']);key=d.isocalendar()[:2]
            groups[key]+=np.array([1,int(a['hit'])-int(b['hit']),float(Decimal(a['net'])-Decimal(b['net']))])
            if a['pick']!=b['pick']:changes['changed']+=1;changes['recovered']+=int(a['hit'] and not b['hit']);changes['damaged']+=int(not a['hit'] and b['hit']);changes['both_wrong']+=int(not a['hit'] and not b['hit'])
        summary={'changes':{key:changes[key] for key in ('changed','recovered','damaged','both_wrong')},'weeks':len(groups),'accuracy_delta_ci95':None,'roi_delta_ci95':None}
        if len(groups)>=2:
            blocks=np.array([groups[w] for w in sorted(groups)]);rng=np.random.default_rng(20261102);samples=blocks[rng.integers(0,len(blocks),size=(5000,len(blocks)))].sum(axis=1)
            summary.update(accuracy_delta_ci95=np.quantile(samples[:,1]/samples[:,0],[.025,.975]).tolist(),roi_delta_ci95=np.quantile(samples[:,2]/samples[:,0],[.025,.975]).tolist())
        result['pairs'][left+'_vs_'+right]=summary
    comparisons=[result['pairs']['neighbor_vs_market'],result['pairs']['neighbor_vs_global']]
    complete=all(c['accuracy_delta_ci95'] is not None and c['roi_delta_ci95'] is not None for c in comparisons)
    gate=bool(complete and all(c['accuracy_delta_ci95'][0]>0 and c['roi_delta_ci95'][0]>0 for c in comparisons) and all(result['models']['neighbor'][metric]<=result['models'][baseline][metric] for baseline in ('market','global') for metric in ('logloss','brier')))
    result['review_gate']={'status':('criteria_met_review_only' if gate else 'criteria_not_met' if complete else 'insufficient_data'),'automatic_adoption':False}
    return result,ledger
