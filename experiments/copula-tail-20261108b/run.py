import os
os.environ.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import json, gzip, hashlib, argparse
from pathlib import Path
from collections import defaultdict
from datetime import date
from decimal import Decimal
import numpy as np
from scipy.special import ndtr, ndtri, gammaln
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import poisson
from numpy.polynomial.legendre import leggauss

N=14
H,A=np.indices((N,N)); X,W=leggauss(16)
def base(lh,la,n=N):
 h,a=np.indices((n,n)); q=np.exp(h*np.log(lh)-lh-gammaln(h+1)+a*np.log(la)-la-gammaln(a+1));return q/q.sum()
def copula(lh,la,rho,n=N):
 u=poisson.cdf(np.arange(n-1),lh);v=poisson.cdf(np.arange(n-1),la)
 u=np.r_[0,u,1];v=np.r_[0,v,1]; z=ndtri(np.clip(u[1:-1],1e-15,1-1e-15))[:,None]; t=ndtri(np.clip(v[1:-1],1e-15,1-1e-15))[None,:]
 c=u[:,None]*v[None,:]; rr=rho*(X+1)/2
 integ=np.zeros_like(z+t)
 for r,w in zip(rr,W): integ+=w*np.exp(-(z*z-2*r*z*t+t*t)/(2*(1-r*r)))/np.sqrt(1-r*r)
 c[1:-1,1:-1]+=rho/(4*np.pi)*integ
 q=np.diff(np.diff(c,axis=0),axis=1); q=np.maximum(q,0);return q/q.sum()
def shock(lh,la,d,n=N):
 # A two-point common intensity multiplier, equally weighted; means remain unchanged.
 return .5*base(lh*(1-d),la*(1-d),n)+.5*base(lh*(1+d),la*(1+d),n)
def masks(fam,line,n=N):
 h,a=np.indices((n,n)); d=h-a if fam=='승무패' else h+line-a if fam=='핸디캡' else h+a-line
 return np.array([d<0,d>0]) if fam=='언더오버' else np.array([d>0,d==0,d<0])
def probs(q,m):return np.array([q[z].sum() for z in m])
def means(p):
 m=masks('승무패',None)
 def loss(x):return np.sum((probs(base(*np.exp(x)),m)-p)**2)
 r=minimize(loss,np.log([1.4,1.1]),bounds=[(-2,1.3)]*2,method='L-BFGS-B',options={'ftol':1e-11});return np.exp(r.x)
def day_available(history_day,prediction_day):return (prediction_day-history_day).days>=4
def fit_param(rows,kind):
 def loss(v):
  total=0
  for r in rows:
   h,a=r['score']; q=copula(*r['means'],v) if kind=='copula' else shock(*r['means'],v)
   total-=np.log(max(q[min(h,N-1),min(a,N-1)],1e-15))
  return total
 opt=minimize_scalar(loss,bounds=(-.65,.65) if kind=='copula' else (0,.7),method='bounded',options={'xatol':.0005});return float(opt.x)
def metric(rows):
 p=np.array([r['p'] for r in rows]);y=np.array([r['y'] for r in rows]);d=defaultdict(Decimal)
 for r in rows:d[r['day']]+=Decimal(r['net'])
 running=peak=dd=Decimal(0)
 for day in sorted(d):
  running+=d[day];peak=max(peak,running);dd=max(dd,peak-running)
 return {'n':len(rows),'hit':sum(r['hit'] for r in rows)/len(rows),'roi':float(sum((Decimal(r['net']) for r in rows),Decimal(0))/len(rows)),'logloss':float(-np.log(p[np.arange(len(y)),y]).mean()),'brier':float(((p-np.eye(p.shape[1])[y])**2).sum(axis=1).mean()),'mdd':float(dd)}
def compare(a,b):
 assert [(r['event_id'],r['product'],r['odds'],r['y']) for r in a]==[(r['event_id'],r['product'],r['odds'],r['y']) for r in b]
 weeks=defaultdict(list)
 for x,y in zip(a,b):weeks[date.fromisoformat(x['day']).isocalendar()[:2]].append((x,y))
 groups=list(weeks.values());rng=np.random.default_rng(20261108); samples=[]
 def dif(pairs):
  aa,bb=zip(*pairs);ma,mb=metric(aa),metric(bb);return [ma[k]-mb[k] for k in ('hit','roi','logloss','brier')]
 for _ in range(1000):
  idx=rng.integers(0,len(groups),len(groups));samples.append(dif([z for i in idx for z in groups[i]]))
 z=np.array(samples); obs=dif(list(zip(a,b))); out={}
 for j,k in enumerate(('hit','roi','logloss','brier')):
  out[k]={'difference':obs[j],'ci95':np.quantile(z[:,j],[.025,.975]).tolist(),'p_two_sided_bootstrap_sign':float(min(1,2*min(np.mean(z[:,j]<=0),np.mean(z[:,j]>=0))))}
 out['weeks']=len(groups);out['changed_picks']=sum(x['pick']!=y['pick'] for x,y in zip(a,b));out['directions']={f'{i}->{j}':sum(x['pick']==i and y['pick']==j for x,y in zip(a,b)) for i in range(len(a[0]['p'])) for j in range(len(a[0]['p'])) if i!=j};return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--out',required=True);args=ap.parse_args();raw=Path(args.input).read_bytes(); rows=json.loads(raw);out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
 grouped=defaultdict(dict)
 for r in rows:grouped[(r['date'],r['league'],r['home'],r['away'])][(r['model'],r['family'])]=r
 games=[]
 for key,g in sorted(grouped.items()):
  x=g[('market_devig','승무패')]; lm=means(np.array(x['market_devig']));games.append({'key':key,'day':key[0][:10],'score':x['score'],'means':lm,'g':g})
 train=[r for r in games if r['day']<'2025-01-01']; val=[r for r in games if '2025-01-01'<=r['day']<'2026-01-01'];ev=[r for r in games if '2026-01-01'<=r['day']<='2026-08-26']
 pars={k:fit_param(train,k) for k in ('copula','shock')}; print('training parameters',pars,flush=True)
 results=[]
 for game in games:
  if game not in val and game not in ev:continue
  key=game['key']; ident=hashlib.sha256('|'.join(key).encode()).hexdigest()[:20]; g=game['g'];lh,la=game['means']; q={'copula':copula(lh,la,pars['copula']),'shock':shock(lh,la,pars['shock'])}
  for fam in ('승무패','핸디캡','언더오버'):
   ref=g[('market_devig',fam)]; m=masks(fam,ref['line']);pred={k:probs(v,m).tolist() for k,v in q.items()}
   for name in ('market_devig','poisson_1x2','poisson_all','dc_all','kl_soft'):pred[name]=g[(name,fam)]['probabilities']
   pred['fixed_mix']=(.5*np.array(pred['poisson_1x2'])+.5*np.array(pred['copula'])).tolist()
   for name,p in pred.items():
    p=np.asarray(p);assert abs(sum(p)-1)<1e-8 and min(p)>=0
    pick=int(np.argmax(p));y=ref['outcome'];hit=pick==y;net=Decimal(str(ref['odds'][pick]))-1 if hit else Decimal(-1)
    results.append({'event_id':ident,'day':game['day'],'sport':'sc','league':key[1],'home':key[2],'away':key[3],'product':fam,'line':ref['line'],'y':y,'odds':ref['odds'],'market_p':ref['market_devig'],'model':name,'p':p.tolist(),'pick':pick,'hit':hit,'net':str(net)})
 with gzip.open(out/'predictions.jsonl.gz','wt',encoding='utf8',newline='\n') as f:
  for r in results:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n')
 summary={'input_sha256':hashlib.sha256(raw).hexdigest(),'split':{'training':len(train),'validation_2025':len(val),'evaluation_2026':len(ev),'evaluation_requested_end':'2026-08-26','actual_end':max(r['day'] for r in ev)},'parameters_train_only':pars,'availability':'retrospective day-only price, actual reception and purchasability unverified','metrics':{},'paired_week_bootstrap':{}}
 for period in ('validation','evaluation'):
  rr=[r for r in results if (r['day']<'2026-01-01')==(period=='validation')];summary['metrics'][period]={};summary['paired_week_bootstrap'][period]={}
  for fam in ('승무패','핸디캡','언더오버'):
   summary['metrics'][period][fam]={};summary['paired_week_bootstrap'][period][fam]={};subset=[r for r in rr if r['product']==fam]; models=sorted(set(r['model'] for r in subset)); by={name:[r for r in subset if r['model']==name] for name in models}
   for name,group in by.items():
    segments={'all':group}
    for r in group:
     for key in (r['league'],r['day'][:7],r['day'][:4]+'-Q'+str((int(r['day'][5:7])-1)//3+1)):segments.setdefault(key,[]).append(r)
    summary['metrics'][period][fam][name]={k:metric(v) for k,v in segments.items()}
   if period=='evaluation':
    for candidate in ('copula','shock'):
     for control in ('market_devig','poisson_1x2','fixed_mix','poisson_all','dc_all','kl_soft'):summary['paired_week_bootstrap'][period][fam][candidate+'-vs-'+control]=compare(by[candidate],by[control])
 (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
if __name__=='__main__':main()



