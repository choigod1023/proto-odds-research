"""Pack official daily supplement and public response bytes; never sends requests."""
import argparse,base64,gzip,hashlib,json,shutil
from pathlib import Path

def sha(b):return hashlib.sha256(b).hexdigest()

def pack(checkpoint,snapshot,destination,excluded_path=None):
    checkpoint=Path(checkpoint);source=checkpoint/snapshot;raw=source.read_bytes();records=[json.loads(x) for x in gzip.decompress(raw).splitlines() if x.strip()]
    if len(records)!=256 or len({x['official_game_id'] for x in records})!=256:raise ValueError('supplement_game_count')
    metadata=[json.loads(x.read_text(encoding='utf8')) for x in (checkpoint/'requests').glob('*.json')]
    metadata.sort(key=lambda x:(x.get('url',''),x.get('tag',''),json.dumps(x.get('query',{}),sort_keys=True)))
    excluded=json.loads(Path(excluded_path).read_text(encoding='utf8')) if excluded_path else []
    excluded_keys={(m.get('tag'),json.dumps(m.get('query',{}),sort_keys=True),m.get('sha256')) for m in excluded}
    entries=[];available=set()
    for m in metadata:
        if m.get('sha256'):
            body=gzip.decompress((checkpoint/'raw'/f"{m['sha256']}.gz").read_bytes())
            if sha(body)!=m['sha256'] or len(body)!=m['bytes']:raise ValueError('response_sha_mismatch')
            available.add(m['sha256']);entries.append({'metadata':m,'raw_base64':base64.b64encode(body).decode('ascii'),
                'eligible_for_supplement':(m.get('tag'),json.dumps(m.get('query',{}),sort_keys=True),m.get('sha256')) not in excluded_keys})
        else:entries.append({'metadata':m,'raw_base64':None,'eligible_for_supplement':False})
    for m in metadata:
        form=m.get('form') or {}; state=form.get('public_form_state_source_sha256')
        if state and state not in available:raise ValueError('postback_source_missing')
    payload=b''.join((json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n').encode() for x in entries)
    dst=Path(destination);dst.mkdir(parents=True,exist_ok=True)
    for name,data in [('supplement-v2.jsonl.gz',raw),('official-daily-raw.jsonl.gz',gzip.compress(payload,mtime=0))]:
        with (dst/name).open('xb') as f:f.write(data)
    return {'games':len(records),'request_entries':len(entries),'excluded_request_entries':sum(not e['eligible_for_supplement'] for e in entries),'supplement_gzip_sha256':sha(raw),
            'supplement_uncompressed_sha256':sha(gzip.decompress(raw)),
            'raw_gzip_sha256':sha((dst/'official-daily-raw.jsonl.gz').read_bytes()),'raw_uncompressed_sha256':sha(payload)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',required=True);p.add_argument('--snapshot',required=True);p.add_argument('--destination',required=True);p.add_argument('--excluded');a=p.parse_args()
    print(json.dumps(pack(a.checkpoint,a.snapshot,a.destination,a.excluded),ensure_ascii=False,indent=2))
