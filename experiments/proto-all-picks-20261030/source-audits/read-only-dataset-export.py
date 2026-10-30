import sqlite3,json,datetime,zlib,base64,hashlib,sys,time
start=time.monotonic()
c=sqlite3.connect('file:/data/proodd.sqlite3?mode=ro',uri=True,timeout=15)
c.row_factory=sqlite3.Row
c.execute('BEGIN')
meta=dict(c.execute("SELECT * FROM dataset_revisions WHERE name='processed_games'").fetchone())
print('HEADER '+json.dumps({'dataset':meta,'exported_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'read_only':True}),flush=True)
compress=zlib.compressobj(1,zlib.DEFLATED,31);h=hashlib.sha256();n=0
for r in c.execute("SELECT payload_json FROM dataset_rows WHERE dataset='processed_games' ORDER BY ordinal"):
 raw=(r[0]+'\n').encode('utf8');h.update(raw);n+=1
 chunk=compress.compress(raw)
 if chunk:print(base64.b64encode(chunk).decode(),flush=True)
tail=compress.flush()
if tail:print(base64.b64encode(tail).decode(),flush=True)
c.close()
assert n==meta['row_count']
print('END '+json.dumps({'rows':n,'sha256_uncompressed':h.hexdigest(),'elapsed_seconds':round(time.monotonic()-start,3)}),flush=True)
