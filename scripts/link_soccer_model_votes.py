import json, hashlib, collections, datetime, math, gzip, time, urllib.request, urllib.error, sys, re, html
from pathlib import Path
ROOT=Path('C:/Users/user/Documents/Codex')
ARCH=Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/experiments/tipster-year-20261028')
OUT=Path('experiments/matched-soccer-20261104')
INPUTS={'joined':ARCH/'joined-standard-ot.json','candidates':ARCH/'korean/candidates.jsonl','prices':ROOT/'pregame-compare/experiments/pregame-soccer-20261104/records.jsonl','results':ROOT/'pregame-compare/experiments/pregame-soccer-20261104/season-records.jsonl','k1_predictions':ROOT/'k1model/experiments/kleague1-lineup-challenger-20261103/frozen/predictions.jsonl','k2_predictions':ROOT/'k2model/experiments/kleague2-lineup-challenger-20261103/frozen/test-predictions.jsonl'}
INVENTORY=ARCH/'korean/sportsanalysis1_sitemap_inventory.json'
ALIASES={'서울이랜드':'서울E','경남FC':'경남','광주FC':'광주','인천유나이티드':'인천','전북현대':'전북','울산HD':'울산','포항스틸러스':'포항','대전하나':'대전','강원FC':'강원','수원FC':'수원FC','화성FC':'화성','성남FC':'성남','부산아이파크':'부산','FC서울':'서울','FC안양':'안양','충남아산':'충남아산','안산그리너스':'안산','김천상무':'김천','제주SK':'제주','김포FC':'김포','충북청주':'충북청주'}
def lines(p):
 with p.open(encoding='utf8') as f: return [json.loads(s) for s in f if s.strip()]
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def select_vote(x, fixture):
 if x.get('market')!='moneyline': return None,'other_market'
 if x.get('within_article_conflicting_directions'): return None,'conflict'
 excerpt=x.get('evidence_excerpt','')
 if '또는' in excerpt or '컵 |' in excerpt or ('무승부' in excerpt and ' 승' in excerpt): return None,'ambiguous_or_cup'
 teams=x.get('teams',[])
 if len(teams)!=2 or any(t not in ALIASES for t in teams): return None,'unmapped_team'
 mapped=[ALIASES[t] for t in teams]
 if set(mapped)!={fixture['home'],fixture['away']}: return None,'fixture_team_mismatch'
 side=x.get('side')
 if side=='draw': return 1,None
 selected=ALIASES.get(x.get('selected_team'))
 if selected not in mapped: return None,'unmapped_selection'
 return (0 if selected==fixture['home'] else 2),None
def pick(p):
 m=max(p); winners=[i for i,v in enumerate(p) if math.isclose(v,m,rel_tol=0,abs_tol=1e-12)]
 return winners[0] if len(winners)==1 else None
def blend(base, counts):
 total=sum(counts)
 if not total: raise ValueError('vote required; no fallback')
 return [.9*base[i]+.1*counts[i]/total for i in range(3)]
def profit(selection,odds,outcome):
 return None if selection is None else round(odds[selection]-1 if selection==outcome else -1,2)
def metrics(rows, pick_key, profit_key, model=False):
 entries=[r['model_revisions'][0] if model else r for r in rows]
 settled=[(r,e) for r,e in zip(rows,entries) if e[pick_key] is not None]
 net=round(sum(e[profit_key] for _,e in settled),2)
 return {'events':len(rows),'bets':len(settled),'abstentions':len(rows)-len(settled),'hits':sum(e[pick_key]==r['outcome'] for r,e in settled),'net_units':net,'roi':round(net/len(settled),6) if settled else None}
def summarize(rows, model=False):
 specs={'vote':('vote_pick','vote_profit',False),'market':('market_pick','market_profit',False),'market90_vote10':('market90_vote10_pick','market90_vote10_profit',False)}
 if model: specs.update({'postgame_roster_oracle':('model_pick','model_profit',True),'postgame_roster_oracle90_vote10':('oracle90_vote10_pick','oracle90_vote10_profit',True)})
 return {name:metrics(rows,*spec) for name,spec in specs.items()}
def recover_2025():
 """Read the existing 2025 sitemap candidates, cache raw bodies, stop on error."""
 target=OUT/'recovery-2025'; target.mkdir(exist_ok=True,parents=True)
 if (target/'manifest.json').exists():
  previous=json.loads((target/'manifest.json').read_text(encoding='utf8'))
  if any(r.get('status') in (403,429) for r in previous['records']): raise RuntimeError('previous access restriction; no retry')
 inventory=json.loads(INVENTORY.read_text(encoding='utf8'))
 selected=[x for x in inventory if 'K리그' in x.get('title_hint','') and x.get('sitemap_lastmod_claim','').startswith('2025')]
 manifest=[]; requests=0
 for x in selected:
  key=hashlib.sha256(x['url'].encode()).hexdigest()[:20]; raw=target/(key+'.body.gz'); meta=target/(key+'.json')
  if raw.exists() and meta.exists():
   record=json.loads(meta.read_text(encoding='utf8'))
   if hashlib.sha256(gzip.decompress(raw.read_bytes())).hexdigest()!=record['body_sha256']: raise ValueError('cache hash mismatch')
   manifest.append(record); continue
  try:
   requests+=1
   req=urllib.request.Request(x['url'],headers={'User-Agent':'Mozilla/5.0 (compatible; research archive)'})
   with urllib.request.urlopen(req,timeout=15) as response:
    body=response.read(3000001)
    finished=datetime.datetime.now(datetime.timezone.utc).isoformat()
    if len(body)>3000000: raise ValueError('body too large')
    status=response.status
   record={'url':x['url'],'title_hint':x['title_hint'],'sitemap_lastmod_claim':x['sitemap_lastmod_claim'],'received_at':finished,'timestamp_basis':'http_response_body_completed','body_sha256':hashlib.sha256(body).hexdigest(),'status':status,'evidence_tier':'retrospective'}
   raw.write_bytes(gzip.compress(body,mtime=0)); meta.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8'); manifest.append(record)
   time.sleep(.6)
  except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,ValueError) as e:
   manifest.append({'url':x['url'],'error':str(e),'status':getattr(e,'code',None),'stopped':True}); break
 result={'inventory_sha256':digest(INVENTORY),'eligible_urls':len(selected),'new_requests':requests,'records':manifest}
 (target/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8'); print(json.dumps(result,ensure_ascii=False,indent=2))
def join_recovered_2025():
 # Explicit primary 1X2 statement, exact fixture IDs, and exact source phrases.
 curated={
  'kleague:2025:1:127':('수원FC vs 광주FC','최종 결론은 광주FC의 승리',2),
  'kleague:2025:2:137':('천안시티 FC vs 화성 FC',"최우선 추천: 이 경기의 가장 합리적이고 안정적인 베팅은 '천안시티 승'",0),
  'kleague:2025:2:139':('부천 vs 김포','주력 베팅: 부천 FC 1995 일반 승리',0),
  'kleague:2025:2:133':('김포 vs 부산','주력 베팅: 김포 FC 승리',0),
 }
 target=OUT/'recovery-2025'; manifest=json.loads((target/'manifest.json').read_text(encoding='utf8'))
 def indexed(path,kind):
  d=collections.defaultdict(list)
  for r in lines(path):
   if r['kind']==kind: d[r['payload']['source_fixture_id']].append(r)
  return d
 fixtures=indexed(INPUTS['results'],'fixture'); results=indexed(INPUTS['results'],'result'); prices=indexed(INPUTS['prices'],'price')
 rows=[]; review=[]
 for rec in manifest['records']:
  if 'body_sha256' not in rec: review.append({**rec,'reason':'fetch_failed'}); continue
  key=hashlib.sha256(rec['url'].encode()).hexdigest()[:20]; body=gzip.decompress((target/(key+'.body.gz')).read_bytes())
  if hashlib.sha256(body).hexdigest()!=rec['body_sha256']: raise ValueError('body hash mismatch')
  content=body.decode('utf8','replace'); content=html.unescape(re.sub(r'<[^>]+>',' ',content)); content=' '.join(content.split())
  matches=[(fid,phrase,side) for fid,(title,phrase,side) in curated.items() if title in rec['title_hint']]
  if not matches: review.append({'url':rec['url'],'sha256':rec['body_sha256'],'reason':'no_curated_primary_1x2_selection_held'}); continue
  if len(matches)!=1: raise ValueError('ambiguous curated title')
  fid,phrase,side=matches[0]
  if phrase not in content: raise ValueError(f'missing source phrase {fid}')
  if any(len(x[fid])!=1 for x in (fixtures,results,prices)): review.append({'fixture_id':fid,'reason':'official_fixture_price_result_not_unique'}); continue
  f=fixtures[fid][0]; result=results[fid][0]; price=prices[fid][0]; q=price['payload']; score=result['payload']['score']
  if score!=q['score_crosscheck']: raise ValueError('official result conflict')
  original=body.decode('utf8','replace'); match=re.search(r'<meta property="article:published_time" content="([^"]+)"',original)
  if not match or datetime.datetime.fromisoformat(match.group(1))>=datetime.datetime.fromisoformat(f['kickoff']): review.append({'fixture_id':fid,'reason':'publication_claim_not_before_kickoff'}); continue
  odds=[q['prices'][k] for k in ('home','draw','away')]; outcome=0 if score[0]>score[1] else 2 if score[0]<score[1] else 1
  mp=[1/o for o in odds]; mp=[v/sum(mp) for v in mp]; counts=[int(i==side) for i in range(3)]; market=pick(mp); hybrid=pick(blend(mp,counts)); start=content.index(phrase)
  rows.append({'fixture_id':fid,'league':f['league'],'kickoff':f['kickoff'],'teams':[f['home'],f['away']],'official_ids':[q['home_team_id'],q['away_team_id']],'source_url':rec['url'],'source_sha256':rec['body_sha256'],'source_excerpt':content[start:start+180],'publication_claim':match.group(1),'observed_at':rec['received_at'],'author':'unverified_single_source','price_id':[q['gmId'],q['gmTs'],q['matchSeq']],'price_sha256':price['body_sha256'],'result_sha256':result['body_sha256'],'odds':odds,'score':score,'outcome':outcome,'vote_counts':counts,'vote_pick':side,'market_pick':market,'market90_vote10_pick':hybrid,'vote_profit':profit(side,odds,outcome),'market_profit':profit(market,odds,outcome),'market90_vote10_profit':profit(hybrid,odds,outcome),'evidence_tier':'retrospective_publication_claim_only','model_missing_reason':'no_preserved_2025_test_prediction'})
 report={'market_basis':'multiplicative_normalized_inverse_price_not_shin','manifest_sha256':digest(target/'manifest.json'),'input_sha256':{k:digest(INPUTS[k]) for k in ('prices','results')},'eligible_source_documents':len(manifest['records']),'joined_events':len(rows),'held_documents':len(review),'controls':summarize(rows),'rows':rows,'held':review}
 (target/'joined.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8'); print(json.dumps(report,ensure_ascii=False,indent=2))
def run():
 OUT.mkdir(exist_ok=True,parents=True)
 archive=json.loads(INPUTS['joined'].read_text(encoding='utf8')); candidates=lines(INPUTS['candidates'])
 price_rows=[x for x in lines(INPUTS['prices']) if x['kind']=='price']; result_rows=[x for x in lines(INPUTS['results']) if x['kind']=='result']; fixture_rows=[x for x in lines(INPUTS['results']) if x['kind']=='fixture']
 def unique(rows):
  d=collections.defaultdict(list)
  for x in rows:d[x['payload']['source_fixture_id']].append(x)
  return d
 prices,results,fixtures=map(unique,(price_rows,result_rows,fixture_rows))
 models=collections.defaultdict(list)
 for x in lines(INPUTS['k1_predictions']): models[x['fixture_id']].append(('k1_lineup',x['lineup_p'],x['price']))
 for x in lines(INPUTS['k2_predictions']): models[x['fixture_id']].append(('k2_lineup',x['models']['lineup']['p'],None))
 exclusions=collections.Counter(); votes=collections.defaultdict(list); audit=[]
 for x in archive:
  fid=x.get('fixture_id','')
  if not str(fid).startswith('kleague:'): continue
  row={'fixture_id':fid,'source_sha256':x.get('source_sha256'),'url':x.get('source_url'),'excerpt':x.get('evidence_excerpt'),'published_at_claim':x.get('published_at_claim'),'observed_at':x.get('observed_at')}
  if len(fixtures[fid])!=1: reason='fixture_missing_or_ambiguous'; sel=None
  else:
   fixture=fixtures[fid][0]; sel,reason=select_vote(x,fixture)
   try:
    if not reason and datetime.datetime.fromisoformat(x['published_at_claim'])>=datetime.datetime.fromisoformat(fixture['kickoff']): reason='publication_not_before_kickoff'
   except (TypeError,ValueError): reason=reason or 'publication_missing'
  row.update(selection=sel,exclusion=reason); audit.append(row)
  if reason: exclusions[reason]+=1; continue
  votes[fid].append((x,sel))
 joined_keys={(x.get('source_sha256'),x.get('evidence_excerpt')) for x in archive if str(x.get('fixture_id','')).startswith('kleague:')}
 candidate_audit=[{'source_sha256':x.get('source_sha256'),'league':x.get('league'),'match_date':x.get('match_date'),'excerpt':x.get('evidence_excerpt'),'status':'joined_kleague_fixture' if (x.get('source_sha256'),x.get('evidence_excerpt')) in joined_keys else 'not_joined_kleague_fixture_or_other_sport'} for x in candidates]
 output=[]
 for fid,group in sorted(votes.items()):
  if len(prices[fid])!=1 or len(results[fid])!=1: exclusions['price_or_result_not_unique']+=1; continue
  price=prices[fid][0]; result=results[fid][0]; fixture=fixtures[fid][0]; q=price['payload']; score=result['payload']['score']; outcome=0 if score[0]>score[1] else 2 if score[0]<score[1] else 1; odds=[q['prices'][k] for k in ('home','draw','away')]
  if score!=q['score_crosscheck']: exclusions['official_score_conflict']+=1; continue
  # One author per event; conflicting directions by the same author are held.
  by_author=collections.defaultdict(set)
  for x,s in group: by_author[(x['source'],x.get('author'))].add(s)
  if any(len(v)!=1 for v in by_author.values()): exclusions['author_conflict']+=1; continue
  counts=[sum(next(iter(v))==i for v in by_author.values()) for i in range(3)]; vpick=pick(counts)
  if vpick is None: exclusions['vote_tie']+=1; continue
  mp=[1/o for o in odds]; mp=[p/sum(mp) for p in mp]; market=pick(mp); market_vote=pick(blend(mp,counts))
  row={'fixture_id':fid,'kickoff':fixture['kickoff'],'teams':[fixture['home'],fixture['away']],'official_ids':[q['home_team_id'],q['away_team_id']],'price_id':[q['gmId'],q['gmTs'],q['matchSeq']],'odds':odds,'price_sha256':price['body_sha256'],'result_sha256':result['body_sha256'],'score':score,'outcome':outcome,'votes':[{'source':x['source'],'author':x.get('author'),'url':x['source_url'],'sha256':x['source_sha256'],'claim':x.get('published_at_claim'),'observed':x.get('observed_at'),'excerpt':x['evidence_excerpt'],'selection':s} for x,s in group],'vote_counts':counts,'vote_pick':vpick,'market_pick':market,'vote_profit':profit(vpick,odds,outcome),'market_profit':profit(market,odds,outcome),'model_revisions':[],'evidence_tier':'historical_publication_claim_retrospective_price'}
  row.update(league=fixture['league'],year=int(fixture['kickoff'][:4]),market_p=mp,market90_vote10_p=blend(mp,counts),market90_vote10_pick=market_vote,market90_vote10_profit=profit(market_vote,odds,outcome))
  for name,p,old_price in models[fid]:
   if old_price and (old_price['odds']!=odds or old_price['product_id']!=row['price_id']): exclusions['model_price_conflict']+=1; continue
   model=pick(p); hybrid=pick(blend(p,counts))
   row['model_revisions'].append({'name':name,'tier':'postgame_roster_oracle','p':p,'model_pick':model,'oracle90_vote10_pick':hybrid,'model_profit':profit(model,odds,outcome),'oracle90_vote10_profit':profit(hybrid,odds,outcome)})
  row['model_missing_reason']=None if row['model_revisions'] else 'no_matching_preserved_test_prediction_or_price_conflict'
  output.append(row)
 matched=[r for r in output if r['model_revisions']]
 def grouped(rows,model):
  groups=collections.defaultdict(list)
  for r in rows: groups[(r['kickoff'][:7],r['league'])].append(r)
  return {f'{month}/{league}':summarize(rs,model) for (month,league),rs in sorted(groups.items())}
 summary={'market_basis':'multiplicative_normalized_inverse_price_not_shin','input_sha256':{k:digest(p) for k,p in INPUTS.items()},'candidate_count':len(candidates),'joined_all_rows':len(archive),'kleague_joined_rows':len(audit),'exclusions':dict(exclusions),'diagnostic_events':len(output),'price_rows':len(output),'model_events':len(matched),'model_revision_rows':sum(len(r['model_revisions']) for r in output),'controls_23':summarize(output),'controls_23_by_month_league':grouped(output,False),'oracle_intersection':summarize(matched,True),'oracle_intersection_by_month_league':grouped(matched,True),'date_range':[min(r['kickoff'] for r in output),max(r['kickoff'] for r in output)],'strict_pregame_comparison_events':0,'network_requests':0}
 (OUT/'candidate-audit.json').write_text(json.dumps(candidate_audit,ensure_ascii=False,indent=2),encoding='utf8'); (OUT/'rows.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf8'); (OUT/'source-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8'); (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':
 if sys.argv[1:]==['--recover-2025']: recover_2025()
 elif sys.argv[1:]==['--join-recovered-2025']: join_recovered_2025()
 elif not sys.argv[1:]: run()
 else: raise SystemExit('usage: link_soccer_model_votes.py [--recover-2025|--join-recovered-2025]')
