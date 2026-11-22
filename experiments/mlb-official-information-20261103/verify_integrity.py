import datetime as dt,gzip,hashlib,json,pathlib,collections
P=pathlib.Path(__file__).resolve().parent;E=P/'evidence'; metas=[]
for f in sorted(E.glob('*.meta.json')):
 m=json.loads(f.read_text(encoding='utf8')); name=f.name[:-10]; raw=gzip.decompress((E/(name+'.json.gz')).read_bytes())
 assert len(raw)==m['bytes'] and hashlib.sha256(raw).hexdigest()==m['sha256'] and m['status']==200
 assert m['availability']=='retrospective_current_receipt'
 metas.append(m)
metas.sort(key=lambda x:x['requested_at']); gaps=[]
for x,y in zip(metas,metas[1:]):
 gap=(dt.datetime.fromisoformat(y['requested_at'])-dt.datetime.fromisoformat(x['received_at'])).total_seconds();gaps.append(gap);assert gap>=1.0,(x['url'],y['url'],gap)
price=json.loads((P/'price-join.json').read_text(encoding='utf8'))
for h,m in price['sources'].items():
 data=(E/'betman-cache'/(h+'.body.gz')).read_bytes();assert hashlib.sha256(data).hexdigest()==m['compressed_sha256'];assert hashlib.sha256(gzip.decompress(data)).hexdigest()==h
assert len(price['records'])==len({x['gamePk'] for x in price['records']});assert all(x['price_observed_at'] is None and not x['executable_price_verified'] for x in price['records'])
counts=collections.Counter(x['sha256'] for x in metas)
print(json.dumps({'mlb_responses':len(metas),'repeated_raw_hash_groups':sum(v>1 for v in counts.values()),'duplicate_hash_extra_responses':sum(v-1 for v in counts.values() if v>1),'min_seconds_between_requests':min(gaps) if gaps else None,'verified_betman_bodies':len(price['sources']),'matched_prices':len(price['records'])},indent=2))
