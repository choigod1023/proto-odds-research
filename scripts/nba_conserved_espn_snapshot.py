"""NBA historical research only; never writes production state."""
import os
for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'): os.environ[name]='1'
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone,date
from pathlib import Path
import argparse,gzip,hashlib,importlib.util,json,math
import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import expit,logit
ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'.local-test/nba-espn-history-20261108'
OUT=ROOT/'experiments/nba-espn-history-20261108'
PRICE=ROOT/'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz'
# Explicit archive aliases; values are read as Unicode, not terminal-encoded text.
ALIASES=dict(zip('ATL BOS BKN CHA CHI CLE DAL DEN DET GS HOU IND LAC LAL MEM MIA MIL MIN NO NY OKC ORL PHI PHX POR SAC SA TOR UTAH WSH'.split(), '애틀호크 보스셀틱 브루네츠 샬럿호네 시카불스 클리캐벌 댈러매버 덴버너게 디트피스 골든워리 휴스로케 인디페이 LA클리퍼 LA레이커 멤피그리 마이히트 밀워벅스 미네울브 뉴올펠리 뉴욕닉스 오클썬더 올랜매직 필라76s 피닉선즈 포틀트레 새크킹스 샌안스퍼 토론랩터 유타재즈 워싱위저'.split()))
ALIAS_INDEX=defaultdict(list)
for team,alias in ALIASES.items(): ALIAS_INDEX[alias].append(team)
def name(s):
 v=ALIAS_INDEX.get(s,[]); return v[0] if len(v)==1 else None

def read_events(cache=CACHE):
 events={}; duplicates=0; conflicting=set()
 for path in sorted((cache/'scoreboard').glob('*.json')):
  if path.name.endswith('.receipt.json'): continue
  for e in verified_json(path).get('events',[]):
   key=e['id']
   if key in events:
    duplicates+=1
    if events[key]!=e: conflicting.add(key)
   else: events[key]=e
 if conflicting: raise ValueError(f'conflicting scoreboard event IDs: {len(conflicting)}; collection must be reviewed')
 return events,duplicates,len(conflicting)

def verified_json(path):
 raw=path.read_bytes(); receipt=json.loads(path.with_suffix('.receipt.json').read_text(encoding='utf8'))
 if receipt['status']!=200 or hashlib.sha256(raw).hexdigest()!=receipt['sha256']:
  raise ValueError(f'Invalid cache receipt: {path}')
 return json.loads(raw)

def parse_game(e,cache=CACHE):
 if not e.get('status',{}).get('type',{}).get('completed'): return None
 path=cache/'summary'/(e['id']+'.json')
 summary=verified_json(path) if path.exists() else None
 try:
  cs=e['competitions'][0]['competitors']; teams={x['homeAway']:x for x in cs}; h,a=teams['home'],teams['away']
  ht,at=h['team']['abbreviation'],a['team']['abbreviation']
  if ht not in ALIASES or at not in ALIASES or ht==at: return None
  scores=[int(h['score']),int(a['score'])]
  if scores[0]==scores[1]: return None
  day=datetime.fromisoformat(e['date'].replace('Z','+00:00')).astimezone(timezone(timedelta(hours=9))).date().isoformat()
  stats=[]
  for x in (h,a):
   try:
    v={z['name']:z['displayValue'] for z in x['statistics']}
    s={'fga':int(v['fieldGoalsAttempted']),'fgm':int(v['fieldGoalsMade']),'three':int(v['threePointFieldGoalsMade']),'fta':int(v['freeThrowsAttempted'])}
    if not (0<=s['three']<=s['fgm']<=s['fga'] and s['fga']>0 and s['fta']>=0):raise ValueError('invalid shooting totals')
   except (KeyError,ValueError,TypeError):s={}
   stats.append(s)
  g={'id':e['id'],'day':day,'utc':e['date'],'teams':[ht,at],'scores':scores,'stats':stats}
  if summary is not None:
   try:
    if str(summary['header']['id'])!=str(e['id']):raise ValueError('summary event ID mismatch')
    summary_scores={c['team']['abbreviation']:int(c['score']) for c in summary['header']['competitions'][0]['competitors']}
    if [summary_scores[ht],summary_scores[at]]!=scores:raise ValueError('scoreboard/summary score conflict')
    box=summary['boxscore']['teams']; by={b['team']['abbreviation']:b for b in box}; extra=[]
    for team,s in zip((ht,at),stats):
     v={z['name']:z['displayValue'] for z in by[team]['statistics']}
     fgm,fga=map(int,v['fieldGoalsMade-fieldGoalsAttempted'].split('-'))
     three,_=map(int,v['threePointFieldGoalsMade-threePointFieldGoalsAttempted'].split('-'))
     _,fta=map(int,v['freeThrowsMade-freeThrowsAttempted'].split('-'))
     if (fgm,fga,three,fta)!=(s['fgm'],s['fga'],s['three'],s['fta']): raise ValueError('scoreboard/summary conflict')
     extra.append({'orb':int(v['offensiveRebounds']),'tov':int(v['totalTurnovers'])})
     if min(extra[-1].values())<0:raise ValueError('negative box totals')
    for s,x in zip(stats,extra):s.update(x)
   except (KeyError,ValueError,TypeError,IndexError):g['summary_invalid']=True
  return g
 except (KeyError,ValueError,TypeError,IndexError): return None

def features(game, histories, mode, with_sources=False):
 cutoff=date.fromisoformat(game['day'])-timedelta(days=4); vals=[]; source_ids=[]
 for team in game['teams']:
  recent=[g for g in histories[team] if date.fromisoformat(g['day'])<=cutoff][-10:]
  if len(recent)!=10: return None
  source_ids.append([g['id'] for g in recent])
  values=[]
  for g in recent:
   i=g['teams'].index(team); own,opp=g['stats'][i],g['stats'][1-i]
   if any(k not in s for s in (own,opp) for k in ('fga','fgm','three','fta')):return None
   if mode=='efficiency':
    if any(k not in s for s in (own,opp) for k in ('orb','tov')): return None
    p=lambda s:s['fga']-s['orb']+s['tov']+.44*s['fta']
    if p(own)<=0 or p(opp)<=0:return None
    v=100*g['scores'][i]/p(own)-100*g['scores'][1-i]/p(opp)
   else:
    if not own['fga'] or not opp['fga']:return None
    v=(own['fgm']+.5*own['three'])/own['fga']-(opp['fgm']+.5*opp['three'])/opp['fga']
   values.append(v)
  vals.append(sum(values)/10)
 return (vals[0]-vals[1],source_ids) if with_sources else vals[0]-vals[1]

def fit(rows):
 x=np.array([r['feature'] for r in rows]); y=np.array([r['home_win'] for r in rows]); q=np.array([r['market_home'] for r in rows]); mean=float(x.mean()); sd=float(x.std()) or 1.; z=(x-mean)/sd
 loss=lambda b:float(np.logaddexp(0,logit(q)+b*z).sum()-np.dot(y,logit(q)+b*z)+10*b*b/2)
 opt=minimize_scalar(loss,method='bounded',bounds=(-5,5),options={'xatol':1e-12})
 if not opt.success:raise RuntimeError('fit failed')
 return {'mean':mean,'sd':sd,'beta':float(opt.x),'n':len(rows)}
def calc(rows,model):
 profits=[]; wins=[]; probs=[]; odds=[]
 for r in rows:
  p=r[model]; side=0 if p>=.5 else 1; won=int(side==(0 if r['home_win'] else 1)); price=r['odds'][side]; profits.append(price*won-1);wins.append(won);probs.append(p);odds.append(price)
 if not rows:return None
 y=np.array([r['home_win'] for r in rows]); p=np.clip(probs,1e-12,1-1e-12); cum=np.cumsum(profits); peak=np.maximum.accumulate(np.r_[0,cum])[1:]
 return {'n':len(rows),'wins':sum(wins),'hit_rate':float(np.mean(wins)),'roi':float(np.mean(profits)),'log_loss':float(-np.mean(y*np.log(p)+(1-y)*np.log(1-p))),'brier':float(np.mean((p-y)**2)),'mean_odds':float(np.mean(odds)),'max_drawdown_units':float(np.max(peak-cum))}
def paired(rows,model='candidate'):
 groups=defaultdict(list)
 for r in rows:groups[r['day']].append(r)
 days=sorted(groups); rng=np.random.default_rng(20261108); delta=[]
 for _ in range(2000):
  sample=[r for i in rng.integers(0,len(days),len(days)) for r in groups[days[i]]]; a=calc(sample,model);b=calc(sample,'market_home');delta.append([a[k]-b[k] for k in ('roi','hit_rate','log_loss','brier')])
 return {k:np.percentile(np.array(delta)[:,i],[2.5,97.5]).tolist() for i,k in enumerate(('roi','hit_rate','log_loss','brier'))}
def receipt_digest():
 receipts=[]
 for kind in ('scoreboard','summary'):
  for path in sorted((CACHE/kind).glob('*.json')):
   if path.name.endswith('.receipt.json'):continue
   verified_json(path)
   receipts.append([kind,path.name,json.loads(path.with_suffix('.receipt.json').read_text(encoding='utf8'))])
  for path in sorted((CACHE/kind).glob('*.unavailable.body')):
   meta=path.with_name(path.name.replace('.body','.receipt.json'));receipt=json.loads(meta.read_text(encoding='utf8'))
   if receipt['status']!=404 or hashlib.sha256(path.read_bytes()).hexdigest()!=receipt['sha256']:raise ValueError(f'invalid unavailable receipt: {path}')
   receipts.append([kind,path.name,receipt])
 return hashlib.sha256(json.dumps(receipts,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def availability():
 events,_,_=read_events();games=[g for e in events.values() if (g:=parse_game(e))];games.sort(key=lambda g:(g['day'],g['utc'],g['id']))
 histories=defaultdict(list);features_by_id={};bykey=defaultdict(list)
 for g in games:
  features_by_id[g['id']]=features(g,histories,'efficiency')
  for t in g['teams']:histories[t].append(g)
  bykey[(g['day'],*g['teams'])].append(g)
 rows=[json.loads(s) for s in gzip.open(PRICE,'rt',encoding='utf8')];counts=Counter();matches=[]
 for r in rows:
  if r['league']!='NBA' or not '2025-01-01'<=r['day']<='2026-10-27':continue
  found=bykey[(r['day'],name(r['home']),name(r['away']))]
  if len(found)==1:matches.append((r,found[0]))
 duplicates=Counter(g['id'] for _,g in matches)
 for r,g in matches:
  if duplicates[g['id']]!=1 or (g['scores'][0]>g['scores'][1])!=(r['y']==0) or features_by_id[g['id']] is None:continue
  d=r['day']
  if d<='2025-08-28':counts['fit']+=1
  elif '2025-09-01'<=d<='2025-12-31':counts['validation']+=1
  elif '2026-01-01'<=d<='2026-10-27':counts['holdout']+=1
 thresholds={'fit':300,'validation':100,'holdout':300}
 mode='efficiency' if all(counts[k]>=v for k,v in thresholds.items()) else 'shooting_efficiency'
 result={'mode':mode,'full_efficiency_eligible':dict(counts),'thresholds':thresholds,'receipt_digest':receipt_digest(),'price_sha256':hashlib.sha256(PRICE.read_bytes()).hexdigest()}
 OUT.mkdir(exist_ok=True,parents=True);(OUT/'availability.json').write_text(json.dumps(result,indent=2),encoding='utf8')
 print(json.dumps(result),flush=True)

def main():
 decision=json.loads((OUT/'availability.json').read_text(encoding='utf8'))
 if decision['receipt_digest']!=receipt_digest() or decision['price_sha256']!=hashlib.sha256(PRICE.read_bytes()).hexdigest():raise ValueError('availability inputs changed; rerun and record availability before evaluation')
 events,dup,conf=read_events(); games=[g for e in events.values() if (g:=parse_game(e))]; games.sort(key=lambda g:(g['day'],g['utc'],g['id']))
 complete=sum(all('orb' in s and 'tov' in s for s in g['stats']) for g in games); mode=decision['mode']
 print('games',len(games),'complete boxes',complete,'mode',mode,flush=True)
 histories=defaultdict(list); feat={}
 for g in games:
  feat[g['id']]=features(g,histories,mode,with_sources=True)
  for t in g['teams']: histories[t].append(g)
 bykey=defaultdict(list)
 for g in games:bykey[(g['day'],*g['teams'])].append(g)
 allprices=[json.loads(s) for s in gzip.open(PRICE,'rt',encoding='utf8')];allprices=[r for r in allprices if r['league']=='NBA' and r['day']<='2026-10-27']
 prices=sorted((r for r in allprices if r['day']>='2025-01-01'),key=lambda r:(r['day'],r['kickoff'],r['id']))
 counts=Counter(); joined=[]; seen=Counter()
 for r in prices:
  h,a=name(r['home']),name(r['away'])
  if not h or not a:counts['ambiguous_or_unknown_team']+=1;continue
  matches=bykey[(r['day'],h,a)]
  if not matches:counts['no_matching_game']+=1;continue
  if len(matches)!=1:counts['nonunique_espn_fixture']+=1;continue
  g=matches[0]; seen[g['id']]+=1
  if (g['scores'][0]>g['scores'][1])!=(r['y']==0):counts['outcome_conflict']+=1;continue
  counts['matched_outcome_agrees']+=1
  if feat[g['id']] is None:counts['insufficient_history']+=1;continue
  q=(1/r['odds'][0])/(1/r['odds'][0]+1/r['odds'][1]); joined.append({'day':r['day'],'id':r['id'],'espn_id':g['id'],'teams':g['teams'],'scores':g['scores'],'odds':r['odds'],'home_win':int(r['y']==0),'feature':feat[g['id']][0],'history_event_ids':feat[g['id']][1],'market_home':q})
 duplicate_ids={k for k,v in seen.items() if v>1}; counts['duplicate_price_event_ids']=len(duplicate_ids); joined=[r for r in joined if r['espn_id'] not in duplicate_ids]
 fitrows=[r for r in joined if r['day']<='2025-08-28']; val=[r for r in joined if '2025-09-01'<=r['day']<='2025-12-31']; hold=[r for r in joined if '2026-01-01'<=r['day']<='2026-10-27']
 if not fitrows or not hold:raise RuntimeError('insufficient eligible observations')
 states={'validation':fit(fitrows),'holdout':fit([r for r in joined if r['day']<='2025-12-28'])}
 spec=importlib.util.spec_from_file_location('nba_existing_common',ROOT/'experiments/all-sports-underdog-20261031/common.py'); common=importlib.util.module_from_spec(spec);spec.loader.exec_module(common)
 ordered,x=common.causal_features(allprices); q=np.array([[1/o for o in r['odds']] for r in ordered]);q=q/q.sum(axis=1,keepdims=True); y=np.array([r['y'] for r in ordered]); idx={r['id']:i for i,r in enumerate(ordered)}
 for subset,key in ((val,'validation'),(hold,'holdout')):
  state=states[key]
  cutoff='2025-08-28' if key=='validation' else '2025-12-28'
  training=np.array([i for i,r in enumerate(ordered) if '2025-01-01'<=r['day']<=cutoff]); baseline=common.train(x[training],y[training],q[training],False);states[key]['existing_outcome_form']=baseline
  predictions=common.predict(baseline,x[[idx[r['id']] for r in subset]],q[[idx[r['id']] for r in subset]]) if subset else []
  for r,baseline_p in zip(subset,predictions):
   r['candidate']=float(expit(logit(r['market_home'])+state['beta']*(r['feature']-state['mean'])/state['sd']))
   r['existing_outcome_form']=float(baseline_p[0])
   r['selected_side']='home' if r['candidate']>=.5 else 'away'
   r['selected_probability']=r['candidate'] if r['selected_side']=='home' else 1-r['candidate']
 result={'mode':mode,'prices':len(prices),'scoreboard_events':len(events),'duplicate_event_observations':dup,'conflicting_event_ids':conf,'valid_games':len(games),'incomplete_shooting_games':sum(any(not s for s in g['stats']) for g in games),'complete_team_boxes':complete,'invalid_summary':sum(bool(g.get('summary_invalid')) for g in games),'summary_http_404':len(list((CACHE/'summary').glob('*.unavailable.receipt.json'))),'source_games_without_price':sum(g['id'] not in seen for g in games),'join_counts':dict(counts),'eligible_fit':len(fitrows),'states':states,'ambiguous_aliases':{k:v for k,v in ALIAS_INDEX.items() if len(v)>1},'periods':{}}
 for key,subset in (('validation',val),('holdout',hold)):
  months={m:{model:calc([r for r in subset if r['day'].startswith(m)],model) for model in ('candidate','market_home','existing_outcome_form')} for m in sorted({r['day'][:7] for r in subset})}
  eligible_prices=sum(('2025-09-01'<=r['day']<='2025-12-31') if key=='validation' else ('2026-01-01'<=r['day']<='2026-10-27') for r in prices)
  result['periods'][key]={'price_rows':eligible_prices,'coverage':len(subset)/eligible_prices if eligible_prices else None,'candidate':calc(subset,'candidate'),'market':calc(subset,'market_home'),'existing_outcome_form':calc(subset,'existing_outcome_form'),'paired_date_bootstrap_95':paired(subset) if subset else None,'monthly':months}
 OUT.mkdir(exist_ok=True,parents=True)
 (OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
 receipts={}
 for kind in ('scoreboard','summary'):
  records=[json.loads(p.read_text(encoding='utf8')) for p in sorted((CACHE/kind).glob('*.receipt.json'))]
  canonical=json.dumps(records,sort_keys=True,separators=(',',':')).encode()
  receipts[kind]={'count':len(records),'status_counts':dict(Counter(str(x['status']) for x in records)),'first_received_at':min((x['received_at'] for x in records),default=None),'last_received_at':max((x['received_at'] for x in records),default=None),'receipts_sha256':hashlib.sha256(canonical).hexdigest()}
 receipts['price_sha256']=hashlib.sha256(PRICE.read_bytes()).hexdigest()
 (OUT/'source_receipts.json').write_text(json.dumps(receipts,indent=2),encoding='utf8')
 with (OUT/'predictions.jsonl.gz').open('wb') as f:
  with gzip.GzipFile(fileobj=f,mode='wb',mtime=0,filename='') as z:
   for r in val+hold:z.write((json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n').encode())
 print(json.dumps({'join_counts':dict(counts),'fit':len(fitrows),'validation':len(val),'holdout':len(hold)},ensure_ascii=False),flush=True)
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--availability',action='store_true');args=parser.parse_args()
 availability() if args.availability else main()
