import datetime as dt, json
import collect
from summarize import read

def main():
 schedule,_=read('schedule-2026'); chosen=collect.selected(collect.games(schedule)); reports=[]
 for idx in (0,34,85):
  game=chosen[idx]; vals={}
  for delta,tag in [(-180,'probe-180'),(-35,'pregame'),(120,'probe-plus120')]:
   obj,meta=read(f"{game['gamePk']}-{tag}"); requested=collect.stamp(game['gameDate'])+dt.timedelta(minutes=delta)
   provider=dt.datetime.strptime(obj['metaData']['timeStamp'],'%Y%m%d_%H%M%S').replace(tzinfo=dt.timezone.utc)
   plays=obj['liveData']['plays']['allPlays'];pitch=sum(bool(e.get('isPitch')) for p in plays for e in p.get('playEvents',[]));at_bats=sum(obj['liveData']['boxscore']['teams'][s]['teamStats']['batting'].get('atBats',0) for s in ('home','away'))
   end_times=[]
   for play in plays:
    for obj2 in [play.get('about',{})]+play.get('playEvents',[]):
     for key in ('startTime','endTime'):
      if obj2.get(key):end_times.append(collect.stamp(obj2[key]))
   vals[tag]={'requested':requested.isoformat(),'provider':provider.isoformat(),'provider_not_after_requested':provider<=requested,'later_play_count':sum(t>requested for t in end_times),'pitches':pitch,'at_bats':at_bats,'sha256':meta['sha256']}
  post,_=read(f"{game['gamePk']}-boxscore"); final=sum(post['teams'][s]['teamStats']['batting'].get('atBats',0) for s in ('home','away'))
  assert vals['pregame']['pitches']==0 and vals['pregame']['at_bats']==0 and vals['pregame']['provider_not_after_requested'] and vals['pregame']['later_play_count']==0
  assert vals['probe-plus120']['pitches']>0 and 0<vals['probe-plus120']['at_bats']<final and vals['probe-plus120']['later_play_count']==0 and vals['probe-plus120']['provider_not_after_requested']
  reports.append({'gamePk':game['gamePk'],'times':vals,'postgame':{'at_bats':final},'same_early_response':vals['probe-180']['sha256']==vals['pregame']['sha256']})
 assert all(x['same_early_response'] for x in reports)
 assert not reports[0]['times']['probe-180']['provider_not_after_requested']
 print(json.dumps(reports,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
