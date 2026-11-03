"""Fixed KBO player-role research: fit-only vocabulary and temporal features."""
import gzip,hashlib,importlib.util,json,math,sys
from pathlib import Path
from datetime import date,timedelta
import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp
from scipy.sparse import csr_matrix,hstack
from scipy.stats import skellam
P=Path(__file__).resolve().parent;I=P/'inputs';A=P/'artifacts'
def module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
adapter=module('_effects_original_adapter',P/'original_adapter.py')
WEIGHTS=np.array([4.7,4.6,4.5,4.4,4.3,4.2,4.1,4.0,3.9])/4.3
SETTINGS=('projected','official_batting_and_starter');PENALTIES=(1.,10.,100.)

def verify_inputs():
 manifest=json.loads((I/'manifest.json').read_text(encoding='utf8'))
 if hashlib.sha256((P/'contract.json').read_bytes()).hexdigest()!=manifest['contract_sha256'] or manifest['contract_sha256']!='6ffdf7947559df052c248a1f9fb5e7ad871321f52e7920126934bf21771465ed':raise ValueError('contract SHA mismatch')
 for name,sha in manifest['files'].items():
  path=P/name if name.startswith('original_') else I/name;raw=path.read_bytes()
  if name.startswith('original_'):raw=raw.replace(b'\r\n',b'\n')
  if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('input hash mismatch '+name)
 if hashlib.sha256(gzip.decompress((I/'rows.jsonl.gz').read_bytes())).hexdigest()!=manifest['raw_source_rows_sha256']:raise ValueError('rows hash mismatch')
 return manifest

def read_gz(name):return [json.loads(line) for line in gzip.decompress((I/name).read_bytes()).splitlines()]
def index(rows):
 d={x['fixture_id']:x for x in rows}
 if len(d)!=len(rows):raise ValueError('duplicate fixture')
 return d

def scalar_features(row):
 def val(name):
  x=row['features'].get(name);return None if x is None else x['value']
 def side(s):
  o='away' if s=='home' else 'home';return [val(s+'_team_strength'),val(o+'_team_strength'),val(s+'_rest_days'),val(o+'_rest_days'),val('park_factor'),val(s+'_lineup_strength'),val(o+'_starter_strength'),val(o+'_bullpen_load')]
 return [side('home'),side('away')]

def roster(src,scenario):
 out={}
 for side in ('home','away'):
  p=src['projection']
  if scenario=='official_batting_and_starter':
   line=src['official_lineup_context']['lineups'][side];players=[x['player_id'] for x in line]
   if len(players)!=9 or [x['order'] for x in line]!=list(range(1,10)) or len(set(players))!=9:raise ValueError('invalid official order')
   pitch=src['official_schedule_pitcher_context']['pitchers'][side]['player_id']
  else:players=p[side+'_lineup']['player_ids'];pitch=p[side+'_starter']['player_id']
  if players is not None and (len(players)!=9 or any(not x for x in players) or len(set(players))!=9):raise ValueError('invalid projected roster')
  out[side]={'hitters':players,'starter':pitch}
 return out

def load_data():
 verify_inputs();base=index(read_gz('features.jsonl.gz'));prior=index(read_gz('prior-projected-features.jsonl.gz'));raw=index(read_gz('rows.jsonl.gz'));labels=index(read_gz('development-labels.jsonl.gz'))
 if len(base)!=2913 or set(base)!=set(prior) or set(base)!=set(raw) or any(x['fixture_id'] not in base or base[x['fixture_id']]['season']==2026 for x in labels.values()):raise ValueError('baseline or development cohort mismatch')
 dates={fid:date.fromisoformat(x['event_date']) for fid,x in base.items()};training=[]
 for fid,b in base.items():
  src=prior[fid];adapter.check_nested(src['features'],fid,dates[fid],dates);adapter.check_nested(src['projection'],fid,dates[fid],dates)
  if src['scenario']!='projected_from_prior_games' or b['season']!=raw[fid]['season'] or b['home_id']!=raw[fid]['home_id'] or b['away_id']!=raw[fid]['away_id']:raise ValueError('source identity')
  for k,v in src['features'].items():
   actual=adapter.scalar(k,v['value']);expected=b['features'].get(k,{}).get('value')
   if actual!=expected:raise ValueError('original scalar mismatch')
  if b['season']<2026:
   if fid not in labels:raise ValueError('missing development label')
   training.append({'fixture_id':fid,'event_date':b['event_date'],'season':b['season'],'home_id':b['home_id'],'away_id':b['away_id'],'features':b['features'],'roster':roster(src,'projected')})
 predsets={}
 for scenario in SETTINGS:
  src=index(read_gz('fresh-history-2026-'+scenario+'.jsonl.gz'));out=[]
  if len(src)!=709:raise ValueError('fresh cohort mismatch')
  for fid,s in src.items():
   b=base[fid];day=dates[fid]
   if b['season']!=2026 or s['scenario']!='fresh_history_'+scenario:raise ValueError('fresh fixture mismatch')
   adapter.check_nested(s['features'],fid,day,dates);adapter.check_nested(s['projection'],fid,day,dates)
   features={}
   for k,v in s['features'].items():
    if not k.endswith(('lineup_strength','starter_strength','bullpen_load')) and v!=prior[fid]['features'].get(k):raise ValueError('nonplayer feature changed')
    features[k]={'value':adapter.scalar(k,v['value'])}
   if scenario=='official_batting_and_starter':
    for context in ('official_lineup_context','official_schedule_pitcher_context'):
     if any(s[context][k] is not False for k in ('historical_receipt_proven','eligible_as_pregame_feature','point_in_time_verified')):raise ValueError('prematch availability promoted')
   out.append({'fixture_id':fid,'event_date':b['event_date'],'season':2026,'home_id':b['home_id'],'away_id':b['away_id'],'features':features,'roster':roster(s,scenario)})
  predsets[scenario]=sorted(out,key=lambda x:(x['event_date'],x['fixture_id']))
 if [x['fixture_id'] for x in predsets['projected']]!=[x['fixture_id'] for x in predsets['official_batting_and_starter']]:raise ValueError('709 roster cohort mismatch')
 return sorted(training,key=lambda x:(x['event_date'],x['fixture_id'])),labels,predsets

def cut_training(rows,years,first_validation):
 cutoff=date.fromisoformat(first_validation)-timedelta(days=7)
 return [x for x in rows if x['season'] in years and date.fromisoformat(x['event_date'])<=cutoff]

def make_spec(train):
 nums=np.array([a for r in train for a in scalar_features(r)],dtype=float);mask=np.isfinite(nums);n=mask.sum(0);mean=np.divide(np.where(mask,nums,0).sum(0),n,out=np.zeros(8),where=n>0);var=np.divide(np.where(mask,(nums-mean)**2,0).sum(0),n,out=np.zeros(8),where=n>0);scale=np.sqrt(var);scale[scale<1e-12]=1.
 players=lambda role:sorted({p for r in train for side in ('home','away') for p in ((r['roster'][side]['hitters'] or []) if role=='hitter' else [r['roster'][side]['starter']]) if p is not None})
 return {'teams':sorted({r[k] for r in train for k in ('home_id','away_id')}),'hitters':players('hitter'),'starters':players('starter'),'mean':mean.tolist(),'scale':scale.tolist()}

def side_features(row,side,spec):
 own=row['roster'][side];opp=row['roster']['away' if side=='home' else 'home'];line=own['hitters'];starter=opp['starter'];h=set(spec['hitters']);s=set(spec['starters']);unknown=sum(x not in h for x in line)/9 if line else 0.
 return [float(line is None),unknown,float(starter is None),float(starter is not None and starter not in s)]

def design(rows,spec,family):
 teams=spec['teams'];hmap={x:i for i,x in enumerate(spec['hitters'])};smap={x:i for i,x in enumerate(spec['starters'])};nh=len(hmap);ns=len(smap);mean=np.array(spec['mean']);scale=np.array(spec['scale']);bases=[];ri=[];ci=[];vv=[]
 def add(idx,col,value):ri.append(idx);ci.append(col);vv.append(value)
 for j,row in enumerate(rows):
  num=np.array(scalar_features(row),dtype=float);missing=~np.isfinite(num);norm=(np.where(missing,mean,num)-mean)/scale;flags=np.array([side_features(row,side,spec) for side in ('home','away')]);
  if family in ('M0','M1'):
   for z,side in enumerate(('home','away')):
    opp='away' if side=='home' else 'home';own=row[side+'_id'];op=row[opp+'_id'];base=[1.,float(side=='home')]+[float(own==x) for x in teams]+[float(op==x) for x in teams]+norm[z].tolist()+missing[z].astype(float).tolist()
    if family=='M1':base+=flags[z].tolist()
    bases.append(base)
    if family=='M1':
     for pos,p in enumerate(row['roster'][side]['hitters'] or []):
      if p in hmap:add(2*j+z,hmap[p],WEIGHTS[pos])
     starter=row['roster'][opp]['starter']
     if starter in smap:add(2*j+z,nh+smap[starter],1.)
  else:
   bases.append([1.]+[float(row['home_id']==t)-float(row['away_id']==t) for t in teams]+(norm[0]-norm[1]).tolist()+(missing[0].astype(float)-missing[1].astype(float)).tolist()+(flags[0]-flags[1]).tolist())
   for side,sign in [('home',1.),('away',-1.)]:
    for pos,p in enumerate(row['roster'][side]['hitters'] or []):
     if p in hmap:add(j,hmap[p],sign*WEIGHTS[pos])
    starter=row['roster'][side]['starter']
    if starter in smap:add(j,nh+smap[starter],sign)
 base=csr_matrix(np.array(bases,dtype=float));nbase=base.shape[1]
 if family=='M0':return base,nbase
 sparse=csr_matrix((vv,(ri,ci)),shape=(len(bases),nh+ns));return hstack((base,sparse),format='csr'),nbase

def target(rows,labels,family):
 y=[]
 for r in rows:
  z=labels[r['fixture_id']];h=z['home_score'];a=z['away_score']
  if type(h) is not int or type(a) is not int or h<0 or a<0:raise ValueError('invalid score')
  y.extend((h,a) if family in ('M0','M1') else (0 if h>a else 1 if h==a else 2,))
 return np.array(y,dtype=float if family!='M2' else int)

def fit(rows,labels,family,player_l2):
 spec=make_spec(rows);X,nbase=design(rows,spec,family);y=target(rows,labels,family);d=X.shape[1];pen=np.r_[np.array([0.]+[10.]*(nbase-1)),np.full(d-nbase,player_l2)];nclass=2 if family=='M2' else 1
 def objective(flat):
  beta=flat.reshape(nclass,d)
  if family=='M2':
   logits=np.column_stack((X@beta[0],X@beta[1],np.zeros(len(y))));ls=logsumexp(logits,axis=1);prob=np.exp(logits-ls[:,None]);loss=float(np.sum(ls-logits[np.arange(len(y)),y]));grad=np.vstack([X.T@(prob[:,k]-(y==k)) for k in (0,1)])
  else:
   z=X@beta[0]
   if np.max(np.abs(z))>30:return 1e100,np.zeros_like(flat)
   mu=np.exp(z);loss=float(np.sum(mu-y*z));grad=np.asarray(X.T@(mu-y)).reshape(1,d)
  loss+=.5*float(np.sum(beta*beta*pen));grad+=beta*pen
  return loss,grad.ravel()
 init=np.zeros(nclass*d)
 if family!='M2':init[0]=math.log(max(float(y.mean()),.1))
 sol=minimize(objective,init,jac=True,method='L-BFGS-B',options={'maxiter':3000,'ftol':1e-11,'gtol':1e-6,'maxls':50})
 if not sol.success or not np.isfinite(sol.fun) or not np.isfinite(sol.x).all():raise ValueError('optimizer failure: '+str(sol.message))
 return {'family':family,'player_l2':player_l2,'spec':spec,'beta':sol.x.tolist(),'nbase':nbase,'objective':float(sol.fun),'iterations':int(sol.nit),'convergence':str(sol.message),'gradient_max':float(np.max(np.abs(sol.jac))),'n_train':len(rows)}

def predict(model,rows):
 family=model['family'];X,nbase=design(rows,model['spec'],family);d=X.shape[1];beta=np.array(model['beta']);z=X@beta.reshape(2,d).T if family=='M2' else X@beta
 if family=='M2':
  logits=np.column_stack((z,np.zeros(len(rows))));p=np.exp(logits-logsumexp(logits,axis=1)[:,None]);rates=None
 else:
  if np.any(np.abs(z)>20):raise ValueError('unstable rate')
  rates=np.exp(z).reshape(-1,2);h=rates[:,0];a=rates[:,1];p=np.column_stack((skellam.sf(0,h,a),skellam.pmf(0,h,a),skellam.cdf(-1,h,a)));p=p/p.sum(axis=1)[:,None]
 if not np.isfinite(p).all() or (p<0).any() or not np.allclose(p.sum(axis=1),1,atol=1e-12):raise ValueError('invalid probabilities')
 result=[]
 for i,r in enumerate(rows):
  denom=p[i,0]+p[i,2]
  if denom<=0:raise ValueError('no decisive probability')
  result.append({'fixture_id':r['fixture_id'],'event_date':r['event_date'],'probabilities_3way':{k:float(p[i,j]) for j,k in enumerate(('home','draw','away'))},'probabilities_2way':{'home':float(p[i,0]/denom),'away':float(p[i,2]/denom)},'home_rate':None if rates is None else float(rates[i,0]),'away_rate':None if rates is None else float(rates[i,1]),'roster_coverage':{side:side_features(r,side,model['spec']) for side in ('home','away')}})
 return result

def metrics(pred,labels):
 ll=brier=hits=0.
 for x in pred:
  y=labels[x['fixture_id']];t='home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw';p=x['probabilities_3way'];ll-=math.log(max(p[t],1e-15));brier+=sum((p[k]-(k==t))**2 for k in ('home','draw','away'));hits+=int(max(('home','draw','away'),key=p.get)==t)
 return {'n':len(pred),'hits':int(hits),'accuracy':hits/len(pred),'logloss':ll/len(pred),'brier':brier/len(pred)}

def dump(path,obj):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('x',encoding='utf8',newline='\n') as f:json.dump(obj,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);f.write('\n')
