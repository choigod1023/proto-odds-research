"""Recheck the completed 2023 lineup checkpoint against official captured bytes."""
import collections,json
from functools import lru_cache
from pathlib import Path
import collect_lineups as c
from build_rosters_2023 import CACHE,ROWS,BAT,fixed_file
from collect_actual_starters import evidence

def main():
 rows={x['fixture_id']:x for x in map(json.loads,fixed_file(ROWS,'66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c').splitlines()) if x['season']==2023};archive=json.loads(fixed_file(BAT,'24bdea9094046bdab4967d314c043d548a252599b652009d5ffdabd341cc84e8'));lines=[json.loads(x) for x in (CACHE/'official-lineups-2023-final.jsonl').read_text(encoding='utf8').splitlines()];assert len(lines)==723 and {x['fixture_id'] for x in lines}==set(rows);counts=collections.Counter();gids=set();name=lru_cache(8192)(c.profile_name)
 for rec in lines:
  fid=rec['fixture_id'];row=rows[fid];counts['population']+=1
  if rec.get('lineup_status')!='official_flag_true_profile_name_crossmatched':counts['unresolved']+=1;continue
  sm=rec['schedule_evidence'];sched=json.loads(evidence(CACHE,sm));matches=[g for g in sched['game'] if g['G_ID']==rec['official_game_id']];assert len(matches)==1;game=matches[0]
  assert game['G_ID'] not in gids;gids.add(game['G_ID']);assert game['G_DT']==row['kickoff_at'][:10].replace('-','') and (game['HOME_NM'].strip(),game['AWAY_NM'].strip())==(row['home_id'],row['away_id'])
  assert str(game['HEADER_NO'])==str(rec['header_no']) and str(game['SR_ID'])==str(rec['series_id'])
  actual=c.parse_lineup(evidence(CACHE,rec['lineup_evidence']),game);assert actual is not None;seen=set()
  for side in ('home','away'):
   for saved,official in zip(sorted(rec['lineups'][side],key=lambda p:p['order']),actual[side]):
    assert (saved['order'],saved['name'])==(official['order'],official['name']);pid=saved['player_id'];assert pid not in seen;seen.add(pid);ev=saved['official_profile_evidence'];assert str(ev['params']['playerId'])==pid and name(evidence(CACHE,ev))==saved['name'];candidates=[z for z in archive[fid]['data'][side] if str(z.get('code'))==pid and z.get('order')==saved['order']];assert candidates;counts['hitter_identities']+=1
  assert len(seen)==18;counts['validated']+=1;counts['year_alias']+=int(rec['fixture_match_basis']=='encoded_year_alias_exact_official_schedule_match');counts['kickoff_conflict']+=int(bool(rec.get('kickoff_conflict')))
 print(json.dumps({'status':'ok',**counts},ensure_ascii=False))
if __name__=='__main__':main()
