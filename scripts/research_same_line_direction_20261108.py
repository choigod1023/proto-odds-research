import collections, datetime as dt, hashlib, json, math, random, re
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from bets import SEL_NAMES
from same_line_research_rules_20261108 import canonical_options, partition
P=Path('C:/Users/user/AppData/Local/Temp'); OUT=Path('experiments/same-line-market-evidence-20261108')
a=json.loads((P/'market-changes-audit-live-20261108.json').read_text(encoding='utf-8')); b=json.loads((P/'settlement-deploy-all-after-20261108.json').read_text(encoding='utf-8'))
def key(g): return tuple(g.get(k) for k in ('round','sport','league','home','away','date'))
def parse(s): return dt.datetime.fromisoformat(s.replace('Z','+00:00'))
def prob(odds):
 inv=[1/x for x in odds]; return [x/sum(inv) for x in inv]
results=collections.defaultdict(list)
for g in b['result_games']: results[key(g)].append(g)
counts=collections.Counter(); candidates=[]
for g in b['live']+b['past']:
 counts['fixture_rows']+=1; found=results[key(g)]
 if len(found)!=1: continue
 counts['unique_exact_fixture_result']+=1; rec=found[0].get('prediction_record') or {}; opts=g.get('options',[])
 selected=[o for o in opts if o.get('selection_id')==rec.get('selection_id') and o.get('selection_id')]
 if len(selected)!=1: continue
 counts['unique_record_selection']+=1; s=selected[0]
 if any(rec.get(k)!=s.get(k) for k in ('market','label')) or rec.get('selection')!=s.get('선택'):continue
 ops=[o for o in opts if o.get('게임번호')==s.get('게임번호')]
 if len(ops)!=2 or any(o.get('n_way')!=2 for o in ops): continue
 ops=canonical_options(s.get('market'), ops, SEL_NAMES)
 if ops is None: continue
 counts['two_way_complete_options_canonical']+=1
 if rec.get('result') not in ('hit','miss'):continue
 counts['record_hit_or_miss']+=1
 if any(o.get('market')!=s.get('market') or o.get('label')!=s.get('label') or o.get('line')!=s.get('line') for o in ops):continue
 history=a['history'].get(str(g['round']),{}).get(str(s.get('게임번호')),[])
 try:
  kickoff=parse(rec['kickoff']); local=kickoff.astimezone(dt.timezone(dt.timedelta(hours=9)))
  m=re.match(r'(\d{2})\.(\d{2})\([^)]*\) (\d{2}):(\d{2})',g['date'])
  if not m or (local.year,local.month,local.day,local.hour,local.minute)!=(g['year'],*map(int,m.groups())):continue
  cutoff=kickoff-dt.timedelta(minutes=30)
  hs=sorted((h for h in history if parse(h['observed_at'])<=cutoff),key=lambda h:parse(h['observed_at']))
 except (TypeError,ValueError,KeyError):continue
 if len(hs)<2:continue
 counts['two_pre_t30_observations']+=1
 if any(h.get('market')!=s.get('market') or h.get('label')!=s.get('label') or h.get('line')!=s.get('line') or len(h.get('odds',[]))!=2 or any(not isinstance(x,(int,float)) or x<=1 for x in h['odds']) for h in hs):continue
 counts['same_line_complete_history']+=1
 first,last=hs[0],hs[-1]
 if first['observed_at']==last['observed_at'] or first['odds'][0]==first['odds'][1] or last['odds'][0]==last['odds'][1]:continue
 counts['non_tied']+=1
 f=min(range(2),key=lambda i:first['odds'][i]); l=min(range(2),key=lambda i:last['odds'][i]); si=ops.index(s); win=si if rec['result']=='hit' else 1-si
 candidates.append({'round':g['round'],'kickoff':rec['kickoff'],'first_time':first['observed_at'],'last_time':last['observed_at'],'first_odds':first['odds'],'last_odds':last['odds'],'first_pick':f,'last_pick':l,'winner':win,'block':rec['kickoff'][:10], 'game_no':s.get('게임번호'),'market':s.get('market'),'label':s.get('label'),'fixed_pick':si,'record_odds':rec.get('odds'),'record_captured_at':rec.get('captured_at'),'fixture_key':list(key(g))})
# Exclude all physical duplicates, including cross-round re-releases, before separating late records.
candidates, late_candidates, duplicate_rows = partition(candidates)
counts['physical_duplicate_rows_excluded']=duplicate_rows
counts['late_or_missing_capture_excluded']=len(late_candidates)
candidates.sort(key=lambda x:x['kickoff'])
def summary(rows):
 n=len(rows); wins0=sum(x['first_pick']==x['winner'] for x in rows); wins1=sum(x['last_pick']==x['winner'] for x in rows)
 profits=[[x['last_odds'][x[k]]-1 if x[k]==x['winner'] else -1 for x in rows] for k in ('first_pick','last_pick')]
 def dd(v):
  cur=peak=worst=0
  for x in v:cur+=x;peak=max(peak,cur);worst=max(worst,peak-cur)
  return worst
 return {'n':n,'first_wins':wins0,'last_wins':wins1,'win_to_loss':sum(x['first_pick']==x['winner'] and x['last_pick']!=x['winner'] for x in rows),'loss_to_win':sum(x['first_pick']!=x['winner'] and x['last_pick']==x['winner'] for x in rows),'additional_wins':wins1-wins0,'hit_rate_difference_pp':100*(wins1-wins0)/n if n else None,'profit_units':[sum(v) for v in profits],'roi':[sum(v)/n for v in profits] if n else None,'max_drawdown_units':[dd(v) for v in profits]}
changed=[x for x in candidates if x['first_pick']!=x['last_pick']]
# Sample days as paired blocks, not quote rows.
rng=random.Random(20261108); blocks=collections.defaultdict(list)
for x in candidates:blocks[x['block']].append(x)
diffs=[]
if len(blocks)>=2:
 days=list(blocks)
 for _ in range(5000):
  sample=[x for day in rng.choices(days,k=len(days)) for x in blocks[day]]
  diffs.append(summary(sample)['hit_rate_difference_pp'])
 diffs.sort()
fixed=[]
fixed_rows=[x for x in candidates if x['record_captured_at'] and parse(x['record_captured_at'])<=parse(x['kickoff'])-dt.timedelta(minutes=30)]
for x in fixed_rows:
 y=int(x['fixed_pick']==x['winner']); ps=[prob(x[k])[x['fixed_pick']] for k in ('first_odds','last_odds')]
 fixed.append((y,ps))
fixed_returns=[x['record_odds']-1 if x['fixed_pick']==x['winner'] else -1 for x in fixed_rows if isinstance(x['record_odds'],(int,float)) and x['record_odds']>1]
fixed_metrics={'record_odds_n':len(fixed_returns),'fixed_pick_wins':sum(y for y,ps in fixed),'record_price_realized_roi':sum(fixed_returns)/len(fixed_returns) if fixed_returns else None,'n':len(fixed),'logloss':[sum(-(y*math.log(p)+(1-y)*math.log(1-p)) for y,ps in fixed for p in [ps[i]])/len(fixed) for i in (0,1)] if fixed else None,'brier':[sum((ps[i]-y)**2 for y,ps in fixed)/len(fixed) for i in (0,1)] if fixed else None}
changed_blocks=collections.defaultdict(list)
for x in changed: changed_blocks[x['block']].append(x)
changed_diffs=[]
if len(changed_blocks)>=2:
 days=list(changed_blocks)
 for _ in range(5000):
  sample=[x for day in rng.choices(days,k=len(days)) for x in changed_blocks[day]]
  changed_diffs.append(summary(sample)['hit_rate_difference_pp'])
 changed_diffs.sort()
result={'scope':'retrospective, stored history capped at 50; first retained is not necessarily opening; HTTP receipt and execution unproven','input_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [P/'market-changes-audit-live-20261108.json',P/'settlement-deploy-all-after-20261108.json']},'counts':dict(counts),'late_or_missing_capture':summary(late_candidates),'fixed_pick_probability_metrics':fixed_metrics,'time_split_first_half':summary(candidates[:len(candidates)//2]),'time_split_second_half':summary(candidates[len(candidates)//2:]),'changed_day_blocks':len(changed_blocks),'changed_day_bootstrap_95_hit_difference_pp':[changed_diffs[124],changed_diffs[4874]] if changed_diffs else None,'total':summary(candidates),'changed':summary(changed),'day_blocks':len(blocks),'paired_day_bootstrap_95_hit_difference_pp':[diffs[124],diffs[4874]] if diffs else None,'candidates':candidates}
(OUT/'direction-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='candidates'},ensure_ascii=False,indent=2))
