import gzip,json,hashlib
from collections import defaultdict,Counter
from datetime import date,timedelta
from decimal import Decimal as D
from pathlib import Path

base=Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/all-sports-underdog-validation-20261031/experiments/all-sports-underdog-20261031')
out=Path(__file__).parent
cohort=out/'domestic_archive'
expected={'common.py':'2a507d75338442feeedb1d7ec85402f0f3f8217758b7ac8f89ad7b9a21c8e092','protocol.json':'b6397461d8144a802ce80be25be45ccdbb8f9b9fbdf3f89079759b8afca22590','inputs/domestic.jsonl.gz':'4bf3abf8bb82838ee72975e1ef486a471b357a03ee4d8627808780f046dae51a','inputs/external-football.jsonl.gz':'31c4e7bc3a4dfc4bafa1bcb70ba295e99d910b3e326c14bfffe5bb0bf1613605'}
def readgz(p):
 with gzip.open(p,'rt',encoding='utf8') as f: return [json.loads(s) for s in f]
source=[r for r in readgz(base/'inputs/domestic.jsonl.gz') if r['sport']=='bk']
rows=readgz(cohort/'ledger.jsonl.gz')
report=json.loads((cohort/'results.json').read_text(encoding='utf8'))
states=json.loads((cohort/'states.json').read_text(encoding='utf8'))
errors=[]
def check(ok, desc):
 if not ok: errors.append(desc)
models=('market','outcome_form','market_residual','half_market_form')
original={r['id']:r for r in source}
check(len(original)==len(source)==len(rows), 'source/ledger count or uniqueness')
check(set(original)=={r['id'] for r in rows},'source/ledger IDs')
by_month={s['month']:s for s in states}
check(set(by_month)=={r['day'][:7] for r in rows},'month coverage')
for s in states:
 cutoff=(date.fromisoformat(s['month']+'-01')-timedelta(days=4)).isoformat()
 train=[r for r in source if r['day']<=cutoff and r['cohort']==report['cohort']]
 fallback=len(train)<200 or len({r['y'] for r in train})<2
 check((s['training_cutoff_inclusive'],s['training_rows'],s['fallback'])==(cutoff,len(train),fallback),'state '+s['month'])
for r in rows:
 ident=r['id']; orig=original.get(ident)
 if orig is None: continue
 check(all(r.get(k)==v for k,v in orig.items()),ident+' source fields/odds/label')
 check(r['fallback']==by_month[r['day'][:7]]['fallback'],ident+' fallback')
 odds=[D(str(x)) for x in orig['odds']]; inv=[D(1)/v for v in odds]; q=[v/sum(inv) for v in inv]
 check(all(abs(D(str(a))-b)<D('1e-14') for a,b in zip(r['market_p'],q)),ident+' market odds')
 for m in models:
  p=r['predictions'][m]; probs=[D(str(x)) for x in p['p']]; pick=max(range(len(probs)),key=lambda i:probs[i]); top=max(q)
  group='underdog' if q[pick]<top-D('1e-12') else ('top_tie' if sum(abs(x-top)<=D('1e-12') for x in q)>1 else 'favorite')
  rank=1+sum(x>q[pick]+D('1e-12') for x in q)
  hit=pick==orig['y']; net=odds[pick]-1 if hit else D(-1)
  check(p['pick']==pick and p['group']==group and p['rank']==rank and p['hit']==hit and D(p['net'])==net and not p['draw'],ident+' '+m+' pick/group/hit/net')
  if m=='market' or r['fallback']:
   check(all(abs(a-b)<D('1e-14') for a,b in zip(probs,q)),ident+' '+m+' market/fallback')

def stats(subset,m,policy):
 chosen=[r for r in subset if policy=='all' or policy=='favorites' and r['predictions'][m]['group']=='favorite' or policy in ('underdogs','market_same_underdogs') and r['predictions'][m]['group']=='underdog']
 use='market' if policy=='market_same_underdogs' else m
 values=[]
 for r in chosen:
  pick=r['predictions'][use]['pick']; values.append(D(str(r['odds'][pick]))-1 if pick==r['y'] else D(-1))
 net=sum(values,D(0)); return {'bets':len(chosen),'hits':sum(r['predictions'][use]['pick']==r['y'] for r in chosen),'net_units':str(net),'roi':float(net/len(chosen)) if chosen else None}
summary={}
for year,subset,section in [('full',rows,report['full'])]+[(y,[r for r in rows if r['day'].startswith(y)],v) for y,v in report['by_year'].items()]:
 summary[year]={}
 for m in models:
  summary[year][m]={}
  for policy in ('all','favorites','underdogs','market_same_underdogs'):
   got=stats(subset,m,policy); recorded=section[m]['policies'][policy]
   check(got['bets']==recorded['bets'] and got['hits']==recorded['hits'] and D(got['net_units'])==D(recorded['net_units']) and got['roi']==recorded['roi'],year+' '+m+' '+policy+' aggregate')
   summary[year][m][policy]=got
hashes={p:hashlib.sha256((base/p).read_bytes()).hexdigest() for p in expected}
check(hashes==expected,'source/input SHA changed during run')
validation={'status':'PASS' if not errors else 'FAIL','source_count':len(source),'ledger_count':len(rows),'model_prediction_rows_checked':len(rows)*len(models),'cohort':report['cohort'],'date_range':report['range'],'fallback_events':sum(r['fallback'] for r in rows),'fallback_months':[s['month'] for s in states if s['fallback']],'source_sha256_start_end':{p:{'start':expected[p],'end':hashes[p]} for p in expected},'checks':'Source fields including odds and labels, all IDs, Decimal market probabilities, argmax, tie-aware market classification/rank, hit and Decimal unit net for every model row; full and yearly four-policy bets/hits/net/ROI versus report; monthly cutoff, training count and fallback; common and inputs hashes. Forecast fitting itself is not independently recalculated.','summary':summary,'errors':errors}
years=sorted(k for k in summary if k!='full')
validation['yearly_signs_and_changes']={}
for m in models:
 validation['yearly_signs_and_changes'][m]={}
 for policy in ('all','favorites','underdogs'):
  signs={y:('positive' if D(summary[y][m][policy]['net_units'])>0 else 'negative' if D(summary[y][m][policy]['net_units'])<0 else 'zero') for y in years}
  validation['yearly_signs_and_changes'][m][policy]={'signs':signs,'changes':[years[i] for i in range(1,len(years)) if signs[years[i]]!=signs[years[i-1]]]}
(out/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps({'status':validation['status'],'errors':errors[:20],'full':summary['full'],'yearly':{y:{m:summary[y][m]['all']['net_units'] for m in models} for y in summary if y!='full'},'fallback_months':validation['fallback_months']},ensure_ascii=False))
if errors: raise SystemExit(1)
