"""Freeze the model cohort and obtain only missing official round evidence."""
import gzip
import hashlib
import json
import shutil
import time
from datetime import datetime,timezone
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
SOURCE=Path(r'C:\Users\user\Documents\Codex\kbodata\experiments\kbo-player-data-20261103')
PHASE=Path(r'C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\kbo-player-model-20261103\experiments\kbo-player-model-20261103\phase2')
CACHES=[ROOT.parent/'evidence',Path(r'C:\Users\user\Documents\Codex\klprice\experiments\kleague-official-prices-20261103\evidence')]
BASE='https://www.betman.co.kr'
EXPECTED={'rows.jsonl':'66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c','labels.jsonl':'ef32ffe806b41521f5bd53c12d0eea1362bd44293181fc3ebb7c24c6fcb249ca'}
def sha(data):return hashlib.sha256(data).hexdigest()
def readlines(path):return [json.loads(x) for x in path.read_bytes().splitlines()]
def writejson(path,data):path.write_bytes((json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode('utf8'))

def freeze():
    for name,digest in EXPECTED.items():
        if sha((SOURCE/name).read_bytes())!=digest:raise ValueError('original input changed: '+name)
    pred=json.loads((PHASE/'predictions.json').read_bytes())['predictions']
    sets=[{x['fixture_id'] for x in p} for p in pred.values()]
    if not sets or len(sets[0])!=726 or any(s!=sets[0] for s in sets):raise ValueError('model fixture mismatch')
    ids=sets[0]
    test_labels=[json.loads(x) for x in gzip.decompress((PHASE/'test-labels.jsonl.gz').read_bytes()).splitlines()]
    if len(test_labels)!=726 or {x['fixture_id'] for x in test_labels}!=ids:raise ValueError('test label fixture mismatch')
    model_labels={x['fixture_id']:x for x in test_labels}
    features=[json.loads(x) for x in gzip.decompress((PHASE/'features.jsonl.gz').read_bytes()).splitlines()]
    feat={x['fixture_id']:x for x in features if x['fixture_id'] in ids}
    rows=readlines(SOURCE/'rows.jsonl');labels={x['fixture_id']:x for x in readlines(SOURCE/'labels.jsonl')}
    target=[]
    for row in rows:
        if row['fixture_id'] not in ids or row['market'] is None:continue
        if row['market']!=feat[row['fixture_id']]['market']:raise ValueError('model market mismatch')
        m=row['market'];label=labels[row['fixture_id']]
        ml=model_labels[row['fixture_id']]
        if (ml['market_id'],ml['home_score'],ml['away_score'])!=(m['market_id'],label['home_score'],label['away_score']):raise ValueError('model test label mismatch')
        target.append({'fixture_id':row['fixture_id'],'season':row['season'],'kickoff_at':row['kickoff_at'],
          'home_id':row['home_id'],'away_id':row['away_id'],'market_id':m['market_id'],
          'original_prices':m['selections'],'score':[label['home_score'],label['away_score']]})
    if len(target)!=354 or any(x['season']!=2026 for x in target):raise ValueError('wrong target size or season')
    for name,data in [('test-fixture-ids.json.gz',json.dumps(sorted(ids),separators=(',',':')).encode()),
                      ('audit-targets.jsonl.gz',''.join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for x in target).encode())]:
        (ROOT/name).write_bytes(gzip.compress(data,mtime=0))
    source_info={'original_input_sha256':EXPECTED,'model_input_sha256':{n:sha((PHASE/n).read_bytes()) for n in ('predictions.json','features.jsonl.gz','test-labels.jsonl.gz')},
                 'test_fixtures':len(ids),'target_fixtures':len(target),'cohort_selection':'all 2026 frozen test fixtures with original market; no outcome selection'}
    writejson(ROOT/'audit-sources.json',source_info)
    return target

def main():
    (ROOT/'responses').mkdir(exist_ok=True)
    if (ROOT/'audit-targets.jsonl.gz').exists():
        target=[json.loads(x) for x in gzip.decompress((ROOT/'audit-targets.jsonl.gz').read_bytes()).splitlines()]
    else:target=freeze()
    plan=sorted({(int(x['market_id'].split(':')[1]),int(x['market_id'].split(':')[2])) for x in target})
    if len(target)!=354 or len(plan)!=38:raise ValueError('unexpected frozen cohort')
    ledger_path=ROOT/'request-ledger.json';ledger=json.loads(ledger_path.read_bytes()) if ledger_path.exists() else []
    if any(x.get('http_status') in (403,429) for x in ledger):raise RuntimeError('previous 403/429: stop')
    done={x['name'] for x in ledger if x.get('http_status')==200}
    prior_attempts=sum(x.get('origin')=='new_request' for x in ledger)
    with requests.Session() as session:
        for year,rd in plan:
            name=f'detail-{year}-{rd}'
            if name in done:continue
            hits=[]
            for cache in CACHES:
                for rec in json.loads((cache/'attempts.json').read_bytes()):
                    if rec.get('name')==name and rec.get('http_status')==200:
                        raw=gzip.decompress((cache/Path(rec['body_file']).name).read_bytes())
                        if sha(raw)!=rec['body_sha256']:raise ValueError('cached response sha mismatch '+name)
                        hits.append((rec['received_at'],rec['body_sha256'],cache,rec))
            dest=ROOT/'responses'/f'{name}.body.gz'
            if hits:
                _,_,cache,source=max(hits,key=lambda h:(h[0],h[1]))
                shutil.copyfile(cache/Path(source['body_file']).name,dest)
                record=dict(source);record.update(origin='reused_latest_receipt',source_cache=str(cache),body_file=f'responses/{dest.name}')
            else:
                if prior_attempts>=30:raise RuntimeError('30 new request cap reached')
                payload={'gmId':'G101','gmTs':f'{year%100:02d}{rd:04d}','gameYear':str(year),'_sbmInfo':{'debugMode':'false'}}
                record={'name':name,'origin':'new_request','method':'POST','url':BASE+'/buyPsblGame/gameInfoInq.do','request_json':payload,'timeout_seconds':15};prior_attempts+=1
                try:
                    resp=session.post(record['url'],json=payload,timeout=15,headers={'User-Agent':'Mozilla/5.0','X-Requested-With':'XMLHttpRequest','Referer':BASE+'/main/mainPage/gamebuy/closedGameList.do'})
                    raw=resp.content;dest.write_bytes(gzip.compress(raw,mtime=0));record.update(http_status=resp.status_code,received_at=datetime.now(timezone.utc).isoformat(),body_sha256=sha(raw),body_file=f'responses/{dest.name}',content_type=resp.headers.get('Content-Type'))
                except requests.RequestException as e:
                    record.update(http_status=None,failed_at=datetime.now(timezone.utc).isoformat(),error_type=type(e).__name__,error=str(e))
                ledger.append(record);writejson(ledger_path,ledger);print(name,record.get('http_status'),flush=True)
                if record.get('http_status') in (403,429):break
                time.sleep(1)
                continue
            ledger.append(record);writejson(ledger_path,ledger)
    print('targets',len(target),'rounds',len(plan),'responses',sum(x.get('http_status')==200 for x in ledger),'new requests',prior_attempts,flush=True)

if __name__=='__main__':main()
