import os
for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'): os.environ[k]='1'
import sys, json, gzip, hashlib
from pathlib import Path
from collections import defaultdict
from decimal import Decimal
import numpy as np
from scipy.optimize import minimize
from scipy.special import softmax, logsumexp
ROOT=Path(__file__).resolve().parents[2]; HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'experiments/all-sports-underdog-20261031'))
from common import causal_features

def fit_predict(x,q,y,train,test,kind):
    mu=x[train].mean(0); sd=x[train].std(0); sd[sd<1e-9]=1
    z=np.clip((x-mu)/sd,-5,5)
    if kind=='linear': f=z
    elif kind=='rff':
        rng=np.random.default_rng(410); w=rng.normal(0,0.5,(z.shape[1],64)); b=rng.uniform(0,2*np.pi,64)
        f=np.column_stack([z,np.sqrt(2/64)*np.cos(z@w+b)])
    elif kind=='spline':
        # Fixed linear spline hinges and predeclared market/form interactions.
        f=np.column_stack([z]+[np.maximum(z[:,j]-k,0) for j in (0,1,4) for k in (-1.,0.,1.)]+[z[:,4]*z[:,j] for j in (0,1)])
    else: raise ValueError(kind)
    f=np.column_stack([np.ones(len(z)),f]); k=q.shape[1]; yt=np.eye(k)[y[train]]; offset=np.log(q[train]); a=f[train]
    def obj(v):
        b=v.reshape(f.shape[1],k); logits=offset+a@b; penalty=b.copy(); penalty[0]=0
        return np.mean(logsumexp(logits,axis=1)-logits[np.arange(len(yt)),y[train]])+.03*np.sum(penalty**2), (a.T@(softmax(logits,axis=1)-yt)/len(yt)+.06*penalty).ravel()
    opt=minimize(obj,np.zeros(f.shape[1]*k),jac=True,method='L-BFGS-B',options={'maxiter':500,'ftol':1e-10})
    if not opt.success: raise RuntimeError(str(opt.message))
    return softmax(np.log(q[test])+f[test]@opt.x.reshape(f.shape[1],k),axis=1),int(opt.nit)

def metrics(rows):
    n=len(rows); net=sum((Decimal(r['net']) for r in rows),Decimal(0)); daily=defaultdict(Decimal)
    for r in rows: daily[r['day']]+=Decimal(r['net'])
    bal=peak=dd=Decimal(0)
    for d in sorted(daily):
        bal+=daily[d]; peak=max(peak,bal); dd=max(dd,peak-bal)
    return {'n':n,'hit_rate':sum(r['hit'] for r in rows)/n,'net':str(net),'roi':float(net/n),'ll':float(np.mean([-np.log(r['p'][r['y']]) for r in rows])),'brier':float(np.mean([sum((p-(i==r['y']))**2 for i,p in enumerate(r['p'])) for r in rows])),'mdd_daily_units':str(dd)}

def paired(a,b):
    assert [r['event_id'] for r in a]==[r['event_id'] for r in b]
    weeks=defaultdict(list)
    from datetime import date
    for i,r in enumerate(a):
        yr,w,_=date.fromisoformat(r['day']).isocalendar(); weeks[(yr,w)].append(i)
    groups=list(weeks.values()); rng=np.random.default_rng(211)
    d=np.array([[int(x['hit'])-int(y['hit']),float(Decimal(x['net'])-Decimal(y['net'])), -np.log(x['p'][x['y']])+np.log(y['p'][y['y']])] for x,y in zip(a,b)])
    sums=np.array([d[ix].sum(axis=0) for ix in groups]); sizes=np.array([len(ix) for ix in groups]); samples=[]
    for _ in range(1000):
        ix=rng.integers(0,len(groups),len(groups)); samples.append(sums[ix].sum(axis=0)/sizes[ix].sum())
    samples=np.array(samples); point=d.mean(0); out={}
    for j,key in enumerate(('hit_rate','roi','ll')):
        # Percentile bootstrap sign-tail statistic; descriptive, unadjusted, not null-imposed.
        out[key]={'difference':float(point[j]),'ci95':np.quantile(samples[:,j],[.025,.975]).tolist(),'p_sign_two_sided':float(min(1,2*min(np.mean(samples[:,j]<=0),np.mean(samples[:,j]>=0))))}
    out['weeks']=len(groups);return out

def main():
    allrows=[]; summary={'scope':'2026-01-01..2026-08-26; retrospective, repeated-use; day-only prices, no execution evidence','training':'2023-01-01..2024-12-27, frozen after 2025 validation; causal team state assumes day+4 availability','models':{},'validation':{},'counts':{},'limits':['No tipster/player or operational model inputs','Draws excluded by source for non-soccer; void/missing not recoverable','corrupt team/league labels may merge distinct identities','source duplicate removal and source selection limit coverage','No timestamp or purchasability proof; exact result availability unverified'],'bootstrap':'1000 paired ISO weeks, candidate minus comparator; positive hit/ROI is better, negative LL is better; percentile CI and two-sided sign-tail proportion, unadjusted; multiple testing','comparisons':{}}
    for sport in ('bs','sc','bk','vl'):
        path=ROOT/f'experiments/all-sports-underdog-20261031/results/{sport}/domestic_archive/ledger.jsonl.gz'
        with gzip.open(path,'rt',encoding='utf8') as f: raw=[json.loads(line) for line in f]
        rows,base=causal_features(raw); q=np.array([r['market_p'] for r in rows]); y=np.array([r['y'] for r in rows]); days=np.array([r['day'] for r in rows]);
        if len(set(r['id'] for r in rows))!=len(rows):raise ValueError('duplicate id')
        if not np.allclose(q.sum(axis=1),1) or np.any(q<=0):raise ValueError('invalid market')
        # Per-class centered log odds contain market information for the same-information control.
        logits=np.log(q); centered=logits-logits.mean(axis=1,keepdims=True)
        x=np.column_stack([base,centered])
        train=np.where((days>='2023-01-01')&(days<='2024-12-27'))[0]
        val=np.where((days>='2025-01-01')&(days<='2025-12-31'))[0]
        test=np.where((days>='2026-01-01')&(days<='2026-08-26'))[0]
        summary['counts'][sport]={'train':len(train),'validation':len(val),'evaluation':len(test),'input':len(rows),'fallback_eval':sum(rows[i]['fallback'] for i in test),'corrupt_league_eval':sum('\ufffd' in rows[i]['league'] for i in test),'corrupt_team_eval':sum('\ufffd' in rows[i]['home'] or '\ufffd' in rows[i]['away'] for i in test)}
        predictions={'market':q[test]}; valpred={'market':q[val]}; iterations={}
        for kind in ('linear','rff','spline'):
            p,it=fit_predict(x,q,y,train,np.concatenate([val,test]),kind); valpred[kind]=p[:len(val)]; predictions[kind]=p[len(val):];iterations[kind]=it
        summary['counts'][sport]['optimizer_iterations']=iterations
        def make(ix, pp):
            out={}
            for name,prob in pp.items():
                out[name]=[]
                for i,p in zip(ix,prob):
                    r=rows[i];pick=int(np.argmax(p)); hit=pick==r['y']; net=Decimal(str(r['odds'][pick]))-1 if hit else Decimal(-1)
                    if abs(sum(p)-1)>1e-10:raise ValueError('probability sum')
                    out[name].append({'event_id':r['id'],'day':r['day'],'sport':sport,'league':r['league'],'y':int(r['y']),'odds':r['odds'],'market_p':r['market_p'],'model':name,'p':p.tolist(),'pick':pick,'hit':hit,'net':str(net)})
            return out
        valrows=make(val,valpred); rr=make(test,predictions)
        summary['validation'][sport]={m:metrics(r) for m,r in valrows.items()}
        summary['models'][sport]={}
        for m,r in rr.items():
            allrows.extend(r); subgroup={}
            for field in ('league','month','quarter'):
                groups=defaultdict(list)
                for v in r:
                    key=v['league'] if field=='league' else v['day'][:7] if field=='month' else v['day'][:4]+'-Q'+str((int(v['day'][5:7])-1)//3+1)
                    groups[key].append(v)
                subgroup[field]={key:metrics(group) for key,group in sorted(groups.items())}
            summary['models'][sport][m]={'overall':metrics(r),'subgroups':subgroup}
        summary['comparisons'][sport]={}
        for m in ('rff','spline'):
            summary['comparisons'][sport][m]={}
            for control in ('market','linear'):
                a,b=rr[m],rr[control]; changed=[(u,v) for u,v in zip(a,b) if u['pick']!=v['pick']]
                summary['comparisons'][sport][m][control]={'paired':paired(a,b),'changed':len(changed),'directions':dict(__import__('collections').Counter(f"{v['pick']}->{u['pick']}" for u,v in changed)),'control_hit_candidate_miss':sum(v['hit'] and not u['hit'] for u,v in changed),'control_miss_candidate_hit':sum(not v['hit'] and u['hit'] for u,v in changed)}
    with gzip.GzipFile(filename=str(HERE/'predictions.jsonl.gz'),mode='wb',mtime=0) as f:
        for r in allrows:f.write((json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n').encode())
    (HERE/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
if __name__=='__main__':main()
