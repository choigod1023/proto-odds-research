import gzip,json,math,hashlib,random
from pathlib import Path
from datetime import date,timedelta
from collections import defaultdict,Counter
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[2]; OUT=Path(__file__).resolve().parent
NAMES=('market','fixed_mix','short_long','bocpd','gas')
def clip(x): return min(.999999,max(.000001,x))
def logit(p): p=clip(p); return math.log(p/(1-p))
def sigmoid(x): return 1/(1+math.exp(-max(-20,min(20,x))))
class State:
 def __init__(self): self.short=0.; self.long=0.; self.bias=0.; self.runs=[(1.,0,0.,0.)]; self.recent=[]
 def update(self,p,y):
  self.recent=(self.recent+[int((p>=.5)==bool(y))])[-20:]
  e=y-p; self.short=.9*self.short+.1*e; self.long=.99*self.long+.01*e
  pred=sigmoid(logit(p)+self.bias); self.bias=.99*self.bias+.05*(y-pred)
  # Reset branch uses Beta(5,5) prior centered on the market; each run keeps
  # successes and expected successes, a market-offset beta pseudo-count.
  branches=[]; reset=0.
  for w,n,wins,expected in self.runs:
   q=clip(p+(wins-expected)/(10+n)); likelihood=q if y else 1-q
   branches.append((w*.98*likelihood,n+1,wins+y,expected+p)); reset+=w*.02*(p if y else 1-p)
  branches=[(reset,0,0.,0.)]+branches
  if len(branches)>101:
   # Retain bounded run hypotheses; renormalize after dropping negligible tail.
   branches=branches[:101]
  z=sum(b[0] for b in branches); self.runs=[(w/z,n,a,b) for w,n,a,b in branches]
 def probs(self,p):
  return {'market':p,'short_long':clip(p+.5*(self.short+self.long)),
          'bocpd':clip(p+sum(w*(wins-expected)/(10+n) for w,n,wins,expected in self.runs)),
          'gas':sigmoid(logit(p)+self.bias)}
def transform(r):
 p=r['market_p']; odds=r['odds']; y=r['y']
 if len(p)==3:
  if y==1: return None
  p=[p[0],p[2]]; odds=[odds[0],odds[2]]; y=0 if y==0 else 1
 if len(p)!=2 or len(odds)!=2 or y not in (0,1) or not all(math.isfinite(float(x)) and float(x)>0 for x in p+odds): return None
 p0=p[0]/sum(p); form=r['predictions']['outcome_form']['p']; form=[form[0],form[-1]]; form0=form[0]/sum(form)
 return dict(event_id=r['id'],day=r['day'],sport=r['sport'],league=r['league'],home=r['home'],away=r['away'],y=y,odds=odds,market_p=[p0,1-p0],form_p=form0,fallback=r['fallback'])
def calculate(rows):
 states=defaultdict(State); pending=defaultdict(list); out=[]; day_groups=defaultdict(list)
 for row in rows: day_groups[row['day']].append(row)
 for day in sorted(set(r['day'] for r in rows)):
  for due in sorted(k for k in pending if k<=day):
   for r in pending.pop(due): states[(r['sport'],r['league'])].update(r['market_p'][0],int(r['y']==0))
  for r in day_groups[day]:
   state=states[(r['sport'],r['league'])]; probs=state.probs(r['market_p'][0]); probs['fixed_mix']=.5*(r['market_p'][0]+r['form_p'])
   if day>='2025-01-01':
    for name in NAMES:
     p=probs[name]; v=[p,1-p]; pick=0 if p>=.5 else 1; hit=pick==r['y']; net=Decimal(str(r['odds'][pick]))-1 if hit else Decimal(-1)
     out.append({**{k:r[k] for k in ('event_id','day','sport','league','home','away','y','odds','market_p','fallback')},'model':name,'p':v,'pick':pick,'hit':hit,'net':str(net),'recent_market_slump':len(state.recent)==20 and sum(state.recent)<10,'recent_history_n':len(state.recent)})
   due=(date.fromisoformat(day)+timedelta(days=4)).isoformat(); pending[due].append(r)
 return out
def metrics(rows):
 if not rows:return None
 n=len(rows); net=Decimal(0); daily=defaultdict(Decimal); ll=br=0.; hits=0
 for r in rows:
  v=Decimal(r['net']); net+=v; daily[r['day']]+=v; hits+=r['hit']; ll-=math.log(r['p'][r['y']]); br+=sum((p-int(i==r['y']))**2 for i,p in enumerate(r['p']))
 c=peak=dd=Decimal(0)
 for d in sorted(daily): c+=daily[d]; peak=max(peak,c); dd=max(dd,peak-c)
 return dict(n=n,hits=hits,hit_rate=hits/n,roi=float(net/n),net=str(net),log_loss=ll/n,brier=br/n,mdd_daily_units=float(dd))
def bootstrap(groups,a,b):
 keys=sorted(groups); rng=random.Random(20261108); arr=[]
 for _ in range(1000):
  num=[0.,0.,0.,0.]; total=0
  for __ in keys:
   g=groups[rng.choice(keys)]; x=g[a]; y=g[b]; total+=x[0]
   for j in range(4): num[j]+=x[j+1]-y[j+1]
  arr.append([v/total for v in num])
 ans={}
 for i,k in enumerate(('hit_rate','roi','log_loss','brier')):
  xs=sorted(x[i] for x in arr); p=min(1,2*min(sum(x<=0 for x in xs),sum(x>=0 for x in xs))/len(xs)); ans[k]={'ci95':[xs[24],xs[974]],'sign_tail_p':p}
 return ans
def main():
 global DATA,byday
 counts=Counter(); hashes={}; raw=[]
 for s in ('bs','sc','bk','vl'):
  path=ROOT/f'experiments/all-sports-underdog-20261031/results/{s}/domestic_archive/ledger.jsonl.gz'; hashes[s]=hashlib.sha256(path.read_bytes()).hexdigest()
  with gzip.open(path,'rt',encoding='utf8') as f:
   for line in f:
    r=json.loads(line); counts[s+'_raw']+=1
    try: t=transform(r)
    except (ValueError,KeyError,ZeroDivisionError,TypeError): t=None
    if t is None: counts[s+'_draw_or_invalid']+=1; continue
    raw.append(t)
 ids=Counter((r['sport'],r['event_id']) for r in raw); DATA=sorted((r for r in raw if ids[(r['sport'],r['event_id'])]==1),key=lambda x:(x['day'],x['sport'],x['event_id'])); counts['duplicate_rows_excluded']=len(raw)-len(DATA)
 byday=defaultdict(list)
 for r in DATA: byday[r['day']].append(r)
 rows=calculate(DATA); all_eval=[r for r in rows if '2026-01-01'<=r['day']<='2026-08-26']; evaluation=[r for r in all_eval if r['sport'] in ('bs','bk')]; validation=[r for r in rows if '2025-01-01'<=r['day']<='2025-12-31' and r['sport'] in ('bs','bk')]; diagnostic=[r for r in all_eval if r['sport']=='sc']
 with gzip.open(OUT/'predictions.jsonl.gz','wt',encoding='utf8',newline='\n') as f:
  for r in evaluation: f.write(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n')
 subsets=defaultdict(list)
 for r in evaluation:
  d=r['day']; subsets['all'].append(r); subsets['sport:'+r['sport']].append(r); subsets['league:'+r['sport']+':'+r['league']].append(r); subsets['month:'+d[:7]].append(r); subsets['quarter:'+d[:4]+'Q'+str((int(d[5:7])-1)//3+1)].append(r)
  if r['recent_market_slump']: subsets['slump_league:'+r['sport']+':'+r['league']].append(r)
  if r['pick'] != (0 if r['market_p'][0]>=.5 else 1): subsets['underdog_pick_league:'+r['sport']+':'+r['league']].append(r)
 for r in evaluation:
  for prefix in ('slump_league:', 'underdog_pick_league:'): subsets.setdefault(prefix+r['sport']+':'+r['league'],[])
 summary={key:{n:metrics([r for r in rr if r['model']==n]) for n in NAMES} for key,rr in subsets.items()}
 grouped=defaultdict(dict)
 for r in evaluation: grouped[(r['sport'],r['event_id'])][r['model']]=r
 weekly=defaultdict(lambda:{n:[0,0.,0.,0.,0.] for n in NAMES}); changes={n:Counter() for n in NAMES}
 for same in grouped.values():
  assert set(same)==set(NAMES); base=same['market']; dt=date.fromisoformat(base['day']); week=(dt-timedelta(days=dt.weekday())).isoformat()
  for n,r in same.items():
   assert (r['day'],r['sport'],r['league'],r['y'],r['odds'],r['market_p'])==(base['day'],base['sport'],base['league'],base['y'],base['odds'],base['market_p'])
   assert abs(sum(r['p'])-1)<1e-12 and r['net']==str(Decimal(str(r['odds'][r['pick']]))-1 if r['hit'] else Decimal(-1))
   w=weekly[week][n]; w[0]+=1;w[1]+=r['hit'];w[2]+=float(r['net']);w[3]+=-math.log(r['p'][r['y']]);w[4]+=sum((p-int(i==r['y']))**2 for i,p in enumerate(r['p']))
   if r['pick']!=base['pick']: changes[n]['home_to_away' if base['pick']==0 else 'away_to_home']+=1
 cis={n:{b:bootstrap(weekly,n,b) for b in (('market','fixed_mix') if n=='short_long' else ('market','fixed_mix','short_long'))} for n in ('short_long','bocpd','gas')}
 result={'scope':'2026-01-01..2026-08-26; baseball and basketball two-way settled archive; shared exact event, odds, outcome','probability_order':['home','away'],'training':'2023-2024 warmup, 2025 validation, no model selection','timing_limit':'day+4 assumption; original price receipt and execution not certified','source_sha256':hashes,'counts':dict(counts),'soccer_posthoc_nondraw_diagnostic_only':{n:metrics([r for r in diagnostic if r['model']==n]) for n in NAMES},'validation':{n:metrics([r for r in validation if r['model']==n]) for n in NAMES},'evaluation':summary,'changed_picks_vs_market':{n:dict(x) for n,x in changes.items()},'paired_week_bootstrap_1000':cis}
 (OUT/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
if __name__=='__main__': main()
