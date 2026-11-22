"""Import already frozen public official 2026 box responses into the isolated cache."""
import base64,gzip,hashlib,json,subprocess
from pathlib import Path
import collect_lineups as c
K=Path(r'C:\Users\user\Documents\Codex\kbor');SRC=K/'experiments/kbo-official-record-gap-20261103/artifacts';CACHE=Path('.research-state/kbo-actual-2023');COMMIT='d11367b7b37f3ea34f4c08987ca042af9235575e'
FIX={'normalized-records.jsonl.gz':'e9d84a6ee8dd3d0262fa6ef04a8fc6e23c79b6b5b04f52fc85a4223b41bc3334','raw-responses.jsonl.gz':'8aeaf579f7ac84a642c3017810f8cf42c101bda2f6290a7fa3779a1d6fa72500'}
def sha(x):return hashlib.sha256(x).hexdigest()
for n,want in FIX.items():
 p=SRC/n;raw=p.read_bytes();blob=subprocess.check_output(['git','cat-file','blob',COMMIT+':'+p.relative_to(K).as_posix()],cwd=K)
 if sha(raw)!=want or raw!=blob:raise ValueError('frozen_gap_source_mismatch')
source={x['official_game_id']:x for x in map(json.loads,(CACHE/'official-lineups.jsonl').read_text(encoding='utf8').splitlines()) if x.get('season_id')==2026 and x.get('official_game_id')}
records=list(map(json.loads,gzip.open(SRC/'normalized-records.jsonl.gz','rt',encoding='utf8')));boxes={r['evidence']['box']['sha256']:r for r in records}
count=0
for x in map(json.loads,gzip.open(SRC/'raw-responses.jsonl.gz','rt',encoding='utf8')):
 meta=x['metadata'];r=boxes.get(meta.get('sha256'))
 if not r:continue
 raw=base64.b64decode(x['raw_base64'],validate=True)
 if sha(raw)!=meta['sha256'] or len(raw)!=meta['bytes'] or meta['http_status']!=200 or meta['url']!=c.ROOT+'/ws/Schedule.asmx/GetBoxScore':raise ValueError('raw_box_mismatch')
 o=source[r['official_game_id']];params={'leId':o['league_id'],'srId':o['series_id'],'seasonId':o['season_id'],'gameId':o['official_game_id']}
 if any(str(meta['params'].get(k))!=str(v) for k,v in params.items()):raise ValueError('box_game_or_parameter_mismatch')
 key=sha(json.dumps(['POST',meta['url'],params],sort_keys=True,ensure_ascii=False).encode())
 (CACHE/'raw'/meta['sha256']).write_bytes(raw)
 (CACHE/'requests'/(key+'.json')).write_text(json.dumps(meta,ensure_ascii=False,sort_keys=True),encoding='utf8');count+=1
if count!=256:raise ValueError('wrong_box_cache_count:'+str(count))
print(json.dumps({'cached_box_responses':count,'pinned_commit':COMMIT,'raw_bundle_sha256':FIX['raw-responses.jsonl.gz']}))
