"""Offline pre-registered K League nearest-neighbor experiment."""
from collections import defaultdict, Counter
from datetime import date,timedelta
from decimal import Decimal
from pathlib import Path
import hashlib,json,math
import numpy as np

ROOT=Path(__file__).resolve().parent
SHA='158cd931f3fa576b97678dfcc0b44042b9ef1a5826183853fb6d9b0beb314f05'
K=(15,30,60); VAL=date(2024,8,1); TRAIN=date(2024,7,25)


def read():
    raw=(ROOT/'inputs/oof.jsonl').read_bytes().replace(b'\r\n',b'\n')
    if hashlib.sha256(raw).hexdigest()!=SHA: raise ValueError('input SHA mismatch')
    rows=[json.loads(line) for line in raw.splitlines()]
    if len(rows)!=455 or len({r['id'] for r in rows})!=455: raise ValueError('cohort duplicate or incomplete')
    for r in rows:
        day=date.fromisoformat(r['day']);cut=date.fromisoformat(r['dc_training_cutoff'])
        if cut>day-timedelta(days=7): raise ValueError('DC OOF cutoff violation')
        if r['split'] not in ('meta_history_2024','evaluation_2025') or (r['split']=='meta_history_2024')!=(day.year==2024) or (r['split']=='evaluation_2025')!=(day.year==2025): raise ValueError('unexpected split')
        if type(r['y']) is not int or r['y'] not in (0,1,2): raise ValueError('missing settlement')
        if any(len(r[k])!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in r[k]) or not math.isclose(sum(r[k]),1,abs_tol=1e-8) for k in ('market_p','dc_p')): raise ValueError('invalid probability vector')
        if len(r['prices'])!=3 or any(not Decimal(x).is_finite() or Decimal(x)<=1 for x in r['prices']): raise ValueError('invalid prices')
    if Counter(r['split'] for r in rows)!={'meta_history_2024':228,'evaluation_2025':227}: raise ValueError('cohort count mismatch')
    return rows


def features(row):
    # Strict allowlist: callers cannot supply scores or target outcome.
    if set(row)!={'market_p','dc_p'}: raise ValueError('unexpected prediction feature')
    m=np.asarray(row['market_p'],float);d=np.asarray(row['dc_p'],float)
    if m.shape!=(3,) or d.shape!=(3,) or not np.isfinite(m).all() or not np.isfinite(d).all(): raise ValueError('invalid prediction vector')
    return np.r_[m,d]


def prepare(rows):
    return tuple((r['id'],features({'market_p':r['market_p'],'dc_p':r['dc_p']}),r['y']) for r in rows)


def predict(bank, target, k):
    if k not in K or len(bank)<k: raise ValueError('insufficient neighbors')
    x=features(target)
    ordered=sorted(((float(np.sum((v-x)**2)),ident,label) for ident,v,label in bank),key=lambda t:(t[0],t[1]))[:k]
    counts=np.bincount([r[2] for r in ordered],minlength=3)
    return ((counts+1)/(k+3)).tolist()


def pick(probs):return int(np.argmax(probs))


def metrics(rows,key):
    net=Decimal(0);hits=0;ll=0;bs=0;daily=defaultdict(Decimal);monthly=defaultdict(Decimal);changed=0
    for r in rows:
        pred=r['forecasts'][key]; p=pred['probability']; i=pred['pick']; y=r['result']; hit=i==y
        v=Decimal(r['prices'][i])-Decimal(1) if hit else Decimal(-1)
        if v!=Decimal(pred['net']): raise ValueError('settlement mismatch')
        net+=v;hits+=hit;daily[r['day']]+=v;monthly[r['day'][:7]]+=v;ll-=math.log(p[y]);bs+=sum((p[j]-(j==y))**2 for j in range(3));changed+=i!=r['forecasts']['market']['pick']
    balance=peak=dd=Decimal(0)
    for day in sorted(daily):
        balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
    n=len(rows)
    return {'n':n,'coverage':1.0,'hits':hits,'accuracy':hits/n,'roi':float(net/Decimal(n)),'net_units':str(net),'logloss':ll/n,'brier':bs/n,'pick_changes_vs_market':changed,'monthly_units':{k:str(v) for k,v in sorted(monthly.items())},'daily_close_max_drawdown_units':str(dd)}


def paired(rows):
    groups=defaultdict(lambda:np.zeros(3))
    for r in rows:
        d=date.fromisoformat(r['day']);a=r['forecasts']['market'];b=r['forecasts']['neighbor'];w=d.isocalendar()[:2]
        groups[w]+=np.array([1,int(b['hit'])-int(a['hit']),float(Decimal(b['net'])-Decimal(a['net']))])
    blocks=np.array([groups[w] for w in sorted(groups)]);rng=np.random.default_rng(20261102)
    samples=blocks[rng.integers(0,len(blocks),size=(5000,len(blocks)))].sum(axis=1)
    return {'observed_weeks':len(blocks),'resamples':5000,'seed':20261102,'accuracy_delta_ci95':np.quantile(samples[:,1]/samples[:,0],[.025,.975]).tolist(),'roi_delta_ci95':np.quantile(samples[:,2]/samples[:,0],[.025,.975]).tolist(),'interpretation':'descriptive only; time dependence and repeated prior inspection remain'}


def run(rows):
    hist=[r for r in rows if r['split']=='meta_history_2024'];test=sorted((r for r in rows if r['split']=='evaluation_2025'),key=lambda r:(r['day'],r['id']))
    train=[r for r in hist if date.fromisoformat(r['day'])<TRAIN]
    val=[r for r in hist if VAL<=date.fromisoformat(r['day'])<date(2025,1,1)]
    if len(train)<max(K) or not val or not test: raise ValueError('insufficient cohort')
    bank=prepare(train);losses={}
    for k in K:
        losses[k]=sum(-math.log(predict(bank,{'market_p':r['market_p'],'dc_p':r['dc_p']},k)[r['y']]) for r in val)/len(val)
    best=min(losses.values());chosen=next(k for k in K if losses[k]<=best+1e-12)
    first=date.fromisoformat(test[0]['day']);final=[r for r in hist if date.fromisoformat(r['day'])<=first-timedelta(days=7)]
    if len(final)!=len(hist): raise ValueError('full 2024 neighbor bank not available')
    bank=prepare(final); ledger=[]
    for r in test:
        x={'market_p':r['market_p'],'dc_p':r['dc_p']}
        ps={'market':r['market_p'],'dc':r['dc_p'],'neighbor':predict(bank,x,chosen)}
        # Predictions constructed before and independently of result settlement.
        saved={name:{'probability':p,'pick':pick(p)} for name,p in ps.items()}
        for name,v in saved.items():
            v['hit']=v['pick']==r['y'];v['net']=str(Decimal(r['prices'][v['pick']])-1 if v['hit'] else Decimal(-1))
        ledger.append({'id':r['id'],'day':r['day'],'prices':r['prices'],'result':r['y'],'forecasts':saved})
    return {'source_rows':len(rows),'history_n':len(hist),'validation_train_n':len(train),'validation_n':len(val),'validation_logloss':{str(k):v for k,v in losses.items()},'selected_k':chosen,'test_n':len(test),'final_training_n':len(final),'source_price_receipt_verified':False,'source_tipster_available':False,'metrics':{k:metrics(ledger,k) for k in ('market','dc','neighbor')},'paired_vs_market':paired(ledger)},ledger


def main():
    report,ledger=run(read())
    (ROOT/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    (ROOT/'ledger.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in ledger),encoding='utf8',newline='\n')
    print(json.dumps({'selected_k':report['selected_k'],'validation_n':report['validation_n'],'test_n':report['test_n'],'metrics':report['metrics']},ensure_ascii=False))

if __name__=='__main__':main()
