"""Independent read-only byte and identity audit of finalized 2023 roster artifact."""
import base64,gzip,hashlib,json,collections
from functools import lru_cache
from pathlib import Path
import collect_lineups as c
H=Path(__file__).resolve().parent

def digest(raw):return hashlib.sha256(raw).hexdigest()
def rows(path):
 with gzip.open(path,'rt',encoding='utf8') as f:return list(map(json.loads,f))
def main():
 manifest=json.loads((H/'actual-rosters-2023-manifest.json').read_text(encoding='utf8'));p=H/'actual-rosters-2023.jsonl.gz';bundle=H/'actual-rosters-2023-raw.jsonl.gz'
 assert digest(p.read_bytes())==manifest['rows_sha256'] and digest(bundle.read_bytes())==manifest['raw_evidence_bundle_sha256']
 data=rows(p);evidence={}
 for x in rows(bundle):
  raw=base64.b64decode(x['raw_base64'],validate=True);assert digest(raw)==x['sha256'] and len(raw)==x['bytes'];assert x['sha256'] not in evidence;evidence[x['sha256']]=raw
 assert len(data)==723 and len({x['fixture_id'] for x in data})==723 and len(evidence)==manifest['official_raw_response_count']
 def get(meta,url=None):
  assert meta['http_status']==200 and meta['url'].startswith('https://www.koreabaseball.com/')
  if url:assert meta['url']==url
  raw=evidence[meta['sha256']];assert len(raw)==meta['bytes'];return raw
 profile=lru_cache(8192)(c.profile_name);counts=collections.Counter();ids=set()
 for x in data:
  assert x['season']==2023 and x['historical_receipt_proven'] is False and x['target_player_stats_used_as_features'] is False
  if x['status']!='complete':assert x['exclusions'];counts['excluded']+=1;continue
  counts['complete']+=1;gid=x['official_game_id'];assert gid not in ids;ids.add(gid)
  home=x['starters']['home'];away=x['starters']['away'];sm=home['evidence']['schedule'];assert away['evidence']['schedule']==sm
  sched=json.loads(get(sm,c.SCHEDULE));matches=[g for g in sched['game'] if g['G_ID']==gid];assert len(matches)==1;game=matches[0]
  assert game['G_DT']==x['date'].replace('-','') and str(game['SEASON_ID'])=='2023' and str(game['GAME_RESULT_CK'])=='1' and (game['HOME_NM'].strip(),game['AWAY_NM'].strip())==(x['teams']['home'],x['teams']['away'])
  lm=x['hitters']['home'][0]['evidence']['lineup'];official=c.parse_lineup(get(lm,c.LINEUP),game);assert official
  for side in ('home','away'):
   chosen=x['hitters'][side];assert len(chosen)==9 and [q['slot'] for q in chosen]==list(range(1,10)) and [q['name'] for q in chosen]==[q['name'] for q in official[side]]
   for q in chosen:
    ev=q['evidence'];assert ev['lineup']==lm and ev['archive_identity_only'] is True and ev['archive_fixture_id']==x['fixture_id'];assert profile(get(ev['profile'],c.PROFILE))==q['name'] and str(ev['profile']['params']['playerId'])==q['player_id'];counts['hitter_id_checks']+=1
  allhit=[q['player_id'] for s in ('home','away') for q in x['hitters'][s]];assert len(set(allhit))==18
  bm=home['evidence']['official_record'];assert away['evidence']['official_record']==bm and bm['params']['gameId']==gid
  box=json.loads(get(bm,c.ROOT+'/ws/Schedule.asmx/GetBoxScore'));assert str(box['code'])=='100' and len(box['tables'])==5
  for side,i,prefix in [('away',3,'T'),('home',4,'B')]:
   player=x['starters'][side];assert player['actual_record_verified'] is True;t=box['tables'][i];headers=[z['Text'].strip() for z in t['headers'][0]['row']];ni=headers.index('선수명');ai=headers.index('등판');starts=[[z['Text'].strip() for z in v['row']] for v in t['rows'] if v['row'][ai]['Text'].strip()=='선발'];assert len(starts)==1 and t['rows'][0]['row'][ai]['Text'].strip()=='선발';claim=starts[0][ni];ev=player['evidence'];assert claim==ev['box_row_player_field']
   if ev['schedule_matches_actual']:
    assert player['player_id']==str(game[prefix+'_PIT_P_ID']) and player['name']==str(game[prefix+'_PIT_P_NM']).strip() and claim in (player['player_id'],player['name'])
   else:
    meta=ev['profile'];assert str(meta['params']['playerId'])==player['player_id'] and profile(get(meta))==player['name'] and (claim==player['name'] or claim==player['player_id']);counts['schedule_different']+=1
   counts['starter_id_checks']+=1
 assert counts['complete']==manifest['counts']['actual_complete'];print(json.dumps({'status':'ok',**counts,'raw_response_count':len(evidence)}))
if __name__=='__main__':main()
