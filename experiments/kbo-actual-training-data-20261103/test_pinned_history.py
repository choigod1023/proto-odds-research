"""Compare reconstructed projected histories to immutable previous research outputs."""
import collections,gzip,hashlib,json,subprocess,sys
from pathlib import Path
from build_rosters_2023 import SOURCE,fixed_file
from paired_core import previous,valid_batter,valid_pitcher
from history_index import build_old,build_fresh_pitch
import collect_lineups
sys.path.insert(0,str(SOURCE/'experiments/kbo-player-data-20261103'))
import build_prior_projection as prior

def gz(path):return [json.loads(x) for x in gzip.decompress(path.read_bytes()).splitlines()]
def main():
 games=gz(prior.GAMES);dates={g['source_fixture_id']:g['date'] for g in games}
 assert len(dates)==len(games)
 bat=json.loads(fixed_file(prior.BAT,'24bdea9094046bdab4967d314c043d548a252599b652009d5ffdabd341cc84e8'))
 pit=json.loads(fixed_file(prior.PIT,'c87faa6a43140dad067860086b00140fe8f05883a9e42121fd2e8f7233775a66'))
 fixed_file(prior.GAMES,'c1561a252f226edcfaceffefedc0295394b2905a7368078b96cf71ba76389fce')
 oldb,oldp=build_old(games,bat,pit,prior.innings_outs);stats=collections.Counter()
 old=prior.OUT/'prior-projected-features.jsonl.gz';fixed_file(old,'eca78a16ca59ccc1de6eca47713acded513541c59cddbbb4d8dd193e7c5b27ae')
 for row in gz(old):
  day=dates[row['fixture_id']]
  if day[:4] not in ('2023','2024','2025'):continue
  for side in ('home','away'):
   for player in row['features'][side+'_lineup_strength']['value'] or []:
    assert (player['history'] or [])==previous(oldb.get(player['player_id'],[]),day,valid_batter);stats['old_batter_histories']+=1
   v=row['features'][side+'_starter_strength']['value']
   if v:assert v['appearances']==previous(oldp.get(v['projected_player_id'],[]),day,valid_pitcher);stats['old_pitcher_histories']+=1
 kbor=Path(r'C:\Users\user\Documents\Codex\kbor');gap=kbor/'experiments/kbo-official-record-gap-20261103'
 for rel,want in [('artifacts/normalized-records.jsonl.gz','e9d84a6ee8dd3d0262fa6ef04a8fc6e23c79b6b5b04f52fc85a4223b41bc3334'),('v2/artifacts/supplement-v2.jsonl.gz','0293a109c20c3f3344215dd1bd7bfad5e55f2755ada22e01e3c0a5329fd67c84')]:
  path=gap/rel;raw=path.read_bytes();blob=subprocess.check_output(['git','cat-file','blob','d11367b7b37f3ea34f4c08987ca042af9235575e:'+path.relative_to(kbor).as_posix()],cwd=kbor)
  assert hashlib.sha256(raw).hexdigest()==want and raw==blob
 v1={x['fixture_id']:x for x in gz(gap/'artifacts/normalized-records.jsonl.gz')};v2={x['fixture_id']:x for x in gz(gap/'v2/artifacts/supplement-v2.jsonl.gz')};fresh=build_fresh_pitch(games,pit,prior.innings_outs,v1,v2)
 for name in ('projected','official_batting_and_starter'):
  path=prior.OUT/('fresh-history-2026-'+name+'.jsonl.gz');fixed_file(path)
  for row in gz(path):
   day=dates[row['fixture_id']]
   for side in ('home','away'):
    v=row['features'][side+'_starter_strength']['value']
    if v:assert v['appearances']==previous(fresh.get(v['projected_player_id'],[]),day,valid_pitcher);stats['fresh_pitcher_histories']+=1
 print(json.dumps({'status':'ok',**stats}))
if __name__=='__main__':main()
