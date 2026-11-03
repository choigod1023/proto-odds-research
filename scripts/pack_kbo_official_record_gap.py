"""Pack exact HTTP bodies and normalized postgame data reproducibly, without network."""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path

SOURCE_SHA='f82acede0093fa2a6a2176018a812ac9c279ad893d3ffb3bed9a16cb37b6c5d3'
def sha(b): return hashlib.sha256(b).hexdigest()


def pack(checkpoint,lineups_path,lineup_raw,destination):
    checkpoint=Path(checkpoint);destination=Path(destination);lineup_bytes=Path(lineups_path).read_bytes()
    if sha(lineup_bytes)!=SOURCE_SHA:raise ValueError('lineup_source_sha_mismatch')
    games=[json.loads(x) for x in lineup_bytes.splitlines() if x.strip()]
    target=[x for x in games if '20260727'<=x['game_date']<='20261027']
    if len(target)!=256:raise ValueError('target_count')
    records=(checkpoint/'records.jsonl').read_bytes();rows=[json.loads(x) for x in records.splitlines() if x.strip()]
    if len({r['official_game_id'] for r in rows})!=len(rows):raise ValueError('duplicate_records')
    if any(r['official_game_id'] not in {g['official_game_id'] for g in target} for r in rows):raise ValueError('out_of_scope')
    requests=[json.loads(p.read_text(encoding='utf8')) for p in (checkpoint/'requests').glob('*.json')]
    requests.sort(key=lambda m:(str(m['params'].get('gameId','')),m['url']))
    evidence=[]
    for m in requests:
        raw=gzip.decompress((checkpoint/'raw'/f"{m['sha256']}.gz").read_bytes())
        if sha(raw)!=m['sha256'] or len(raw)!=m['bytes']:raise ValueError('raw_sha_mismatch')
        evidence.append({'kind':'record_api','metadata':m,'raw_base64':base64.b64encode(raw).decode('ascii')})
    seen=set()
    for g in target:
        m=g['schedule_evidence']; key=json.dumps([m['url'],m['params']],sort_keys=True)
        if key in seen:continue
        seen.add(key);raw=(Path(lineup_raw)/m['sha256']).read_bytes()
        if sha(raw)!=m['sha256'] or len(raw)!=m['bytes']:raise ValueError('schedule_sha_mismatch')
        evidence.append({'kind':'official_schedule','metadata':m,'raw_base64':base64.b64encode(raw).decode('ascii')})
    payload=b''.join((json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n').encode('utf8') for x in evidence)
    destination.mkdir(parents=True,exist_ok=True)
    paths=[destination/'normalized-records.jsonl.gz',destination/'raw-responses.jsonl.gz']
    for p,data in zip(paths,[records,payload]):
        with p.open('xb') as stream:stream.write(gzip.compress(data,mtime=0))
    return {'records_uncompressed_sha256':sha(records),'records_gzip_sha256':sha(paths[0].read_bytes()),
            'raw_bundle_uncompressed_sha256':sha(payload),'raw_bundle_gzip_sha256':sha(paths[1].read_bytes()),
            'record_count':len(rows),'raw_response_entries':len(evidence),'official_record_responses':len(requests),
            'official_schedule_responses':len(seen),'source_lineup_sha256':SOURCE_SHA}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',required=True);p.add_argument('--lineups',required=True)
    p.add_argument('--lineup-raw',required=True);p.add_argument('--destination',required=True);a=p.parse_args()
    print(json.dumps(pack(a.checkpoint,a.lineups,a.lineup_raw,a.destination),ensure_ascii=False,indent=2))
