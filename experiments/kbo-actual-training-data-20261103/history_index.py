"""Construct prior-day complete player histories from pinned public research bytes."""
from collections import Counter,defaultdict
from paired_core import valid_batter,valid_pitcher

def build_old(games,batters,pitchers,innings_outs):
 batting=defaultdict(list);pitching=defaultdict(list)
 for game in sorted(games,key=lambda x:(x['date'],x['source_fixture_id'])):
  fid=game['source_fixture_id'];b=batters.get(fid);p=pitchers.get(fid)
  for box in (b,p):
   if box and (box.get('date'),box.get('home'),box.get('away'))!=(game['date'],game['home'],game['away']):raise ValueError('prior_box_identity_mismatch')
  for side in ('home','away'):
   for x in ((b or {}).get('data') or {}).get(side,[]):
    h={'fixture_id':fid,'date':game['date'],**{k:x.get(k) for k in ('ab','hit','bb','hr')}}
    if x.get('code') and valid_batter(h):batting[str(x['code'])].append(h)
   for x in ((p or {}).get('data') or {}).get(side,[]):
    h={'fixture_id':fid,'date':game['date'],'outs':innings_outs(x.get('inn')),**{k:x.get(k) for k in ('r','er','hit','bb','hr','kk')},'source_bf_raw':x.get('bf'),'source_bf_units':'unverified'}
    if x.get('pcode') and valid_pitcher(h):pitching[str(x['pcode'])].append(h)
 return batting,pitching

def evidence(x):return {k:x[k] for k in ('url','sha256','received_at')}
def build_fresh_pitch(games,pitchers,innings_outs,v1,v2):
 result=defaultdict(list)
 for game in sorted(games,key=lambda x:(x['date'],x['source_fixture_id'])):
  fid=game['source_fixture_id'];day=game['date']
  if day<='2026-07-26':
   p=pitchers.get(fid)
   if p and (p.get('date'),p.get('home'),p.get('away'))!=(day,game['home'],game['away']):raise ValueError('prior_pitcher_box_identity')
   for side in ('home','away'):
    for x in ((p or {}).get('data') or {}).get(side,[]):
     h={'fixture_id':fid,'date':day,'outs':innings_outs(x.get('inn')),'er':x.get('er'),'r':x.get('r')}
     if x.get('pcode') and valid_pitcher(h):result[str(x['pcode'])].append(h)
  elif fid in v1:
   r=v1[fid];supp=v2[fid]
   if r['game_date']!=day.replace('-','') or r['official_game_id']!=supp['official_game_id']:raise ValueError('fresh_pitcher_fixture_mismatch')
   for side in ('home','away'):
    old=r['statistics']['pitchers'][side];new=supp['pitcher_id_supplement'][side]
    if len(old)!=len(new):raise ValueError('fresh_pitcher_row_count')
    pairs=[]
    for i,(x,y) in enumerate(zip(old,new)):
     if y['v1_row_index']!=i or y['name']!=x['name'] or y['original_player_id']!=x['player_id'] or y['source_numeric_id_claim']!=x['source_numeric_id_claim']:raise ValueError('fresh_pitcher_row_identity')
     pid=x['player_id'];profile=None
     if y['status']=='verified_official_profile':
      pid=y['supplement_player_id'];profile=evidence(y['profile_evidence'])
      if not pid or pid!=x['source_numeric_id_claim'] or str(y['profile_evidence'].get('query',{}).get('playerId'))!=pid:raise ValueError('fresh_pitcher_profile_mismatch')
     elif y['status'] not in ('already_linked','no_official_numeric_claim'):raise ValueError('fresh_pitcher_status')
     pairs.append((x,pid,profile))
    duplicates={pid for pid,n in Counter(pid for _,pid,_ in pairs if pid).items() if n>1}
    for x,pid,profile in pairs:
     h={'fixture_id':fid,'date':day,'outs':x['outs'],'er':x['ER'],'r':x['R'],'provenance':{'box':evidence(r['evidence']['box'])}}
     if profile:h['provenance']['profile']=profile
     if pid and pid not in duplicates and valid_pitcher(h):result[pid].append(h)
 return result
