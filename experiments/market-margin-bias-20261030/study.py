import collections, datetime as dt, gzip, hashlib, json, math
from pathlib import Path
import numpy as np
from scipy.optimize import brentq, minimize

ROOT=Path(__file__).resolve().parent
METHODS=['proportional','power','shin','additive-logit']
NAMES=METHODS+[m+'+league-home-draw' for m in METHODS]
FEATURES=frozenset(('odds','league','side_index_0based','kickoff','market'))
def probabilities(odds, method):
    q=1/np.asarray(odds,dtype=float); s=q.sum()
    if method=='proportional' or s<=1: return q/s
    if method=='power':
        k=brentq(lambda k:(q**k).sum()-1,0.01,100); return q**k
    if method=='shin':
        def f(z): return ((np.sqrt(z*z+4*(1-z)*q*q/s)-z)/(2*(1-z))).sum()-1
        z=brentq(f,0,0.999999); p=(np.sqrt(z*z+4*(1-z)*q*q/s)-z)/(2*(1-z)); return p/p.sum()
    logits=np.log(q/(1-q))
    def f(t): return (1/(1+np.exp(-np.clip(logits+t,-700,700)))).sum()-1
    t=brentq(f,-50,50); return 1/(1+np.exp(-np.clip(logits+t,-700,700)))

def load():
    rows=[]; counts=collections.Counter()
    with gzip.open(ROOT/'inputs/events.jsonl.gz','rt',encoding='utf8') as f:
      for line in f:
        event=json.loads(line); counts['fixtures']+=1; groups=collections.defaultdict(list)
        for c in event['choices']:
          if c['market'] in ('승무패','승패'): groups[c['market_id']].append(c)
        for cs in groups.values():
          cs=sorted(cs,key=lambda c:c['side_index_0based']); n=cs[0]['n_way']
          if n not in (2,3) or len(cs)!=n or [c['side_index_0based'] for c in cs]!=list(range(n)) or any(c['refund'] or c['result_status']!='settled' or c['odds']<=1 for c in cs) or sum(bool(c['hit']) for c in cs)!=1:
            counts['excluded_invalid']+=1; continue
          rows.append({'id':event['event_id']+':'+cs[0]['market_id'],'kickoff':event['kickoff'],'league':event['league'],'odds':[c['odds'] for c in cs],'y':next(i for i,c in enumerate(cs) if c['hit'])})
    rows.sort(key=lambda r:(r['kickoff'],r['id'])); counts['rows']=len(rows)
    dates=sorted(set(r['kickoff'] for r in rows)); boundaries=[dates[int(len(dates)*v)] for v in (.6,.8)]
    for r in rows: r['split']=0 if r['kickoff']<boundaries[0] else 1 if r['kickoff']<boundaries[1] else 2
    return rows,counts,boundaries

def fit(rows,base):
    offsets={}
    for league in sorted(set(r['league'] for r in rows)):
      subset=[r for r in rows if r['league']==league and r['split']==0]
      def obj(a):
        loss=5*np.dot(a,a); grad=10*a.copy()
        for r in subset:
          p=base[r['id']]; n=len(p); ix=[0,2] if n==2 else [0,1,2]; z=np.log(p)+a[ix]; z-=z.max(); pp=np.exp(z); pp/=pp.sum(); loss-=math.log(pp[r['y']]); grad[ix]+=pp;grad[ix[r['y']]]-=1
        return loss,grad
      fit=minimize(obj,np.zeros(3),jac=True,method='L-BFGS-B',options={'ftol':1e-12,'gtol':1e-8,'maxiter':500})
      if not fit.success and np.linalg.norm(fit.jac)>1e-4: raise RuntimeError(f'{league}: {fit.message}, gradient={np.linalg.norm(fit.jac)}')
      offsets[league]=fit.x.tolist()
    return offsets

def corrected(p,offset):
    ix=[0,2] if len(p)==2 else [0,1,2]; z=np.log(p)+np.asarray(offset)[ix];z-=z.max();q=np.exp(z);return q/q.sum()

def get_predictions(rows):
    bases={m:{r['id']:probabilities(r['odds'],m) for r in rows} for m in METHODS}
    coeff={m:fit(rows,bases[m]) for m in METHODS}
    predictions={m:bases[m] for m in METHODS}
    for m in METHODS: predictions[m+'+league-home-draw']={r['id']:corrected(bases[m][r['id']],coeff[m][r['league']]) for r in rows}
    return predictions,coeff

def observations(rows,pred):
    result=[]
    for r in rows:
      p=pred[r['id']]; pick=int(np.argmax(p)); y=r['y']; hit=int(pick==y); ret=hit*r['odds'][pick]-1
      result.append((hit,ret,-math.log(p[y]),sum((float(v)-(i==y))**2 for i,v in enumerate(p))))
    return np.array(result)

def independent(rows,pred):
    # Deliberately separate settlement and metric path.
    hits=[]; returns=[]; logs=[]; briers=[]
    for r in rows:
      ps=[float(x) for x in pred[r['id']]]; choice=max(range(len(ps)),key=lambda j:ps[j]); won=choice==r['y']
      hits.append(int(won));returns.append((r['odds'][choice] if won else 0)-1);logs.append(-math.log(ps[r['y']]));briers.append(sum((p-int(i==r['y']))**2 for i,p in enumerate(ps)))
    return np.array(list(zip(hits,returns,logs,briers)))

def run():
    rows,counts,bounds=load();pred,coeff=get_predictions(rows)
    # Held-out label flips occur before the full training/prediction path.
    flipped=[dict(r,y=(r['y']+1)%len(r['odds'])) if r['split']==2 else dict(r) for r in rows]
    pred2,coeff2=get_predictions(flipped)
    assert coeff==coeff2 and all(np.array_equal(pred[k][r['id']],pred2[k][r['id']]) for k in NAMES for r in rows)
    valid=[r for r in rows if r['split']==1]; test=[r for r in rows if r['split']==2]
    chosen=min(NAMES,key=lambda name:observations(valid,pred[name])[:,2].mean()); obs={k:observations(test,pred[k]) for k in NAMES}
    for k in NAMES: assert np.array_equal(obs[k],independent(test,pred[k]))
    weeks=collections.defaultdict(list)
    for i,r in enumerate(test):
      d=dt.datetime.fromisoformat(r['kickoff']); weeks[str(d.isocalendar()[:2])].append(i)
    wk=list(weeks.values()); rng=np.random.default_rng(20261030); bootstrap=rng.integers(0,len(wk),size=(2000,len(wk)))
    results={}; rawp=[]
    for k in NAMES:
      delta=obs[k]-obs['proportional']; means=[]
      for sample in bootstrap:
        idx=np.concatenate([wk[j] for j in sample]);means.append(delta[idx,:2].mean(axis=0))
      a=np.array(means); p=min(1.0,2*min((a[:,0]<=0).mean(),(a[:,0]>=0).mean()));rawp.append(p)
      results[k]={'n':len(test),'coverage':1.0,'accuracy':float(obs[k][:,0].mean()),'roi':float(obs[k][:,1].mean()),'logloss':float(obs[k][:,2].mean()),'brier':float(obs[k][:,3].mean()),'accuracy_delta':float(delta[:,0].mean()),'roi_delta':float(delta[:,1].mean()),'accuracy_ci':np.quantile(a[:,0],[.025,.975]).tolist(),'roi_ci':np.quantile(a[:,1],[.025,.975]).tolist(),'p_bootstrap_uncorrected':p,'changed_picks':int(sum(np.argmax(pred[k][r['id']])!=np.argmax(pred['proportional'][r['id']]) for r in test))}
    order=sorted(range(len(NAMES)),key=lambda i:rawp[i]); adj=0
    for rank,i in enumerate(order):
      adj=max(adj,min(1,(len(NAMES)-rank)*rawp[i]));results[NAMES[i]]['p_holm']=adj
    calibration=collections.defaultdict(lambda:[0,0.,0.])
    for r in test:
      p=pred[chosen][r['id']]
      for i,v in enumerate(p):
        for dimension,group in [('league',r['league']),('odds',str(int(r['odds'][i]))),('side',('home','draw','away')[i] if len(p)==3 else ('home','away')[i]),('way',str(len(p)))]:
          key=(dimension,group,str(min(9,int(v*10))));z=calibration[key];z[0]+=1;z[1]+=float(v);z[2]+=int(i==r['y'])
    report={'counts':counts,'split_boundaries':bounds,'split_counts':[sum(r['split']==i for r in rows) for i in range(3)],'selected_by_validation_logloss':chosen,'validation_logloss':{k:float(observations(valid,pred[k])[:,2].mean()) for k in NAMES},'test':results,'coefficients':coeff,'weeks':len(wk),'calibration':[{'dimension':k[0],'group':k[1],'probability_bin':k[2],'n':v[0],'predicted':v[1]/v[0],'observed':v[2]/v[0]} for k,v in sorted(calibration.items())],'verification':'heldout labels flipped; coefficients and all predictions identical; independent settlement and all per-row metrics exactly equal','source_sha256':hashlib.sha256((ROOT/'inputs/events.jsonl.gz').read_bytes()).hexdigest()}
    (ROOT/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n');print(json.dumps({'counts':counts,'split':report['split_counts'],'selected':chosen,'test':results[chosen]},ensure_ascii=False))
if __name__=='__main__': run()
