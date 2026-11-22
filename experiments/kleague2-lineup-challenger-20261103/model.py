"""Retrospective K2 independent-Poisson ablation. Not production code."""
import collections,datetime,gzip,hashlib,json,math
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln
from scipy.stats import skellam
ROOT=Path(__file__).resolve().parent
INPUT=ROOT/'inputs'
def sha(b): return hashlib.sha256(b).hexdigest()
def iso_day(s):return datetime.date.fromisoformat(s[:10])
def load_games():
 protocol=json.loads((ROOT/'protocol.json').read_text(encoding='utf-8-sig'))
 for name,expected in protocol['input_sha256'].items():
  if sha((INPUT/name).read_bytes())!=expected:raise ValueError('input hash mismatch '+name)
 entries=[json.loads(line) for line in (INPUT/'lineups.jsonl').read_bytes().splitlines()]
 census=[]
 for year in (2025,2026):
  data=json.loads((INPUT/f'census-{year}.json').read_bytes())['fixtures'];census.extend(data)
 index={}
 for r in census:
  key=f"kleague:{r['year']}:{r['meetSeq']}:{r['gameId']}"
  if key in index:raise ValueError('duplicate fixture in census')
  index[key]=r
 seen=set(); games=[];excluded=collections.Counter()
 for e in entries:
  key=e['fixture_id']
  if key in seen:raise ValueError('duplicate lineup fixture')
  seen.add(key);r=index.get(key)
  if r is None:raise ValueError('missing schedule fixture')
  if r['leagueId']!=2 or r['meetSeq']!=2:raise ValueError('wrong league')
  if r['codeName']!='\uc77c\ubc18':excluded['non_regular_playoff']+=1;continue
  if r['gameStatus']!='FE' or r['endYn']!='Y':excluded['not_finished']+=1;continue
  sides=e['teams']; team_ids=[s['team_id'] for s in sides]
  if len(sides)!=2 or team_ids!=[r['homeTeam'],r['awayTeam']] or team_ids[0]==team_ids[1]:raise ValueError('team identity')
  if e['kickoff_kst']!=r['gameDate'].replace('.','-')+'T'+r['gameTime']+':00+09:00':raise ValueError('kickoff mismatch')
  xi=[]
  for s in sides:
   ps=[v['player_id'] for v in s['starting_eleven']]
   if len(ps)!=11 or len(set(ps))!=11 or any(not isinstance(p,str) or len(p)!=8 or not p.isdigit() for p in ps):raise ValueError('invalid XI')
   xi.append(ps)
  score=e['official_result']
  if score!=[r['homeGoal'],r['awayGoal']] or len(score)!=2 or any(not isinstance(x,int) or x<0 for x in score):raise ValueError('result mismatch')
  games.append({'id':key,'kickoff':e['kickoff_kst'],'date':iso_day(e['kickoff_kst']),'teams':team_ids,'xi':xi,'score':score})
 return sorted(games,key=lambda x:(x['date'],x['kickoff'],x['id'])),dict(excluded)

def history_features(games):
 """Only completed XI from strictly earlier calendar dates; never reads any score."""
 hist=collections.defaultdict(list); output={}
 for day, rows_iter in __import__('itertools').groupby(sorted(games,key=lambda x:(x['date'],x['id'])),key=lambda x:x['date']):
  rows=list(rows_iter)
  for g in rows:
   features=[]
   for team,xi in zip(g['teams'],g['xi']):
    prior=hist[team]; current=set(xi);recent=prior[-3:]
    continuity=len(current.intersection(prior[-1]))/11 if prior else 0.0
    recent_starts=sum(len(current.intersection(earlier)) for earlier in recent)/11
    features.append([continuity,recent_starts,float(not prior),float(len(prior)<3)])
   output[g['id']]=features
  same_day=collections.Counter(team for g in rows for team in g['teams'])
  for g in rows:
   for team,xi in zip(g['teams'],g['xi']):
    if same_day[team]==1:hist[team].append(set(xi))
 return output

def extra(own,opp):return np.array([own[0],opp[0],own[1],opp[1],own[2],opp[2],own[3],opp[3]],dtype=float)
def design(games, feats, teams, candidate):
 n=len(games);size=2+2*len(teams)+(8 if candidate else 0);X=np.zeros((2*n,size));unknown=[]
 for i,g in enumerate(games):
  h,a=g['teams'];unknown.append([h not in teams,a not in teams])
  for j,(atk,opp) in enumerate(((h,a),(a,h))):
   row=X[2*i+j];row[0]=1;row[1]=1 if j==0 else 0
   if atk in teams:row[2+teams[atk]]=1
   if opp in teams:row[2+len(teams)+teams[opp]]=1
   if candidate:row[-8:]=extra(feats[g['id']][j],feats[g['id']][1-j])
 return X,unknown

def fit(games,feats,alpha,candidate,cutoff):
 teams={s:i for i,s in enumerate(sorted({t for g in games for t in g['teams']}))}
 X,_=design(games,feats,teams,candidate);y=np.array([v for g in games for v in g['score']],dtype=float)
 weights=np.repeat(np.array([math.exp(-math.log(2)*(cutoff-g['date']).days/180) for g in games]),2)
 penalty=np.ones(X.shape[1]);penalty[0]=0
 def obj(beta):
  z=X@beta
  if np.max(np.abs(z))>30:return 1e50,np.ones_like(beta)*1e20
  rates=np.exp(z);loss=np.dot(weights,rates-y*z+gammaln(y+1))+.5*alpha*np.dot(penalty*beta,beta)
  grad=X.T@(weights*(rates-y))+alpha*penalty*beta
  return float(loss),grad
 result=minimize(obj,np.zeros(X.shape[1]),jac=True,method='L-BFGS-B',options={'maxiter':3000,'ftol':1e-12,'gtol':1e-7})
 if not result.success:raise RuntimeError('optimizer failure: '+str(result.message))
 return {'coeff':result.x,'teams':teams,'alpha':alpha,'candidate':candidate,'optimizer_message':str(result.message)}

def predict(model,games,feats):
 X,unknown=design(games,feats,model['teams'],model['candidate']);rates=np.exp(X@model['coeff']).reshape((-1,2))
 p=np.stack([skellam.sf(0,rates[:,0],rates[:,1]),skellam.pmf(0,rates[:,0],rates[:,1]),skellam.cdf(-1,rates[:,0],rates[:,1])],axis=1)
 if not np.all(np.isfinite(p)) or np.max(np.abs(p.sum(axis=1)-1))>1e-10 or np.any(p<0):raise ValueError('invalid probabilities')
 return rates,p,unknown

def score_loss(games,rates):
 y=np.array([g['score'] for g in games]);return float(np.mean(np.sum(rates-y*np.log(rates)+gammaln(y+1),axis=1)))

def partition(games):
 return ([g for g in games if g['date'].year==2025], [g for g in games if datetime.date(2026,1,1)<=g['date']<datetime.date(2026,7,1)], [g for g in games if g['date']>=datetime.date(2026,7,1)])
