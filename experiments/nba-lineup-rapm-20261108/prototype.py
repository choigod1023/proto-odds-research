import os
for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'): os.environ[k]='1'
import argparse, collections, csv, gzip, hashlib, io, itertools, json, math, re, tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np, pandas as pd
from scipy.sparse import csr_matrix, diags
from scipy.sparse.linalg import lsqr
from sklearn.linear_model import LogisticRegression

HERE=Path(__file__).resolve().parent
PBP_HASH='a5d1bed30778430816b755676649cb2e3690ea62df5d96f4d9d6e9a8b8839339'
LINEUP_HASH='f69e2fc10285738e35484152f0a1a562a4b9605d9ea55f1d087de582326acf27'

def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def players(s):
 return tuple(sorted(re.findall(r'(?:^|, )(\d+) ',s)))
def read_pbp(path):
 games={}
 with tarfile.open(path,'r:xz') as t:
  with io.TextIOWrapper(t.extractfile('cdnnba_2025.csv'),encoding='utf8',newline='') as f:
   for x in csv.DictReader(f):
    g=x['gameId']; tm=x['timeActual']
    if g not in games: games[g]=[tm,tm,None,None]
    if tm and (not games[g][0] or tm<games[g][0]): games[g][0]=tm
    if tm and tm>games[g][1]: games[g][1]=tm
    if x['scoreHome'].isdigit() and x['scoreAway'].isdigit(): games[g][2:]=[int(x['scoreHome']),int(x['scoreAway'])]
 return games

def build(pbp,lineup):
 if sha(pbp)!=PBP_HASH or sha(lineup)!=LINEUP_HASH: raise ValueError('source hash mismatch')
 src=read_pbp(pbp); df=pd.read_parquet(lineup); games=[]; excluded=collections.Counter(); badsecs=0; allsecs=0
 for gid,g in df.groupby('game_id',sort=True):
  home=g[g.location_team=='home'].sort_values(['secs_game_start','period','stint']); away=g[g.location_team=='away'].sort_values(['secs_game_start','period','stint'])
  if str(int(gid)) not in src: excluded['missing_pbp']+=1; continue
  a=src[str(int(gid))]; st=[]; reasons=[]
  if len(home)!=len(away): reasons.append('unpaired')
  for h,v in zip(home.itertuples(),away.itertuples()):
   s=float(h.secs_played); allsecs+=max(s,0)
   hp,ap=players(h.lineup_team),players(h.lineup_opp)
   valid=(len(hp)==5 and len(set(hp))==5 and len(ap)==5 and len(set(ap))==5 and not set(hp)&set(ap) and hp==players(v.lineup_opp) and ap==players(v.lineup_team) and h.team==v.opp and h.opp==v.team and h.period==v.period and h.stint==v.stint and h.secs_game_start==v.secs_game_start and h.secs_game_end==v.secs_game_end and h.secs_played==v.secs_played and h.pts_team==v.pts_opp and h.pts_opp==v.pts_team and s>=0 and abs((h.secs_game_end-h.secs_game_start)-s)<1e-7)
   if not valid: badsecs+=max(s,0); reasons.append('invalid_stint'); continue
   if s>0: st.append({'start':float(h.secs_game_start),'end':float(h.secs_game_end),'secs':s,'h':hp,'a':ap,'margin':float(h.pts_team-h.pts_opp)})
  if not st: reasons.append('no_stint')
  else:
   if abs(st[0]['start'])>1e-6 or any(abs(st[i]['end']-st[i+1]['start'])>1e-6 for i in range(len(st)-1)): reasons.append('gap_or_overlap')
   if abs(st[-1]['end']-(2880+300*max(0,int(home.period.max())-4)))>1e-6: reasons.append('wrong_duration')
   if [int(home.pts_team.sum()),int(home.pts_opp.sum())]!=a[2:]: reasons.append('score_mismatch')
  if reasons:
   for reason in set(reasons): excluded[reason]+=1
   continue
  games.append({'id':gid,'start':a[0],'end':a[1],'home':str(home.iloc[0].team),'away':str(home.iloc[0].opp),'score':a[2:],'stints':st})
 games.sort(key=lambda x:(x['start'],x['id']))
 audit={'source_games':len(df.game_id.unique()),'validated_games':len(games),'excluded_games_by_reason':dict(excluded),'invalid_stint_seconds':badsecs,'all_home_stint_seconds':allsecs,'invalid_stint_exposure_fraction':badsecs/allsecs if allsecs else None}
 with gzip.GzipFile(filename='',mode='wb',fileobj=open(HERE/'stints.jsonl.gz','wb'),mtime=0) as f:
  for g in games: f.write((json.dumps(g,separators=(',',':'))+'\n').encode())
 (HERE/'coverage.json').write_text(json.dumps(audit,indent=2),encoding='utf8'); print('coverage',audit,flush=True)
 return games

def feat(hteam,ateam,h,a,kind):
 d={'home':1,'t:'+hteam:1,'t:'+ateam:-1}
 if kind!='team':
  for p,w in h.items(): d['p:'+p]=d.get('p:'+p,0)+w
  for p,w in a.items(): d['p:'+p]=d.get('p:'+p,0)-w
 return d

def add_pairs(d,h,a):
 for lineups,sign in ((h,1),(a,-1)):
  for lineup,w in lineups.items():
   for p,q in itertools.combinations(lineup,2):
    k='i:'+p+':'+q; d[k]=d.get(k,0)+sign*w*0.31622776601683794
 return d

def matrix(dicts, index=None):
 if index is None:
  keys=sorted({k for d in dicts for k in d}); index={k:i for i,k in enumerate(keys)}
 rr=[];cc=[];vv=[]
 for i,d in enumerate(dicts):
  for k,v in d.items():
   if k in index and v: rr.append(i);cc.append(index[k]);vv.append(v)
 return csr_matrix((vv,(rr,cc)),shape=(len(dicts),len(index))),index

def train(games,kind):
 ds=[]; ys=[]; weights=[]
 for g in games:
  for s in g['stints']:
   h={x:1. for x in s['h']}; a={x:1. for x in s['a']}
   d=feat(g['home'],g['away'],h,a,kind)
   if kind=='pair': add_pairs(d,{tuple(s['h']):1.},{tuple(s['a']):1.})
   ds.append(d);ys.append(s['margin']*2880/s['secs']);weights.append(s['secs']/2880)
 X,index=matrix(ds); weights=np.sqrt(weights); X=diags(weights)@X; y=np.array(ys)*weights
 # Shared ridge penalty 20; pair features scaled sqrt(0.1), imposing 10x penalty.
 co=lsqr(X,y,damp=math.sqrt(20),atol=1e-7,btol=1e-7,iter_lim=1000)[0]
 return index,co

def recent(games,target,team):
 start=datetime.fromisoformat(target['start'].replace('Z','+00:00')); cutoff=start-timedelta(days=4,minutes=30)
 eligible=[g for g in games if team in (g['home'],g['away']) and datetime.fromisoformat(g['end'].replace('Z','+00:00'))<=cutoff]
 chosen=eligible[-5:]
 if len(chosen)<5:return None
 vals=collections.Counter(); total=0
 for g in chosen:
  side='h' if team==g['home'] else 'a'
  for s in g['stints']: vals[tuple(s[side])]+=s['secs'];total+=s['secs']
 return {k:v/total for k,v in vals.items()}

def forecast(games,g,kind,model):
 hh=recent(games,g,g['home']);aa=recent(games,g,g['away'])
 if hh is None or aa is None:return None
 def member(ll):
  d=collections.Counter()
  for players,w in ll.items():
   for p in players:d[p]+=w
  return d
 d=feat(g['home'],g['away'],member(hh),member(aa),kind)
 if kind=='pair':add_pairs(d,hh,aa)
 X,_=matrix([d],model[0]);return float((X@model[1])[0])

def label_available(game, boundary):
 return datetime.fromisoformat(game['end'].replace('Z','+00:00')) + timedelta(days=4) <= datetime.fromisoformat(boundary.replace('Z','+00:00'))

def evaluate(games):
 raw_train=[g for g in games if g['start']<'2026-01-15T00:00:00Z']
 train_games=[g for g in raw_train if label_available(g,'2026-01-15T00:00:00Z')]
 val=[g for g in games if '2026-01-15T00:00:00Z'<=g['start']<'2026-02-15T00:00:00Z']
 test=[g for g in games if g['start']>='2026-02-15T00:00:00Z']
 kinds=['team','player','pair']; models={k:train(train_games,k) for k in kinds}; print('models trained',len(train_games),flush=True)
 forecasts=[]
 for g in val+test:
  scores={k:forecast(games,g,k,models[k]) for k in kinds}
  if any(v is None for v in scores.values()):continue
  forecasts.append({'id':g['id'],'start':g['start'],'home':g['home'],'away':g['away'],'score':g['score'],'margin':g['score'][0]-g['score'][1],'split':'validation' if g in val else 'test','predicted_margin':scores})
 valid_ids={g['id'] for g in val if label_available(g,'2026-02-15T00:00:00Z')}
 validation=[r for r in forecasts if r['split']=='validation' and r['id'] in valid_ids]; testing=[r for r in forecasts if r['split']=='test']; results={}
 for k in kinds:
  x=np.array([[r['predicted_margin'][k]] for r in validation]); y=np.array([r['margin']>0 for r in validation],dtype=int)
  cal=LogisticRegression(C=1,solver='lbfgs',random_state=20261108,n_jobs=1).fit(x,y)
  for r in forecasts:r.setdefault('home_win_probability',{})[k]=float(cal.predict_proba([[r['predicted_margin'][k]]])[0,1])
  p=np.array([r['home_win_probability'][k] for r in testing]); actual=np.array([r['margin']>0 for r in testing],dtype=int); pred=np.array([r['predicted_margin'][k] for r in testing]); truth=np.array([r['margin'] for r in testing]);
  results[k]={'rmse':float(np.sqrt(np.mean((pred-truth)**2))),'accuracy':float(np.mean((p>=.5)==actual)),'log_loss':float(-np.mean(actual*np.log(p)+(1-actual)*np.log(1-p))),'brier':float(np.mean((p-actual)**2)),'calibration_intercept':float(cal.intercept_[0]),'calibration_slope':float(cal.coef_[0,0])}
 summary={'train_games':len(train_games),'train_purged_for_4day_label_lag':len(raw_train)-len(train_games),'validation_total':len(val),'validation_scored':len(validation),'validation_purged_for_4day_label_lag':len(val)-len(valid_ids),'test_total':len(test),'test_scored':len(testing),'unscored_reason':'fewer than five validated prior team games ending at least 4 days before start','models':results,'price_matched_n':0,'roi':None,'mdd':None,'seed':20261108,'training_cutoff_utc':'2026-01-15T00:00:00Z','test_start_utc':'2026-02-15T00:00:00Z','ridge_alpha':20,'pair_penalty_multiplier':10,'prior_games':5,'minimum_prior_age_days':4,'first_event_to_pregame_buffer_minutes':30}
 with gzip.GzipFile(filename='',mode='wb',fileobj=open(HERE/'predictions.jsonl.gz','wb'),mtime=0) as f:
  for r in forecasts:f.write((json.dumps(r,sort_keys=True)+'\n').encode())
 (HERE/'prototype-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8');print(json.dumps(summary,indent=2),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--pbp',required=True);p.add_argument('--lineup',required=True);a=p.parse_args(); evaluate(build(a.pbp,a.lineup))
