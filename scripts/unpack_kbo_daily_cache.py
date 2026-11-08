"""Reconstruct an offline cache from the committed official raw bundles."""
import argparse,base64,gzip,hashlib,json
from pathlib import Path

def sha(b):return hashlib.sha256(b).hexdigest()

def unpack(daily_path,v1_path,output):
    root=Path(output)
    if root.exists():raise FileExistsError('destination_exists')
    daily=[json.loads(x) for x in gzip.decompress(Path(daily_path).read_bytes()).splitlines()]
    schedules=[json.loads(x) for x in gzip.decompress(Path(v1_path).read_bytes()).splitlines()]
    verified=[];by_schedule={}
    for e in daily:
        m=e['metadata']
        if not e['eligible_for_supplement']:continue
        if e['raw_base64'] is None:raise ValueError('eligible_raw_missing')
        raw=base64.b64decode(e['raw_base64'],validate=True)
        if sha(raw)!=m['sha256'] or len(raw)!=m['bytes']:raise ValueError('daily_raw_sha_mismatch')
        key=sha(json.dumps([m['method'],m['url'],m['query'],m['tag']],sort_keys=True).encode())
        verified.append((key,m,raw))
    for e in schedules:
        if e['kind']!='official_schedule':continue
        raw=base64.b64decode(e['raw_base64'],validate=True);m=e['metadata']
        if sha(raw)!=m['sha256'] or len(raw)!=m['bytes']:raise ValueError('schedule_raw_sha_mismatch')
        by_schedule[m['sha256']]=raw
    (root/'capture/raw').mkdir(parents=True);(root/'capture/requests').mkdir();(root/'schedule').mkdir()
    for key,m,raw in verified:
        (root/'capture/raw'/f"{m['sha256']}.gz").write_bytes(gzip.compress(raw,mtime=0))
        (root/'capture/requests'/f'{key}.json').write_text(json.dumps(m,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf8')
    for digest,raw in by_schedule.items():(root/'schedule'/digest).write_bytes(raw)
    return {'eligible_responses':len(verified),'schedule_bodies':len(by_schedule),'network_requests':0}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--daily-raw',required=True);p.add_argument('--v1-raw',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    print(json.dumps(unpack(a.daily_raw,a.v1_raw,a.output),ensure_ascii=False))
