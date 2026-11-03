"""Read-only audit of every finalized 2026 actual-starter verification row."""
import base64,collections,gzip,hashlib,json
from functools import lru_cache
from pathlib import Path
import collect_lineups as c
H=Path(__file__).resolve().parent

def digest(b):return hashlib.sha256(b).hexdigest()
def rows(p):
 with gzip.open(p,'rt',encoding='utf8') as f:return list(map(json.loads,f))
def main():
 manifest=json.loads((H/'actual-starters-2026-manifest.json').read_text(encoding='utf8'));p=H/'actual-starters-2026.jsonl.gz';rp=H/'actual-starters-2026-raw.jsonl.gz'
 assert digest(p.read_bytes())==manifest['sha256']['records'] and digest(rp.read_bytes())==manifest['sha256']['raw']
 evidence={}
 for x in rows(rp):
  raw=base64.b64decode(x['raw_base64'],validate=True);assert digest(raw)==x['sha256'] and len(raw)==x['bytes'] and x['sha256'] not in evidence;evidence[x['sha256']]=raw
 data=rows(p);assert len(data)==709 and len({x['fixture_id'] for x in data})==709 and len(evidence)==manifest['raw_response_count'];counts=collections.Counter();profile=lru_cache(8192)(c.profile_name)
 def get(meta,url=None):
  assert meta['http_status']==200 and meta['url'].startswith('https://www.koreabaseball.com/')
  if url:assert meta['url']==url
  raw=evidence[meta['sha256']];assert len(raw)==meta['bytes'];return raw
 for x in data:
  assert x['season']==2026 and x['historical_receipt_proven'] is False
  if x['status']!='complete':assert x['exclusions'];counts['excluded']+=1;continue
  counts['complete']+=1;gid=x['official_game_id'];sm=x['source_evidence']['schedule'];bm=x['source_evidence']['actual_pitcher_record'];assert bm['params']['gameId']==gid
  schedule=json.loads(get(sm,c.SCHEDULE));found=[g for g in schedule['game'] if g['G_ID']==gid];assert len(found)==1;game=found[0]
  assert game['G_DT']==x['date'].replace('-','') and str(game['SEASON_ID'])=='2026' and str(game['GAME_RESULT_CK'])=='1' and str(game['HOME_ID'])==str(x['teams']['home']) and str(game['AWAY_ID'])==str(x['teams']['away'])
  box=json.loads(get(bm,c.ROOT+'/ws/Schedule.asmx/GetBoxScore'));assert str(box['code'])=='100' and len(box['tables'])==5
  for side,i,prefix in [('away',3,'T'),('home',4,'B')]:
   player=x['starters'][side];assert player['actual_record_verified'] is True;t=box['tables'][i];heads=[z['Text'].strip() for z in t['headers'][0]['row']];ni=heads.index('선수명');ai=heads.index('등판');starts=[[z['Text'].strip() for z in v['row']] for v in t['rows'] if v['row'][ai]['Text'].strip()=='선발'];assert len(starts)==1 and t['rows'][0]['row'][ai]['Text'].strip()=='선발';claim=starts[0][ni];ev=player['evidence'];assert claim==ev['box_row_player_field']
   if ev['schedule_matches_actual']:assert player['player_id']==str(game[prefix+'_PIT_P_ID']) and player['name']==str(game[prefix+'_PIT_P_NM']).strip() and claim in (player['player_id'],player['name'])
   else:
    pe=ev['profile'];assert str(pe['params']['playerId'])==player['player_id'] and profile(get(pe))==player['name'] and claim in (player['player_id'],player['name']);counts['schedule_different']+=1
   counts['starter_checks']+=1
 assert counts['complete']==manifest['counts']['complete'];print(json.dumps({'status':'ok',**counts,'raw_response_count':len(evidence)}))
if __name__=='__main__':main()
