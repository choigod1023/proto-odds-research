import sqlite3,json,datetime,zlib,base64,hashlib,time
start=time.monotonic();names=['archive:2026:'+str(i) for i in range(101,130)]
c=sqlite3.connect('file:/data/proodd.sqlite3?mode=ro',uri=True,timeout=15);c.row_factory=sqlite3.Row;c.execute('BEGIN')
meta=[dict(r) for r in c.execute('SELECT name,revision,stored_at,content_hash,length(payload_json) bytes FROM documents WHERE name IN ('+','.join('?' for _ in names)+')',names)]
assert len(meta)<=29 and sum(r['bytes'] for r in meta)<40_000_000
print('HEADER '+json.dumps({'exported_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'documents':meta,'read_only':True}),flush=True)
z=zlib.compressobj(1,zlib.DEFLATED,31);h=hashlib.sha256();n=0
for name in names:
 r=c.execute('SELECT name,revision,stored_at,content_hash,payload_json FROM documents WHERE name=?',(name,)).fetchone()
 if r is None:continue
 raw=(json.dumps(dict(r),ensure_ascii=False,separators=(',',':'))+'\n').encode();h.update(raw);n+=1
 chunk=z.compress(raw)
 if chunk:print(base64.b64encode(chunk).decode(),flush=True)
chunk=z.flush()
if chunk:print(base64.b64encode(chunk).decode(),flush=True)
c.close();print('END '+json.dumps({'documents':n,'sha256_uncompressed':h.hexdigest(),'elapsed_seconds':round(time.monotonic()-start,3)}),flush=True)
