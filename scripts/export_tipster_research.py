"""Read-only, bounded-memory export; no remote files, collectors or DB writes."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import zlib

REMOTE = '''import sqlite3,json,hashlib,base64,zlib
c=sqlite3.connect('file:/data/proodd.sqlite3?mode=ro',uri=True,timeout=2)
c.execute('PRAGMA query_only=ON')
c.execute('PRAGMA cache_size=-512')
upper=c.execute("SELECT max(id) FROM event_records WHERE stream='pickster_pick_events'").fetchone()[0]
h=hashlib.sha256();n=0;batch=bytearray()
for (payload,) in c.execute("SELECT payload_json FROM event_records WHERE stream='pickster_pick_events' AND id<=? ORDER BY observed_at,id",(upper,)):
    b=(payload+'\\n').encode('utf8');h.update(b);n+=1
    batch.extend(b)
    if len(batch)>=262144:
        print('CHUNK '+base64.b64encode(zlib.compress(batch)).decode(),flush=True);batch.clear()
if batch: print('CHUNK '+base64.b64encode(zlib.compress(batch)).decode(),flush=True)
print('END '+json.dumps(dict(rows=n,sha256=h.hexdigest(),max_id=upper)),flush=True)
c.close()
'''

def remote_source(kind):
    if kind=='events': return REMOTE
    pre=REMOTE[:REMOTE.index('upper=')]
    tail=REMOTE[REMOTE.index('h=hashlib'):]
    tail=tail.replace('for (payload,) in c.execute("SELECT payload_json FROM event_records WHERE stream=\'pickster_pick_events\' AND id<=? ORDER BY observed_at,id",(upper,)):', 'for payload in projected():')
    if kind=='predictions':
        query='''upper=None
def projected():
 for (raw,) in c.execute("SELECT record_json FROM prediction_records WHERE record_type='prediction' AND captured_at>='2026-08-28' AND captured_at<'2026-09-25' ORDER BY ledger_sequence"):
  r=json.loads(raw)
  p={k:r.get(k) for k in ('event_id','snapshot_id','captured_at','as_of','market_observed_at','kickoff','record_hash','ledger_sequence','model')}
  p['options']=r.get('features',{}).get('options',[])
  yield json.dumps(p,ensure_ascii=True)
'''
    elif kind=='odds-fixtures':
        query='''upper=None
def projected():
 c.set_progress_handler(lambda: 1 if __import__('time').monotonic()-started>30 else 0,10000)
 seen=set()
 for row in c.execute("SELECT season,round,game_no,sport,league,home,away,date_text,market_family FROM odds_snapshots WHERE observed_at>='2026-09-13' AND observed_at<'2026-09-25' AND league='MLB' AND market_family=?",('\uC2B9\uD328',)):
  if row in seen: continue
  seen.add(row)
  yield json.dumps(dict(zip(('year','round','game_no','sport','league','home','away','date_text','market_family'),row)),ensure_ascii=True)
started=__import__('time').monotonic()
'''
    elif kind=='fixtures':
        query='''upper=None
def projected():
 for (raw,) in c.execute("SELECT payload_json FROM dataset_rows WHERE dataset='processed_games' ORDER BY ordinal"):
  r=json.loads(raw)
  if r.get('league')=='MLB' and str(r.get('year'))=='2026' and str(r.get('date_text',''))[:3] in ('08.','09.'):
   yield json.dumps({k:r.get(k) for k in ('year','round','game_no','sport','league','home','away','date_text','market_family','source_event_id','match_id')},ensure_ascii=True)
'''
    else: raise ValueError('Unknown export kind')
    return pre+query+tail

def export(destination,kind='events'):
    destination.parent.mkdir(parents=True,exist_ok=True)
    command='python -c "exec(bytes.fromhex(\''+remote_source(kind).encode().hex()+"'))\""
    proc=subprocess.Popen(['flyctl','ssh','console','-q','-a','proto-odds-collector','-C',command],
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    digest=hashlib.sha256();count=0;trailer=None
    with destination.open('xb') as output:
        for line in proc.stdout:
            if line.startswith(b'CHUNK '):
                if trailer is not None: raise ValueError('Data after trailer')
                raw=zlib.decompress(base64.b64decode(line[6:].strip(),validate=True))
                records=raw.splitlines()
                for record in records: json.loads(record)
                output.write(raw);digest.update(raw);count+=len(records)
            elif line.startswith(b'END '):
                trailer=json.loads(line[4:])
    error=proc.stderr.read().decode(errors='replace');code=proc.wait()
    if trailer is None or trailer['rows']!=count or trailer['sha256']!=digest.hexdigest():
        raise ValueError('Incomplete export; file must not be used: '+str(destination))
    # Windows Fly sometimes reports invalid handle after successfully closing stdout.
    if code and 'The handle is invalid' not in error: raise ValueError('Fly failed: '+error)
    manifest=dict(trailer,kind=kind,fly_exit_code=code,verified_complete=True,read_only=True)
    destination.with_suffix('.manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    print(json.dumps(manifest))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('output',type=Path)
    ap.add_argument('--kind',choices=['events','predictions','fixtures','odds-fixtures'],default='events')
    args=ap.parse_args();export(args.output,args.kind)
