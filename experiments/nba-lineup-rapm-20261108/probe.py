import urllib.request, urllib.error, datetime, hashlib, json
ids=['0022400456','0022500451']
rows=[]
for gid in ids:
 for kind in ['playbyplay','boxscore']:
  url=f'https://cdn.nba.com/static/json/liveData/{kind}/{kind}_{gid}.json'
  row={'game_id':gid,'kind':kind,'url':url}
  try:
   with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=12) as r:
    data=r.read(8000001); row.update(status=r.status,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),received_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    if len(data)>8000000: row['error']='size limit'
    else:
     obj=json.loads(data); row['game_keys']=list(obj.get('game',{})); row['action_count']=len(obj.get('game',{}).get('actions',[]))
  except Exception as e: row.update(error=str(e),received_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
  rows.append(row)
print(json.dumps(rows,indent=2))
open('experiments/nba-lineup-rapm-20261108/source-manifest.json','w').write(json.dumps(rows,indent=2))
