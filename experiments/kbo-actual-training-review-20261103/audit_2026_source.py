"""Audit 2026 starter source, using only pinned Git objects and raw responses."""
import base64,collections,gzip,hashlib,json,subprocess
from pathlib import Path
DATA=Path(r'C:\Users\user\Documents\Codex\kbtdata');BASE=Path(r'C:\Users\user\Documents\Codex\kbodata');LINE=Path(r'C:\Users\user\Documents\Codex\klu')
C='e6068b83e1b076f0aa24a71a33a77930d4c09f2c';B='13291d23e855e0d500327cb39ac64a480e395837';L='b24739cf52435070586f8687011fd473a16123e4';P='experiments/kbo-actual-training-data-20261103/'
def git(repo,commit,path):return subprocess.check_output(['git','-C',str(repo),'show',commit+':'+path])
def sha(b):return hashlib.sha256(b).hexdigest()
def items(b):return [json.loads(x) for x in gzip.decompress(b).splitlines()]
def require(x,msg):
 if not x:raise ValueError(msg)
def cells(row):return [str(x.get('Text') or '').strip() for x in row['row']]
def main():
 rec=git(DATA,C,P+'actual-starters-2026.jsonl.gz');bundle=git(DATA,C,P+'actual-starters-2026-raw.jsonl.gz')
 require(sha(rec)=='ca1d6dbc1f301ee4681237cbf1cd864df5079a3ab9364c2f7b50129a9574a69d' and sha(bundle)=='1203fd23f085c56937273a5a42c0780ed703369b4b715fededc0a84a82e6e786','bundle digest')
 manifest=json.loads(git(DATA,C,P+'actual-starters-2026-manifest.json'));require(manifest['source_cohort_commit']==B and manifest['source_official_commit']==L,'manifest commit')
 rr=items(rec);raws={}
 for x in items(bundle):
  data=base64.b64decode(x['raw_base64'],validate=True);require(x['sha256'] not in raws and sha(data)==x['sha256'] and len(data)==x['bytes'],'raw digest');raws[x['sha256']]=data
 groups=json.loads(git(BASE,B,'experiments/kbo-player-data-20261103/official-full-2026-cohorts.jsonl'));ids=groups['all']
 original=[json.loads(x) for x in git(LINE,L,'experiments/kbo-official-lineups-20261103/official-lineups-2026.jsonl').splitlines()];line={x['fixture_id']:x for x in original}
 require(len(ids)==len(set(ids))==len(rr)==709 and set(ids)=={x['fixture_id'] for x in rr} and len({x['official_game_id'] for x in rr})==709,'cohort mismatch')
 used=set();counts=collections.Counter()
 def evidence(m,url):
  require(m['url']=='https://www.koreabaseball.com/'+url and m['method']=='POST' and m['http_status']==200 and bool(m['received_at']),'request metadata')
  data=raws[m['sha256']];require(len(data)==m['bytes'],'bytes');used.add(m['sha256']);return json.loads(data)
 for r in rr:
  fid=r['fixture_id'];gid=r['official_game_id'];x=line[fid];s=r['source_evidence'];sm=s['schedule'];bm=s['actual_pitcher_record']
  require(r['season']==2026 and r['status']=='complete' and r['exclusions']==[] and r['historical_receipt_proven'] is False,'status '+fid)
  require(x['game_date']==r['date'] and x['official_game_id']==gid and x['historical_publication_time_proven'] is False and x['historical_receipt_proven'] is False and x['eligible_as_pregame_feature'] is False,'lineup metadata '+fid)
  require(all(x[side+'_team_id']==r['teams'][side] for side in ('away','home')),'team metadata '+fid)
  require(str(sm['params']['date'])==r['date'] and str(bm['params']['gameId'])==gid and int(bm['params']['leId'])==1 and int(bm['params']['seasonId'])==2026,'request game '+fid)
  games=evidence(sm,'ws/Main.asmx/GetKboGameList')['game'];matches=[g for g in games if g['G_ID']==gid];require(len(matches)==1,'schedule '+fid);g=matches[0]
  require(str(g['G_DT'])==r['date'] and int(g['SEASON_ID'])==2026 and int(g['LE_ID'])==1 and str(g['SR_ID'])==str(bm['params']['srId']) and str(g['GAME_STATE_SC'])=='3' and str(g['GAME_RESULT_CK'])=='1','schedule metadata '+fid)
  require(all(g[side.upper()+'_ID']==r['teams'][side] for side in ('home','away')),'schedule teams '+fid)
  box=evidence(bm,'ws/Schedule.asmx/GetBoxScore');require(str(box.get('code'))=='100' and len(box['tables'])==5,'box envelope '+fid)
  starters=[];hitterids=[]
  for side,i,pre in (('away',3,'T'),('home',4,'B')):
   table=box['tables'][i];heads=cells(table['headers'][0]);require(heads.count('선수명')==heads.count('등판')==1,'pitch headers '+fid)
   ni=heads.index('선수명');ai=heads.index('등판');t=[cells(z) for z in table['rows']];starts=[z for z in t if z[ai]=='선발'];require(len(starts)==1 and t[0][ai]=='선발','explicit starter '+fid)
   p=r['starters'][side];pe=p['evidence'];pid=p['player_id'];claim=starts[0][ni];starters.append(pid)
   require(pid.isdigit() and p['actual_record_verified'] is True and pe['box_row_player_field']==claim and pe['box_table_index']==i and pe['schedule_team_id']==r['teams'][side] and pe['official_record']==bm and pe['schedule']==sm,'starter evidence '+fid)
   require(pe['schedule_matches_actual'] is True and pid==str(g[pre+'_PIT_P_ID']) and p['name']==str(g[pre+'_PIT_P_NM']).strip() and claim in (pid,p['name']),'starter mismatch '+fid)
   hitters=x['lineups'][side];require(x['lineup_flag'] is True and x['lineup_status']=='official_flag_true_profile_name_crossmatched' and [v['order'] for v in hitters]==list(range(1,10)),'lineup metadata '+fid)
   hitterids.extend(v['player_id'] for v in hitters);counts['starters']+=1
  require(len(set(starters))==2 and len(hitterids)==len(set(hitterids))==18,'duplicate IDs '+fid);counts['fixtures']+=1
 require(len(raws)==manifest['raw_response_count']==873 and len(used)==873,'raw coverage')
 print(json.dumps({'commit':C,'cohort_commit':B,'lineup_commit':L,'counts':counts,'raw_responses_verified':len(raws),'cohort_missing':0,'cohort_extra':0,'receipt_proven':False,'model_performance_calculated':False},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
