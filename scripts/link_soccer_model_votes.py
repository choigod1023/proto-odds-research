import json, hashlib, collections, datetime, re
from pathlib import Path
ROOT=Path('C:/Users/user/Documents/Codex')
ARCH=Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/experiments/tipster-year-20261028')
OUT=Path('experiments/matched-soccer-20261104')
INPUTS={'joined':ARCH/'joined-standard-ot.json','candidates':ARCH/'korean/candidates.jsonl','prices':ROOT/'pregame-compare/experiments/pregame-soccer-20261104/records.jsonl','results':ROOT/'pregame-compare/experiments/pregame-soccer-20261104/season-records.jsonl','k1_predictions':ROOT/'k1model/experiments/kleague1-lineup-challenger-20261103/frozen/predictions.jsonl','k2_predictions':ROOT/'k2model/experiments/kleague2-lineup-challenger-20261103/frozen/test-predictions.jsonl'}
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
 m=max(p); return p.index(m) if p.count(m)==1 else None
def profit(selection,odds,outcome):
 return None if selection is None else round(odds[selection]-1 if selection==outcome else -1,2)
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
  mp=[1/o for o in odds]; mp=[p/sum(mp) for p in mp]; market=pick(mp)
  row={'fixture_id':fid,'kickoff':fixture['kickoff'],'teams':[fixture['home'],fixture['away']],'official_ids':[q['home_team_id'],q['away_team_id']],'price_id':[q['gmId'],q['gmTs'],q['matchSeq']],'odds':odds,'price_sha256':price['body_sha256'],'result_sha256':result['body_sha256'],'score':score,'outcome':outcome,'votes':[{'source':x['source'],'author':x.get('author'),'url':x['source_url'],'sha256':x['source_sha256'],'claim':x.get('published_at_claim'),'observed':x.get('observed_at'),'excerpt':x['evidence_excerpt'],'selection':s} for x,s in group],'vote_counts':counts,'vote_pick':vpick,'market_pick':market,'vote_profit':profit(vpick,odds,outcome),'market_profit':profit(market,odds,outcome),'model_revisions':[],'evidence_tier':'historical_publication_claim_retrospective_price'}
  for name,p,old_price in models[fid]:
   if old_price and (old_price['odds']!=odds or old_price['product_id']!=row['price_id']): exclusions['model_price_conflict']+=1; continue
   model=pick(p); hybrid=pick([.9*p[i]+.1*counts[i]/sum(counts) for i in range(3)])
   row['model_revisions'].append({'name':name,'tier':'postmatch_lineup_oracle','p':p,'model_pick':model,'hybrid_9_model_1_vote_pick':hybrid,'model_profit':profit(model,odds,outcome),'hybrid_profit':profit(hybrid,odds,outcome)})
  row['model_missing_reason']=None if row['model_revisions'] else 'no_matching_preserved_test_prediction_or_price_conflict'
  output.append(row)
 summary={'input_sha256':{k:digest(p) for k,p in INPUTS.items()},'candidate_count':len(candidates),'joined_all_rows':len(archive),'kleague_joined_rows':len(audit),'exclusions':dict(exclusions),'diagnostic_events':len(output),'price_rows':len(output),'model_events':sum(bool(r['model_revisions']) for r in output),'model_revision_rows':sum(len(r['model_revisions']) for r in output),'months':dict(collections.Counter(r['kickoff'][:7] for r in output)),'strict_pregame_comparison_events':0,'network_requests':0}
 (OUT/'candidate-audit.json').write_text(json.dumps(candidate_audit,ensure_ascii=False,indent=2),encoding='utf8'); (OUT/'rows.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf8'); (OUT/'source-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf8'); (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__': run()
