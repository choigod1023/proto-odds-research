import os
for k in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'):os.environ[k]='1'
import collections,gzip,hashlib,json,sys
from pathlib import Path
from datetime import timedelta
import numpy as np,pandas as pd
from scipy.optimize import minimize_scalar
from scipy.special import expit,logit
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
MAP={'Argentina':'아르헨','Brazil':'브라질','Bulgaria':'불가리','Canada':'캐나다','China':'중국','Cuba':'쿠바','France':'프랑스','Germany':'독일','Iran':'이란','Italy':'이탈리','Japan':'일본','Netherlands':'네덜란','Poland':'폴란드','Serbia':'세르비','Slovenia':'슬로베','Türkiye':'튀르키','USA':'미국','Ukraine':'우크라','Belgium':'벨기에','Czechia':'체코','Dominican Republic':'도미공','Korea':'한국','Thailand':'태국'}
def date_value(x):
 s=str(x).strip();return (pd.Timestamp('1899-12-30')+pd.Timedelta(days=int(float(s)))) if s.replace('.','',1).isdigit() else pd.Timestamp(s)
def load_games(cache):
 matches=[];audit=collections.Counter()
 for year in (2025,2026):
  for sex in ('Men','Women'):
   d=pd.read_csv(Path(cache)/f'VNL_{sex}_Team_{year}.csv');suffix='M' if sex=='Men' else 'W';
   for num,g in d.groupby('Match',sort=True):
    allrows=[]
    for team,t in g.groupby('Team',sort=True):
     t=t.copy();allrow=t[t['SET'].astype(str)=='ALL']
     if len(allrow)==0 and year==2026 and sex=='Men':
      allrow=t.iloc[:1];part=t.iloc[1:];a=allrow.iloc[0]
      if int(a['RECEPTION_Attempts'])!=int(part['RECEPTION_Attempts'].sum()) or int(a['RECEPTION_Successful'])!=int(part['RECEPTION_Successful'].sum()):audit['shifted_total_disagreement']+=1;continue
     if len(allrow)!=1:audit['bad_total_row']+=1;continue
     if not (year==2026 and sex=='Men'):
      part=t[t['SET'].astype(str)!='ALL']; total=allrow.iloc[0]
      if any(int(total[c])!=int(part[c].sum()) for c in ('RECEPTION_Attempts','RECEPTION_Successful')):audit['total_disagreement']+=1;continue
     row=allrow.iloc[0];name=MAP.get(team);opp=MAP.get(row['VS_Team']);
     if not name or not opp:audit['unknown_team']+=1;continue
     allrows.append({'team':name+suffix,'opponent':opp+suffix,'win':int(row['SET_Won']),'loss':int(row['SET_Lost']),'success':int(row['RECEPTION_Successful']),'attempts':int(row['RECEPTION_Attempts']),'day':date_value(row['Y/M/D']).normalize()})
    if len(allrows)!=2:audit['invalid_team_pairs']+=1;continue
    a,b=allrows
    if not (a['team']==b['opponent'] and b['team']==a['opponent'] and a['win']==b['loss'] and a['loss']==b['win'] and max(a['win'],b['win'])==3 and a['day']==b['day'] and all(0<=x['success']<=x['attempts'] and x['attempts']>0 for x in allrows)):
     audit['invalid_score_or_stats']+=1;continue
    matches.append({'day':a['day'],'teams':{a['team']:a,b['team']:b},'league':'남배네이' if sex=='Men' else '여배네이','match_id':f'{year}-{sex}-{num}'});audit[f'games_{year}_{sex}']+=1
 return matches,dict(audit)
def make_features(matches,prices):
 index=collections.defaultdict(list)
 for game in matches:index[(game['league'],game['day'].date().isoformat(),frozenset(game['teams']))].append(game)
 out=[];audit=collections.Counter()
 for p in prices:
  date=pd.Timestamp(p['day']);key=(p['league'],p['day'],frozenset([p['home'],p['away']])); found=index.get(key,[]);audit[p['league']+'/'+p['day'][:4]+'/prices']+=1
  if len(found)!=1:audit[p['league']+'/'+p['day'][:4]+'/unmatched_or_ambiguous']+=1;continue
  game=found[0];h=game['teams'][p['home']];a=game['teams'][p['away']]
  if (h['win']>h['loss'])!=(p['y']==0):audit[p['league']+'/'+p['day'][:4]+'/outcome_conflict']+=1;continue
  stats=[]
  for team in (p['home'],p['away']):
   prior=[g['teams'][team] for g in matches if g['league']==p['league'] and team in g['teams'] and g['day']<=date-pd.Timedelta(days=4)]
   if len(prior)<5:break
   success=sum(v['success'] for v in prior);attempts=sum(v['attempts'] for v in prior);stats.append((success+50)/(attempts+100))
  if len(stats)!=2:audit[p['league']+'/'+p['day'][:4]+'/insufficient_prior']+=1;continue
  inv=[1/o for o in p['odds']];out.append({'id':p['id'],'day':p['day'],'league':p['league'],'odds':p['odds'],'y':int(p['y']==0),'market':inv[0]/sum(inv),'feature':stats[0]-stats[1],'match_id':game['match_id']});audit[p['league']+'/'+p['day'][:4]+'/eligible']+=1
 return out,dict(audit)
def fit(rows):
 x=np.array([r['feature'] for r in rows]);z=logit([r['market'] for r in rows]);y=np.array([r['y'] for r in rows]);f=lambda b:float(np.mean(np.logaddexp(0,z+b*x)-y*(z+b*x))+b*b)
 return float(minimize_scalar(f,bounds=(-2,2),method='bounded').x)
def metrics(rows,p):
 p=np.clip(np.array(p),1e-10,1-1e-10);y=np.array([r['y'] for r in rows]);pick=p>=.5;o=np.array([r['odds'][0 if v else 1] for r,v in zip(rows,pick)]);hit=pick==y;net=o*hit-1;w=np.r_[0,net.cumsum()];return {'n':len(y),'accuracy':float(hit.mean()),'roi':float(net.mean()),'mean_odds':float(o.mean()),'log_loss':float(-np.mean(y*np.log(p)+(1-y)*np.log1p(-p))),'brier':float(np.mean((y-p)**2)),'mdd':float((np.maximum.accumulate(w)-w).max())}
def run(cache):
 matches,audit=load_games(cache);source=ROOT/'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz';prices=[json.loads(line) for line in gzip.open(source,'rt',encoding='utf8')];prices=[p for p in prices if p['league'] in ('남배네이','여배네이') and p['day'][:4] in ('2025','2026')];data,joins=make_features(matches,prices);result={'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'source_files':json.loads((Path(cache)/'manifest.json').read_text(encoding='utf8')),'source_audit':audit,'joins':joins,'groups':{},'scope':'third party VNL retrospective, not KOVO or pregame certified'};outputs=[]
 for league in ('남배네이','여배네이'):
  a=sorted([r for r in data if r['league']==league],key=lambda r:(r['day'],r['id']));year25=[r for r in a if r['day'].startswith('2025')]; cut=int(len(year25)*.8);boundary=year25[cut-1]['day'] if cut else '';train=[r for r in year25 if r['day']<=boundary];valid=[r for r in year25 if r['day']>boundary];test=[r for r in a if r['day'].startswith('2026')]
  if len(train)<20 or not valid or not test:result['groups'][league]={'train':len(train),'validation':len(valid),'test':len(test),'reason':'insufficient_data'};continue
  validation_beta=fit(train);validation_market=np.array([r['market'] for r in valid]);validation_p=expit(logit(validation_market)+validation_beta*np.array([r['feature'] for r in valid]));beta=fit(year25);m=np.array([r['market'] for r in test]);p=expit(logit(m)+beta*np.array([r['feature'] for r in test]));days=sorted(set(r['day'] for r in test));rng=np.random.default_rng(20261108);diff={k:[] for k in ('accuracy','roi','log_loss','brier')}
  for _ in range(2000):
   sample=rng.choice(days,len(days),replace=True);ii=[i for day in sample for i,r in enumerate(test) if r['day']==day];rr=[test[i] for i in ii];base=metrics(rr,m[ii]);cand=metrics(rr,p[ii]);
   for k in diff:diff[k].append(cand[k]-base[k])
  group={'train':len(train),'validation':len(valid),'test':len(test),'all_price_rows':joins[league+'/2026/prices'],'coverage':len(test)/joins[league+'/2026/prices'],'beta':beta,'validation_market':metrics(valid,validation_market),'validation_candidate':metrics(valid,validation_p),'market':metrics(test,m),'candidate':metrics(test,p),'changed_picks':int(sum((m>=.5)!=(p>=.5))),'paired_95':{k:list(map(float,np.quantile(v,[.025,.975]))) for k,v in diff.items()},'monthly':{month:{'market':metrics([r for r in test if r['day'].startswith(month)],m[[i for i,r in enumerate(test) if r['day'].startswith(month)]]),'candidate':metrics([r for r in test if r['day'].startswith(month)],p[[i for i,r in enumerate(test) if r['day'].startswith(month)]])} for month in sorted(set(r['day'][:7] for r in test))}}
  result['groups'][league]=group
  for r,prob in zip(test,p):outputs.append(r|{'candidate':float(prob)})
 (HERE/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
 with (HERE/'predictions.jsonl.gz').open('wb') as f:
  with gzip.GzipFile(filename='',mode='wb',fileobj=f,mtime=0) as g:
   for row in outputs:g.write((json.dumps(row,ensure_ascii=False,sort_keys=True)+'\n').encode())
if __name__=='__main__':run(sys.argv[1])
