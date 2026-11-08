import collections,datetime,gzip,json,hashlib,math
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parent; ROOT=P.parents[1]
ALIASES={'LA레이커':'LAL','LA클리퍼':'LAC','골든워리':'GSW','뉴올펠리':'NOP','뉴욕닉스':'NYK','댈러매버':'DAL','덴버너게':'DEN','디트피스':'DET','마이히트':'MIA','멤피그리':'MEM','미네울브':'MIN','밀워벅스':'MIL','보스셀틱':'BOS','브루네츠':'BKN','새크킹스':'SAC','샌안스퍼':'SAS','샬럿호네':'CHA','시카불스':'CHI','애틀호크':'ATL','오클썬더':'OKC','올랜매직':'ORL','워싱위저':'WAS','유타재즈':'UTA','인디페이':'IND','클리캐벌':'CLE','토론랩터':'TOR','포틀트레':'POR','피닉선즈':'PHX','필라76s':'PHI','휴스로케':'HOU'}
LEDGER=ROOT/'experiments/all-sports-underdog-20261031/results/bk/domestic_archive/ledger.jsonl.gz'
def read(p):
 with gzip.open(p,'rt',encoding='utf8') as f:return [json.loads(s) for s in f]
def date_kst(s):return (datetime.datetime.fromisoformat(s.replace('Z','+00:00'))+datetime.timedelta(hours=9)).date().isoformat()
def join(forecasts,ledger):
 index=collections.defaultdict(list);counts=collections.Counter(); matches=[]
 for row in ledger:
  if row.get('league')=='NBA' and row.get('day','').startswith('2026'):
   if row['home'] not in ALIASES or row['away'] not in ALIASES: counts['unknown_alias_ledger']+=1;continue
   index[(row['day'],ALIASES[row['home']],ALIASES[row['away']])].append(row)
 test=[r for r in forecasts if r['split']=='test']; keys=collections.Counter((date_kst(r['start']),r['home'],r['away']) for r in test)
 for r in test:
  key=(date_kst(r['start']),r['home'],r['away']);cand=index.get(key,[])
  if keys[key]!=1:counts['ambiguous_test_key']+=1;continue
  if len(cand)==0:counts['no_ledger_match']+=1;continue
  if len(cand)>1:counts['duplicate_ledger_match']+=1;continue
  row=cand[0]; odds=row.get('odds',[]);p=row.get('market_p',[])
  if len(odds)!=2 or len(p)!=2 or any(not isinstance(x,(float,int)) or not math.isfinite(x) or x<=1 for x in odds) or row.get('y') not in [0,1]:counts['invalid_2way_price_or_label']+=1;continue
  y=0 if r['margin']>0 else 1
  if row['y']!=y: counts['label_score_mismatch']+=1;continue
  item={**r,'ledger_id':row['id'],'ledger_day':row['day'],'ledger_kickoff':row['kickoff'],'kickoff_precision':row['kickoff_precision'],'ledger_sources':row['sources'],'odds':odds,'market_home_probability':p[0],'settled_winner':y,'result':'matched_archival_label'}
  matches.append(item);counts['matched']+=1
 return matches,dict(counts)
def stats(rows,model):
 pnls=[];ll=[];bs=[];hits=0; peak=0;balance=0;draw=0
 for r in rows:
  p=r['market_home_probability'] if model=='market' else r['home_win_probability'][model];pick=0 if p>=.5 else 1;y=r['settled_winner'];hit=pick==y;hits+=hit;net=r['odds'][pick]-1 if hit else -1;pnls.append(net);balance+=net;peak=max(peak,balance);draw=max(draw,peak-balance);home=int(y==0);ll.append(-(home*math.log(p)+(1-home)*math.log(1-p)));bs.append((p-home)**2)
 n=len(rows)
 return {'n':n,'hits':hits,'accuracy':hits/n if n else None,'roi':sum(pnls)/n if n else None,'mdd_units':draw if n else None,'log_loss':sum(ll)/n if n else None,'brier':sum(bs)/n if n else None}
if __name__=='__main__':
 rows,counts=join(read(P/'predictions.jsonl.gz'),read(LEDGER)); rows.sort(key=lambda x:(x['start'],x['id']))
 months=collections.defaultdict(list);weeks=collections.defaultdict(list)
 for r in rows:
  months[r['ledger_day'][:7]].append(r); d=datetime.date.fromisoformat(r['ledger_day']).isocalendar();weeks[f'{d.year}-W{d.week:02d}'].append(r)
 comps={};rng=np.random.default_rng(20261108); keys=sorted(weeks)
 for a,b in [('player','market'),('pair','market'),('pair','player')]:
  if not rows:continue
  def val(r,k):
   p=r['market_home_probability'] if k=='market' else r['home_win_probability'][k]; pick=0 if p>=.5 else 1;return (r['odds'][pick]-1 if pick==r['settled_winner'] else -1),pick
  changed=[r for r in rows if val(r,a)[1]!=val(r,b)[1]];diffs=[]
  for _ in range(5000):
   sample=[r for j in rng.integers(0,len(keys),size=len(keys)) for r in weeks[keys[j]]];diffs.append(sum(val(r,a)[0]-val(r,b)[0] for r in sample)/len(sample))
  comps[a+'_minus_'+b]={'changed_picks':len(changed),'roi_difference':stats(rows,a)['roi']-stats(rows,b)['roi'],'weekly_paired_roi_ci95':list(np.quantile(diffs,[.025,.975])),'weeks':len(keys)}
 summary={'source_sha256':hashlib.sha256(LEDGER.read_bytes()).hexdigest(),'join_counts':counts,'sample':'unique NBA team alias + KST date; final archived outcome agrees with PBP','market_claim':'prior research primary full-game 2-way; individual product identity and overtime settlement not present in ledger','availability':'archived prices and kickoff timezone uncertified; not proof of pregame executable prices','metrics':{k:stats(rows,k) for k in ('market','team','player','pair')},'monthly':{m:{k:stats(rs,k) for k in ('market','team','player','pair')} for m,rs in sorted(months.items())},'paired':comps}
 with gzip.GzipFile(filename='',mode='wb',fileobj=open(P/'priced-matches.jsonl.gz','wb'),mtime=0) as f:
  for x in rows:f.write((json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n').encode())
 (P/'priced-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
 model_summary=json.loads((P/'prototype-summary.json').read_text(encoding='utf8')); model_summary.update(reference_price_matches=len(rows),strict_exact_product_settlement_matches=0)
 for name in ('prototype-summary.json','summary.json'): (P/name).write_text(json.dumps(model_summary,indent=2),encoding='utf8')
 print(json.dumps(summary,indent=2))
