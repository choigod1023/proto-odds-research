import os
for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'): os.environ[k]='1'
import json,gzip,hashlib,collections
from pathlib import Path
from datetime import date,timedelta
from decimal import Decimal
import numpy as np
from scipy.optimize import minimize
from scipy.special import betainc

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
NAMES=('market','fixed_equal','beta_pool','covariance_pool')

def beta_pool(x,a,b):
    # Ordered categorical CDF increments; category order is explicitly fixed.
    q=np.clip(x.mean(axis=1),0,1); c=np.cumsum(q,axis=1); c[:,-1]=1
    z=betainc(a,b,np.clip(c,0,1)); z[:,-1]=1
    p=np.diff(np.column_stack((np.zeros(len(z)),z)),axis=1)
    return np.maximum(p,0)/np.maximum(p.sum(axis=1,keepdims=True),1e-300)

def fit(x,y):
    n,k=x.shape[0],x.shape[2]; one=np.eye(k)[y]; equal=np.ones(3)/3
    def bl(v):
        p=beta_pool(x,*np.exp(v)); return -np.log(np.clip(p[np.arange(n),y],1e-12,1)).mean()+.1*np.sum(v*v)
    b=minimize(bl,[0.,0.],method='L-BFGS-B',bounds=[(-1.5,1.5)]*2)
    if not b.success: raise RuntimeError(str(b.message))
    # Empirical cross-expert residual covariance captures shared forecast errors.
    e=x-one[:,None,:]; cov=np.einsum('nik,njk->ij',e,e)/n
    def obj(w):return w@cov@w+.1*np.sum((w-equal)**2)
    opt=minimize(obj,equal,method='SLSQP',bounds=[(0,1)]*3,constraints=[{'type':'eq','fun':lambda w:sum(w)-1}],options={'ftol':1e-12,'maxiter':200})
    if not opt.success:raise RuntimeError(str(opt.message))
    return {'a':float(np.exp(b.x[0])),'b':float(np.exp(b.x[1])),'weights':opt.x.tolist(),'error_covariance':cov.tolist(),'n':n}

def prob(x,model,state):
    if model=='market': p=x[:,0,:]
    elif model=='fixed_equal':p=x.mean(axis=1)
    elif model=='beta_pool':p=beta_pool(x,state['a'],state['b'])
    else:p=np.einsum('nik,i->nk',x,np.array(state['weights']))
    if not np.isfinite(p).all() or np.min(p)<-1e-12 or not np.allclose(p.sum(axis=1),1,atol=1e-9):raise ValueError('invalid probabilities')
    return p

def eligible_fit(rows,cutoff):return [r for r in rows if '2025-01-01'<=r['day']<=cutoff]

def data(rows):
    return np.array([[r['market_p'],r['predictions']['outcome_form']['p'],r['predictions']['market_residual']['p']] for r in rows]),np.array([r['y'] for r in rows],int)

def metrics(rr):
    if not rr:return None
    n=len(rr); daily=collections.defaultdict(Decimal); ll=0.;br=0.
    for r in rr:
        daily[r['day']]+=Decimal(r['net']); p=np.array(r['p']);y=r['y'];ll-=np.log(max(p[y],1e-12));br+=sum((p-np.eye(len(p))[y])**2)
    wealth=peak=dd=Decimal(0)
    for day in sorted(daily):
        wealth+=daily[day];peak=max(peak,wealth);dd=max(dd,peak-wealth)
    return {'n':n,'hits':sum(r['hit'] for r in rr),'hit_rate':sum(r['hit'] for r in rr)/n,'roi':float(wealth)/n,'log_loss':ll/n,'brier':br/n,'mdd_daily_units':float(dd)}

def paired(rr,base):
    weeks=collections.defaultdict(list)
    for r,b in zip(rr,base):
        assert r['event_id']==b['event_id']; d=date.fromisoformat(r['day']);week=(d-timedelta(days=d.weekday())).isoformat()
        weeks[week].append([int(r['hit'])-int(b['hit']),float(Decimal(r['net'])-Decimal(b['net'])),-np.log(max(r['p'][r['y']],1e-12))+np.log(max(b['p'][b['y']],1e-12))])
    vals=np.array([np.sum(v,axis=0) for v in weeks.values()]); counts=np.array([len(v) for v in weeks.values()]); rng=np.random.default_rng(20261108); draws=rng.integers(0,len(vals),size=(1000,len(vals))); dif=vals[draws].sum(axis=1)/counts[draws].sum(axis=1)[:,None]
    point=vals.sum(axis=0)/counts.sum(); out={}
    for i,key in enumerate(('hit_rate','roi','log_loss')):
        z=dif[:,i]; out[key]={'delta':float(point[i]),'ci95':np.quantile(z,[.025,.975]).tolist(),'p_two_sided_sign':float(min(1,2*min(np.mean(z<=0),np.mean(z>=0))))}
    return {'weeks':len(vals),'replicates':1000,'delta':'candidate minus comparator; positive hit/ROI and negative log loss favor candidate','p_method':'two-sided bootstrap sign proportion, no multiplicity adjustment', 'metrics':out}

def run():
    allout=[];summary={'period':['2026-01-01','2026-08-26'],'training':'2025-01-01 to 2025-12-28 inclusive, frozen thereafter; underlying experts use older historical rolling OOS fits','date_limit':'day-only; actual price receipt, availability and official result release unverified; day+4 assumption','models':{},'inputs':{},'limitations':['Previously reused retrospective data, not independent holdout','No players, tipsters, or operational model','Draw-excluded source outcomes, voids and missing data cannot be recovered','Possible duplicate fixtures and contaminated league names in source; no unverified alias merging'], 'ordering':'soccer home/draw/away; other sports home/away'}
    for sport in ('bs','sc','bk','vl'):
        path=ROOT/'experiments/all-sports-underdog-20261031/results'/sport/'domestic_archive/ledger.jsonl.gz'
        raw=path.read_bytes();rows=[json.loads(s) for s in gzip.decompress(raw).decode('utf8').splitlines()]; ids=[r['id'] for r in rows]
        if len(ids)!=len(set(ids)):raise ValueError('duplicate source event ids')
        train=eligible_fit(rows,'2025-12-28');ev=sorted([r for r in rows if '2026-01-01'<=r['day']<='2026-08-26'],key=lambda r:(r['day'],r['id']))
        tx,ty=data(train);ex,ey=data(ev);state=fit(tx,ty); summary['inputs'][sport]={'sha256':hashlib.sha256(raw).hexdigest(),'fit_n':len(train),'eval_n':len(ev),'eval_fallback_n':sum(r['fallback'] for r in ev),'parameters':state}
        by={}
        for name in NAMES:
            pp=prob(ex,name,state); out=[]
            for r,p in zip(ev,pp):
                pick=int(np.argmax(p)); hit=pick==r['y']; net=Decimal(str(r['odds'][pick]))-1 if hit else Decimal(-1)
                out.append({'event_id':r['id'],'day':r['day'],'sport':sport,'league':r['league'],'home':r['home'],'away':r['away'],'y':r['y'],'odds':r['odds'],'market_p':r['market_p'],'fallback':r['fallback'],'model':name,'p':p.tolist(),'pick':pick,'hit':hit,'net':str(net),'product':'general match winner'})
            by[name]=out;allout+=out
        summary['models'][sport]={}
        for name,rr in by.items():
            groups=collections.defaultdict(list)
            for r in rr:
                groups['league:'+r['league']].append(r);groups['month:'+r['day'][:7]].append(r);groups['quarter:'+r['day'][:4]+'Q'+str((int(r['day'][5:7])-1)//3+1)].append(r)
            compare={}
            for baseline in ('market','fixed_equal'):
                if name==baseline:continue
                bb=by[baseline];changed=[(a,b) for a,b in zip(rr,bb) if a['pick']!=b['pick']]
                compare[baseline]={'paired':paired(rr,bb),'changed':len(changed),'baseline_hit_to_candidate_miss':sum(b['hit'] and not a['hit'] for a,b in changed),'baseline_miss_to_candidate_hit':sum(not b['hit'] and a['hit'] for a,b in changed),'directions':dict(collections.Counter(str(b['pick'])+'->'+str(a['pick']) for a,b in changed))}
            summary['models'][sport][name]={'overall':metrics(rr),'groups':{k:metrics(v) for k,v in sorted(groups.items())},'comparisons':compare}
    with (HERE/'events.jsonl.gz').open('wb') as f:
        with gzip.GzipFile(fileobj=f,mode='wb',mtime=0,filename='') as g:
            for r in allout:g.write((json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n').encode())
    (HERE/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
if __name__=='__main__':run()
