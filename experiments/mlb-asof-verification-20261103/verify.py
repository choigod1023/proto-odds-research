import datetime as dt, gzip, hashlib, json
from pathlib import Path
P=Path(__file__).resolve().parent; E=P/'evidence'
CASES=[('824940','twodays','timestamps','20260330_161500','20260401_154000'),('822974','early-822974','timestamps-822974','20260530_224000','20260601_220500'),('822855','early-822855','timestamps-822855','20260830_000500','20260831_233000')]
def parse(t): return dt.datetime.strptime(t,'%Y%m%d_%H%M%S').replace(tzinfo=dt.timezone.utc)
def eligible(requested,provider): return parse(provider)<=parse(requested)
def read_current(name):
 meta=json.loads((E/(name+'.json')).read_text(encoding='utf8')); raw=(E/(name+'.bin')).read_bytes()
 assert meta['status']==200 and meta['bytes']==len(raw) and meta['sha256']==hashlib.sha256(raw).hexdigest()
 return json.loads(raw),meta
def main():
 records=[]
 names=['timestamps','early','twodays','timestamps-822974','early-822974','timestamps-822855','early-822855']
 metas=[read_current(name)[1] for name in names];metas.sort(key=lambda m:m['requested_at'])
 for a,b in zip(metas,metas[1:]): assert (dt.datetime.fromisoformat(b['requested_at'])-dt.datetime.fromisoformat(a['received_at'])).total_seconds()>=1
 assert read_current('early')[1]['sha256']==read_current('twodays')[1]['sha256']
 for game,early_name,times_name,requested,cutoff in CASES:
  feed,em=read_current(early_name);timecodes,tm=read_current(times_name)
  prior_meta=json.loads((E/(game+'-pregame.meta.json')).read_text(encoding='utf8'));prior_raw=gzip.decompress((E/(game+'-pregame.json.gz')).read_bytes());assert prior_meta['sha256']==hashlib.sha256(prior_raw).hexdigest()
  assert timecodes==sorted(timecodes) and len(timecodes)==len(set(timecodes))
  assert prior_meta['params']['timecode']==cutoff
  provider=feed['metaData']['timeStamp']; assert provider==timecodes[0] and requested==em['url'].split('=')[-1] and timecodes[1]>cutoff
  assert not eligible(requested,provider) and eligible(cutoff,provider)
  assert em['sha256']==prior_meta['sha256'];assert feed['gamePk']==int(game)
  teams=feed['liveData']['boxscore']['teams']; assert all(len(teams[s]['battingOrder'])==9 for s in ('home','away'))
  records.append({'gamePk':int(game),'requested_timecode':requested,'T35_timecode':cutoff,'provider_timestamp':provider,'earliest_timestamp_in_list':timecodes[0],'next_timestamp':timecodes[1],'timestamps_count':len(timecodes),'early_snapshot_is_admissible':eligible(requested,provider),'same_bytes_as_T35':True,'early_sha256':em['sha256'],'early_received_at':em['received_at'],'timestamps_sha256':tm['sha256'],'timestamps_received_at':tm['received_at'],'historical_lineup_published_time_verified':False})
 print(json.dumps(records,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
