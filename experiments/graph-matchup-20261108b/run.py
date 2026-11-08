import os
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'): os.environ[k]='1'
import gzip,json,hashlib,collections,datetime,decimal,math
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit,logit
from scipy.sparse import coo_matrix
ROOT=Path(__file__).resolve().parents[2]; OUT=Path(__file__).resolve().parent
SPORTS=('bs','sc','bk','vl'); MODELS=('market','additive','laplacian','matchup')

def load():
 rows=[]; audit=collections.Counter(); hashes={}
 for sport in SPORTS:
  path=ROOT/f'experiments/all-sports-underdog-20261031/results/{sport}/domestic_archive/ledger.jsonl.gz'
  hashes[sport]=hashlib.sha256(path.read_bytes()).hexdigest(); seen={}; conflict=set()
  with gzip.open(path,'rt',encoding='utf8') as f:
   for line in f:
    r=json.loads(line); audit[sport+'_raw']+=1
    try:
     if len(r['odds']) not in (2,3) or len(r['odds'])!=len(r['market_p']) or r['y'] not in range(len(r['odds'])): raise ValueError()
     if any(not math.isfinite(float(x)) or float(x)<=1 for x in r['odds']): raise ValueError()
     if any(not math.isfinite(float(x)) or float(x)<=0 for x in r['market_p']): raise ValueError()
     if not r['home'] or not r['away'] or r['home']==r['away']: raise ValueError()
     datetime.date.fromisoformat(r['day'])
    except (KeyError,TypeError,ValueError): audit[sport+'_invalid']+=1; continue
    if len(r['odds'])==3 and r['y']==1: audit[sport+'_draw_excluded']+=1; continue
    idx=[0,2] if len(r['odds'])==3 else [0,1]
    r['original_y']=r['y']; r['y']=idx.index(r['y']); r['odds']=[r['odds'][i] for i in idx]; p=[r['market_p'][i] for i in idx]; r['market_p']=[x/sum(p) for x in p]; r['sport']=sport
    key=(r['day'],r['league'],r['home'],r['away'])
    if key in seen:
     conflict.add(key); audit[sport+'_duplicate_rows']+=1
    else: seen[key]=r
  audit[sport+'_duplicate_keys_removed']+=len(conflict)
  rows.extend(v for k,v in seen.items() if k not in conflict)
 return sorted(rows,key=lambda r:(r['day'],r['sport'],r['id'])),dict(audit),hashes

def available(rows, prediction_day):
 cutoff=datetime.date.fromisoformat(prediction_day)-datetime.timedelta(days=4)
 return [r for r in rows if datetime.date.fromisoformat(r['day'])<=cutoff]

def key(r,t): return (r['league'],r[t])
def pair(r): return (r['league'],r['home'],r['away'])
def fit(train,kind):
 teams={k:i for i,k in enumerate(sorted({key(r,t) for r in train for t in ('home','away')}))}; pairs={k:i for i,k in enumerate(sorted({pair(r) for r in train}))} if kind=='matchup' else {}
 n=len(teams); m=len(pairs); N=len(train); hi=np.array([teams[key(r,'home')] for r in train]); ai=np.array([teams[key(r,'away')] for r in train]); pi=np.array([pairs[pair(r)] for r in train]) if m else None
 y=np.array([1-r['y'] for r in train]); offset=logit(np.clip([r['market_p'][0] for r in train],1e-8,1-1e-8))
 edge=collections.Counter((min(h,a),max(h,a)) for h,a in zip(hi,ai)); eh=np.array([x[0] for x in edge]); ea=np.array([x[1] for x in edge]); ew=np.array(list(edge.values()),float); ew=ew/(ew+10)
 def obj(x):
  z=offset+x[0]+x[1:1+n][hi]-x[1:1+n][ai]
  if m: z+=x[1+n:][pi]
  d=expit(z)-y; loss=np.logaddexp(0,z).sum()-y@z; grad=np.zeros_like(x);grad[0]=d.sum();np.add.at(grad,1+hi,d);np.add.at(grad,1+ai,-d)
  loss+=5*np.dot(x[1:1+n],x[1:1+n]);grad[1:1+n]+=10*x[1:1+n]; loss+=5*x[0]**2;grad[0]+=10*x[0]
  if kind=='laplacian':
   diff=x[1+eh]-x[1+ea];loss+=5*np.dot(ew,diff*diff); np.add.at(grad,1+eh,10*ew*diff);np.add.at(grad,1+ea,-10*ew*diff)
  if m:
   np.add.at(grad,1+n+pi,d);loss+=20*np.dot(x[1+n:],x[1+n:]);grad[1+n:]+=40*x[1+n:]
  return loss,grad
 res=minimize(obj,np.zeros(1+n+m),jac=True,method='L-BFGS-B',options={'maxiter':350,'ftol':1e-10,'gtol':1e-6})
 if not res.success: raise RuntimeError(f'{kind}: {res.message}')
 return {'x':res.x,'teams':teams,'pairs':pairs,'n':n,'edges':edge,'train_n':N}

def predict(model,r):
 x=model['x']; teams=model['teams']; p0=r['market_p'][0]; z=logit(p0)+x[0]
 for t,sign in [('home',1),('away',-1)]:
  i=teams.get(key(r,t)); z+=sign*x[1+i] if i is not None else 0
 j=model['pairs'].get(pair(r)); z+=x[1+model['n']+j] if j is not None else 0
 return float(expit(z))

def score(rs):
 n=len(rs)
 if not n:return None
 nets=[decimal.Decimal(r['net']) for r in rs]; daily=collections.defaultdict(decimal.Decimal)
 for r,net in zip(rs,nets): daily[r['day']]+=net
 balance=decimal.Decimal(0);peak=decimal.Decimal(0);dd=decimal.Decimal(0)
 for d in sorted(daily): balance+=daily[d];peak=max(peak,balance);dd=max(dd,peak-balance)
 return {'n':n,'hits':sum(r['hit'] for r in rs),'hit_rate':sum(r['hit'] for r in rs)/n,'roi':float(sum(nets)/n),'log_loss':sum(-math.log(max(r['p'][r['y']],1e-15)) for r in rs)/n,'brier':sum(sum((p-(i==r['y']))**2 for i,p in enumerate(r['p'])) for r in rs)/n,'mdd_daily_units':float(dd)}

def run():
 rows,audit,hashes=load(); models={}; train_sizes={}; validation={}
 for sport in SPORTS:
  subset=[r for r in rows if r['sport']==sport]; old=available(subset,'2025-01-01'); val=[r for r in subset if '2025-01-01'<=r['day']<='2025-12-31']; validation[sport]={}
  for name in MODELS:
   model=None if name=='market' else fit(old,name); preds=[]
   for r in val:
    hp=r['market_p'][0] if model is None else predict(model,r); pick=0 if hp>=.5 else 1; hit=pick==r['y']; net=decimal.Decimal(str(r['odds'][pick]))-1 if hit else decimal.Decimal(-1)
    preds.append({'day':r['day'],'y':r['y'],'p':[hp,1-hp],'hit':hit,'net':str(net)})
   validation[sport][name]=score(preds)
  tr=[r for r in available(subset,'2026-01-01') if r['day']>='2023-01-01']; train_sizes[sport]=len(tr)
  for k in MODELS[1:]: models[sport,k]=fit(tr,k)
 evaluation=[r for r in rows if '2026-01-01'<=r['day']<='2026-08-26']; output=[]; uncertainty=collections.Counter(); strata=collections.defaultdict(list)
 for r in evaluation:
  base=models[r['sport'],'additive']; known=sum(key(r,t) in base['teams'] for t in ('home','away')); uncertainty[f"{r['sport']}_unknown_teams_{2-known}"]+=1
  graph=base['edges']; h=base['teams'].get(key(r,'home')); a=base['teams'].get(key(r,'away')); uncertainty[f"{r['sport']}_unseen_edge"]+=int(h is None or a is None or (min(h,a),max(h,a)) not in graph)
  edge_count=graph.get((min(h,a),max(h,a)),0) if h is not None and a is not None else 0
  band='unseen' if edge_count==0 else '1-5' if edge_count<=5 else '6-20' if edge_count<=20 else '21+'
  for name in MODELS:
   hp=r['market_p'][0] if name=='market' else predict(models[r['sport'],name],r); p=[hp,1-hp]; assert abs(sum(p)-1)<1e-12 and min(p)>0
   pick=0 if hp>=.5 else 1;hit=pick==r['y'];net=decimal.Decimal(str(r['odds'][pick]))-1 if hit else decimal.Decimal(-1)
   record={'event_id':r['id'],'day':r['day'],'sport':r['sport'],'league':r['league'],'home':r['home'],'away':r['away'],'y':r['y'],'original_y':r['original_y'],'odds':r['odds'],'market_p':r['market_p'],'model':name,'p':p,'pick':pick,'hit':hit,'net':str(net)};output.append(record);strata[(r['sport'],band,name)].append(record)
 with gzip.open(OUT/'predictions.jsonl.gz','wt',encoding='utf8',newline='\n') as f:
  for r in output:f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n')
 summary={'period':['2026-01-01','2026-08-26'],'primary_sports':['bs','bk','vl'],'soccer_status':'unsupported for primary 3-way evaluation; conditional results below are diagnostic only and not actionable','training_end':'2025-12-28','availability_assumption':'day+4 <= 2026-01-01; official availability unverified','source_sha256':hashes,'audit':audit,'training_n':train_sizes,'validation_2025':validation,'graph_coverage':dict(uncertainty),'models':{},'limitations':['conditional home-away probabilities exclude draws','day-only price availability unverified','no tipster/player inputs','reused historical data; not independent holdout','voids/missing absent from source cannot be quantified','league names may be contaminated','duplicate same day league home away removed including possible doubleheaders']}
 summary['edge_count_strata']={sport:{band:{name:score(strata[(sport,band,name)]) for name in MODELS} for band in ('unseen','1-5','6-20','21+')} for sport in SPORTS}
 for sport in SPORTS:
  summary['models'][sport]={}
  for name in MODELS:
   rs=[r for r in output if r['sport']==sport and r['model']==name]; groups={}
   for field,fn in [('league',lambda r:r['league']),('month',lambda r:r['day'][:7]),('quarter',lambda r:r['day'][:4]+'Q'+str((int(r['day'][5:7])-1)//3+1))]:
    vals=sorted({fn(r) for r in rs});groups[field]={v:score([r for r in rs if fn(r)==v]) for v in vals}
   summary['models'][sport][name]={'overall':score(rs),**groups}
  base=[r for r in output if r['sport']==sport and r['model']=='market']; weeks=sorted({datetime.date.fromisoformat(r['day']).isocalendar()[:2] for r in base}); rng=np.random.default_rng(20261108); sample=rng.integers(0,len(weeks),size=(1000,len(weeks))); wi={w:i for i,w in enumerate(weeks)}
  for name,reference in [(name,reference) for name in ('laplacian','matchup') for reference in ('market','additive')]+[('additive','market')]:
   base=[r for r in output if r['sport']==sport and r['model']==reference]
   rr=[r for r in output if r['sport']==sport and r['model']==name]; assert [(r['event_id'],r['odds'],r['y']) for r in rr]==[(r['event_id'],r['odds'],r['y']) for r in base]
   sums=np.zeros((len(weeks),3)); counts=np.zeros(len(weeks)); changed=collections.Counter()
   for a,b in zip(rr,base):
    i=wi[datetime.date.fromisoformat(a['day']).isocalendar()[:2]];counts[i]+=1;sums[i]+=[float(decimal.Decimal(a['net'])-decimal.Decimal(b['net'])),(-math.log(a['p'][a['y']])+math.log(b['p'][b['y']])),int(a['hit'])-int(b['hit'])]
    if a['pick']!=b['pick']:changed['home_to_away' if b['pick']==0 else 'away_to_home']+=1
   boot=sums[sample].sum(axis=1)/counts[sample].sum(axis=1)[:,None]; comparisons={}
   for j,metric in enumerate(('roi_difference','logloss_difference','hit_difference')):
    v=boot[:,j];comparisons[metric]={'estimate':float(sums[:,j].sum()/counts.sum()),'ci95':np.quantile(v,[.025,.975]).tolist(),'p_two_sided':float(min(1,2*min(np.mean(v<=0),np.mean(v>=0))))}
   summary['models'][sport][name]['paired_vs_'+reference]={'weekly_bootstrap_replicates':1000,'difference':'candidate minus '+reference+'; lower logloss better, higher ROI/hit better','p':'two-sided bootstrap sign tail; descriptive, not multiplicity adjusted','changed_pick_direction':dict(changed),'metrics':comparisons}
 (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
if __name__=='__main__':run()
