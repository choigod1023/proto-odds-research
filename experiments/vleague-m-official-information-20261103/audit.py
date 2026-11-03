"""Offline official census verification and conservative Betman cache intersection."""
import datetime, gzip, hashlib, json
from collections import Counter, defaultdict
from pathlib import Path
P=Path(__file__).resolve().parent; E=P/'evidence'
CACHE=Path('C:/Users/user/Documents/Codex/klprice/experiments/kleague-official-prices-20261103/evidence')
NAMES={'1001':['대한항공'],'1002':['삼성'], '1004':['KB손해보험'],'1005':['현대캐피탈'],'1006':['한국전력'],'1008':['OK저축은행'],'1009':['우리카드']}
def sha(b): return hashlib.sha256(b).hexdigest()
def jread(p): return json.loads(p.read_bytes())
def valid_name(code,name): return code in NAMES and any(s in (name or '') for s in NAMES[code])
def summarize_match(fixture,detail):
 g=detail['game']; assert all(g.get(k)==fixture.get(k) for k in ('gnum','gdate','seasonCode','leagueCode','gender','hcode','acode'))
 assert (g['seasonCode'],g['leagueCode'],g['gender'])==('022','201','1')
 teams={}
 for code in (g['hcode'],g['acode']):
  players=[x for x in detail['player'] if x['tcode']==code]
  starters=[{'id':p['pcode'],'name':p['pname'],'position':p['position']} for p in players if p.get('ynS1')=='-1' and p.get('position')!='L']
  liberos=[{'id':p['pcode'],'name':p['pname'],'set_flags':[p.get('ynS'+str(i)) for i in range(1,6)]} for p in players if p.get('position')=='L']
  teams[code]={'set1_starters':starters,'liberos':liberos,'players':[{'id':p['pcode'],'name':p['pname'],'position':p['position'],'set_flags':[p.get('ynS'+str(i)) for i in range(1,6)]} for p in players]}
 return {'official_id':f"001-022-201-{g['gnum']}",'date':g['gdate'],'teams':teams,'set_scores':[[g.get(f'hs{i}point'),g.get(f'as{i}point')] for i in range(1,6)],'match_sets':[g['hspoint'],g['aspoint']],'source_file':f"evidence/match-{g['gnum']:03}.body",'availability':'retrospective_current_receipt_postgame'}
def candidate(f,r):
 if r.get('leagueCode')!='VL001' or r.get('homeId')!=f['hcode'] or r.get('awayId')!=f['acode']: return False
 if not valid_name(r['homeId'],r.get('homeName')) or not valid_name(r['awayId'],r.get('awayName')): return False
 ts=datetime.datetime.fromtimestamp(r['gameDate']/1000,datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%d %H:%M')
 if ts!=f['gdate']+' '+f['gstime']: return False
 if r.get('mchScore')!=f"{f['hspoint']}:{f['aspoint']}": return False
 if str(r.get('gameResult'))!=('0' if f['hspoint']>f['aspoint'] else '2'): return False
 ids=(str(r.get('betId') or ''),str(r.get('betTypId') or ''))
 if ids not in (('',''),('4','2')): return False
 if r.get('betNm') not in (None,'','배구 승패'): return False
 if any(float(r.get(k) or 0)!=0 for k in ('handi','winHandi','drawHandi','loseHandi','drawAllot')): return False
 if str(r.get('buyReject'))!='0' or str(r.get('gameReject'))!='0': return False
 try:return float(r['winAllot'])>1 and float(r['loseAllot'])>1
 except (KeyError,TypeError,ValueError):return False
def main():
 sched=jread(E/'schedule.body')['payload']; games=sorted(sched['content'],key=lambda x:(x['gdate'],x['gnum']))
 assert len(games)==sched['page']['totalElements']==126 and len({x['gnum'] for x in games})==126
 records=[];missing=[]
 for f in games:
  path=E/f"match-{f['gnum']:03}.body";mp=E/f"match-{f['gnum']:03}.meta.json"
  if not path.exists() or not mp.exists():missing.append(f['gnum']);continue
  meta=jread(mp);b=path.read_bytes();assert meta['sha256']==sha(b) and meta['status']==200
  d=json.loads(b);assert d['result']['status']==200
  rec=summarize_match(f,d['payload']);rec['received_at']=meta['received_at'];records.append(rec)
 ledger=jread(CACHE/'attempts.json'); by_name={x['name']:x for x in ledger if x.get('body_sha256') and x.get('http_status')==200}; candidates=defaultdict(dict);sources={}
 for path in sorted(CACHE.glob('detail-202[56]-*.body.gz')):
  name=path.name.removesuffix('.body.gz');m=by_name.get(name)
  if not m: continue
  b=gzip.decompress(path.read_bytes());assert sha(b)==m['body_sha256']; data=json.loads(b);cs=data.get('compSchedules') or {};rows=[dict(zip(cs.get('keys',[]),v)) for v in cs.get('datas',[])]
  for f in games:
   for r in rows:
    if candidate(f,r):
     key=(name,r.get('matchSeq'));candidates[f['gnum']][key]={'source':str(path),'source_sha256':sha(b),'received_at':m['received_at'],'round':name,'matchSeq':r['matchSeq'],'home_odds':r['winAllot'],'away_odds':r['loseAllot'],'raw_market':{k:r.get(k) for k in ('betId','betTypId','betNm','handi','drawAllot')},'price_observed_at':None}
  sources[name]=sha(b)
 prices=[];exclusions=Counter()
 for f in games:
  c=list(candidates[f['gnum']].values())
  if len(c)==1:prices.append({'official_id':f"001-022-201-{f['gnum']}",**c[0]})
  else:exclusions['no_match_in_cache' if not c else 'multiple_matching_quotes']+=1
 both6=sum(all(len(x['set1_starters'])==6 for x in rec['teams'].values()) for rec in records)
 lib=sum(all(len(x['liberos'])>0 for x in rec['teams'].values()) for rec in records)
 setter=sum(all(any(p['position']=='S' for p in x['set1_starters']) for x in rec['teams'].values()) for rec in records)
 result={'schedule_n':len(games),'period':[games[0]['gdate'],games[-1]['gdate']],'details_n':len(records),'missing_match_numbers':missing,'set1_both_six_n':both6,'both_libero_list_n':lib,'both_starting_setter_n':setter,'strict_pit_n':0,'matched_cached_moneyline_n':len(prices),'price_exclusions':dict(exclusions),'cached_rounds_inspected':len(sources),'price_cache_source_sha256s':sources,'price_cache_ledger_sha256':sha((CACHE/'attempts.json').read_bytes()),'limitations':['Retrospective source received after games; no independently proved pregame availability.','Price cache is incomplete; absence of a matching row in it is not evidence no product was sold.','No independent executable price or predecision price timestamp.','Set rotation order and official pregame absence not established.']}
 (P/'postgame.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in records),encoding='utf8',newline='\n')
 (P/'price-intersection.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in prices),encoding='utf8',newline='\n')
 (P/'coverage.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
 print(json.dumps({k:v for k,v in result.items() if k!='price_cache_source_sha256s'},ensure_ascii=True))
if __name__=='__main__':main()
