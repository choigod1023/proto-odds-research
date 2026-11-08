import copy, hashlib, json
from datetime import date, timedelta
import pytest
from scripts import nba_espn_collect as collect
from scripts import nba_espn_research as research

class Response:
 def __init__(self,status,body=b'{"events":[]}'): self.status_code=status; self.content=body; self.url='https://example.invalid/scoreboard'
 def json(self):return json.loads(self.content)
class Session:
 def __init__(self,responses):self.responses=list(responses);self.calls=0
 def get(self,*a,**kw):self.calls+=1;return self.responses.pop(0)

def test_cache_verified_and_corruption_stops(tmp_path):
 s=Session([Response(200)]); c=collect.Collector(tmp_path,s,0)
 assert c.get('scoreboard','20250101')=={'events':[]}
 assert c.get('scoreboard','20250101')=={'events':[]} and s.calls==1
 (tmp_path/'scoreboard/20250101.json').write_bytes(b'bad')
 with pytest.raises(collect.StopCollection):c.get('scoreboard','20250101')
 assert s.calls==1 and (tmp_path/'scoreboard/20250101.json').read_bytes()==b'bad'
 with pytest.raises(ValueError):research.verified_json(tmp_path/'scoreboard/20250101.json')

def test_403_429_stop_without_retry(tmp_path):
 for status in (403,429):
  folder=tmp_path/str(status);s=Session([Response(status)]);c=collect.Collector(folder,s,0)
  with pytest.raises(collect.StopCollection):c.get('summary','123')
  assert s.calls==1 and json.loads((folder/'STOP.json').read_text())['status']==status

def game(day,ident,score=(100,90)):
 return {'id':str(ident),'day':day,'utc':day+'T00:00Z','teams':['BOS','LAL'],'scores':list(score),'stats':[{'fga':80,'fgm':40,'three':10,'fta':20,'orb':10,'tov':10},{'fga':90,'fgm':35,'three':12,'fta':10,'orb':8,'tov':12}]}

def test_causal_history_and_arithmetic():
 target=game('2025-02-01',100); histories={'BOS':[],'LAL':[]}
 for i in range(10):
  g=game((date(2025,1,10)+timedelta(days=i)).isoformat(),i)
  for team in histories: histories[team].append(g)
 shooting=research.features(target,histories,'shooting_efficiency')
 assert shooting==pytest.approx(2*((40+5)/80-(35+6)/90))
 eff=research.features(target,histories,'efficiency')
 assert eff==pytest.approx(2*(100*100/(80-10+10+.44*20)-100*90/(90-8+12+.44*10)))
 histories['BOS'].append(game('2025-01-29',50,(1,250)))
 assert research.features(target,histories,'shooting_efficiency')==pytest.approx(shooting)
 changed=copy.deepcopy(target);changed['scores']=[1,999];changed['stats'][0]['fgm']=0
 assert research.features(changed,histories,'shooting_efficiency')==pytest.approx(shooting)
 histories['BOS']=histories['BOS'][:9]
 assert research.features(target,histories,'efficiency') is None

def test_aliases_and_label():
 assert len(research.ALIASES)==30
 assert len(research.ALIAS_INDEX)==30
 with __import__('gzip').open(research.PRICE,'rt',encoding='utf8') as f:
  rows=[json.loads(line) for line in f if '"NBA"' in line]
 names={r[k] for r in rows for k in ('home','away')}
 assert names==set(research.ALIASES.values())
 assert all(research.name(n) for n in names)
 assert research.name('LA레이커')=='LAL'
 assert research.name('알 수 없음') is None
 row={'home_win':1,'candidate':.8,'market_home':.6,'odds':[2.,2.], 'day':'2025-01-01'}
 assert research.calc([row],'candidate')['roi']==1

def test_adapter_raw_score_and_summary_stats(tmp_path):
 def competitor(team,home,score,fgm,fga,three,fta):
  return {'team':{'abbreviation':team},'homeAway':home,'score':str(score),'statistics':[{'name':k,'displayValue':str(v)} for k,v in {'fieldGoalsMade':fgm,'fieldGoalsAttempted':fga,'threePointFieldGoalsMade':three,'freeThrowsAttempted':fta}.items()]}
 event={'id':'401705021','date':'2025-01-02T00:00Z','status':{'type':{'completed':True}},'competitions':[{'competitors':[competitor('DET','home',105,41,79,12,17),competitor('ORL','away',96,37,91,8,20)]}]}
 def box(team,fg,three,ft):
  return {'team':{'abbreviation':team},'statistics':[{'name':k,'displayValue':v} for k,v in {'fieldGoalsMade-fieldGoalsAttempted':fg,'threePointFieldGoalsMade-threePointFieldGoalsAttempted':three,'freeThrowsMade-freeThrowsAttempted':ft,'offensiveRebounds':'9','totalTurnovers':'13'}.items()]}
 data={'header':{'id':'401705021','competitions':[{'competitors':[{'team':{'abbreviation':'DET'},'score':'105'},{'team':{'abbreviation':'ORL'},'score':'96'}]}]},'boxscore':{'teams':[box('DET','41-79','12-29','11-17'),box('ORL','37-91','8-35','14-20')]}}
 path=tmp_path/'summary/401705021.json';path.parent.mkdir();raw=json.dumps(data).encode();path.write_bytes(raw);path.with_suffix('.receipt.json').write_text(json.dumps({'status':200,'sha256':hashlib.sha256(raw).hexdigest()}))
 g=research.parse_game(event,tmp_path)
 assert g['day']=='2025-01-02' and g['scores']==[105,96] and g['stats'][0]['orb']==9 and g['stats'][1]['tov']==13
 missing=copy.deepcopy(event);del missing['competitions'][0]['competitors'][0]['statistics'];incomplete=research.parse_game(missing,tmp_path)
 assert incomplete is not None and incomplete['stats'][0]=={} and incomplete['summary_invalid']
 prior=[game((date(2024,12,1)+timedelta(days=i)).isoformat(),i) for i in range(10)]
 incomplete['teams']=['BOS','LAL'];prior.append(incomplete); target=game('2025-02-01',100)
 assert research.features(target,{'BOS':prior,'LAL':prior},'efficiency') is None
 assert research.features(target,{'BOS':prior,'LAL':prior},'shooting_efficiency') is None
 data['header']['id']='wrong';raw=json.dumps(data).encode();path.write_bytes(raw);path.with_suffix('.receipt.json').write_text(json.dumps({'status':200,'sha256':hashlib.sha256(raw).hexdigest()}))
 g=research.parse_game(event,tmp_path);assert g['summary_invalid'] and 'orb' not in g['stats'][0]

def test_retry_server_errors(tmp_path, monkeypatch):
 monkeypatch.setattr(collect.time,'sleep',lambda seconds:None)
 s=Session([Response(500),Response(503),Response(200)]); c=collect.Collector(tmp_path,s,0)
 assert c.get('scoreboard','20250101')=={'events':[]} and s.calls==3
 s=Session([Response(500),Response(500),Response(500)]); c=collect.Collector(tmp_path/'failure',s,0)
 with pytest.raises(collect.StopCollection):c.get('scoreboard','20250101')
 assert s.calls==3 and json.loads((tmp_path/'failure/FAILURE.json').read_text())['status']==500

def test_missing_recent_box_not_replaced_by_older_game():
 target=game('2025-02-01',100); history=[game((date(2025,1,1)+timedelta(days=i)).isoformat(),i) for i in range(11)]
 del history[-1]['stats'][0]['orb']
 assert research.features(target,{'BOS':history,'LAL':history},'efficiency') is None
 assert research.features(target,{'BOS':history,'LAL':history},'shooting_efficiency') is not None

def test_conflicting_scoreboard_event_stops(tmp_path):
 folder=tmp_path/'scoreboard';folder.mkdir()
 def save(name,event):
  path=folder/(name+'.json');raw=json.dumps({'events':[event]}).encode();path.write_bytes(raw)
  path.with_suffix('.receipt.json').write_text(json.dumps({'status':200,'sha256':hashlib.sha256(raw).hexdigest()}))
 save('one',{'id':'1','score':10});save('two',{'id':'1','score':10})
 assert research.read_events(tmp_path)[1:]==(1,0)
 save('two',{'id':'1','score':11})
 with pytest.raises(ValueError,match='conflicting scoreboard'):research.read_events(tmp_path)

def test_summary_404_is_recorded_and_not_retried(tmp_path):
 s=Session([Response(404,b'{"message":"not found"}')]);c=collect.Collector(tmp_path,s,0)
 assert c.get('summary','404') is None
 assert c.get('summary','404') is None and s.calls==1
 assert (tmp_path/'summary/404.unavailable.body').read_bytes()==b'{"message":"not found"}'
 assert json.loads((tmp_path/'summary/404.unavailable.receipt.json').read_text())['status']==404
