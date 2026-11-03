import importlib.util, pathlib, copy
P=pathlib.Path(__file__).resolve().parents[1]/'experiments/mlb-official-information-20261103/audit.py'
spec=importlib.util.spec_from_file_location('mlb_audit',P); module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def fixture():
 game={'gamePk':1,'gameDate':'2025-06-01T20:00:00Z','teams':{'home':{'team':{'id':1}},'away':{'team':{'id':2}}}}
 team={'battingOrder':list(range(1,10)),'players':{f'ID{i}':{'person':{'id':i},'battingOrder':str(i*100)} for i in range(1,10)},'teamStats':{'batting':{'atBats':0},'pitching':{'numberOfPitches':0}}}
 feed={'gamePk':1,'metaData':{'timeStamp':'20250601_190000'},'gameData':{'datetime':{'dateTime':game['gameDate']},'status':{'abstractGameState':'Preview'},'teams':{'home':{'id':1},'away':{'id':2}}},'liveData':{'plays':{'allPlays':[]},'boxscore':{'teams':{'home':team,'away':copy.deepcopy(team)}}}}
 return game,feed

def test_future_play_and_target_stats_rejected():
 g,f=fixture(); assert module.audit_pregame(f,g)['event_replay_checks_pass']
 f['liveData']['plays']['allPlays']=[{'about':{'endTime':'2025-06-01T19:26:00Z'},'playEvents':[{'isPitch':True}]}]
 f['liveData']['boxscore']['teams']['home']['teamStats']['batting']['atBats']=1
 assert {'play_after_cutoff','pitch_in_pregame','target_game_stats_nonzero'} <= set(module.audit_pregame(f,g)['reasons'])

def test_substitution_is_not_initial_lineup_and_never_certified():
 g,f=fixture();h=f['liveData']['boxscore']['teams']['home']; h['players']['ID10']={'person':{'id':10},'battingOrder':'101'};h['battingOrder'][0]=10
 result=module.audit_pregame(f,g); assert result['lineups']['home']['starting_codes_100_to_900'][0]==1
 assert not result['lineups']['home']['arrays_match'];assert not result['strict_pit_certified']

def test_provider_timestamp_after_cutoff_rejected():
 g,f=fixture(); f['metaData']['timeStamp']='20250601_192501';assert 'provider_timestamp_after_cutoff' in module.audit_pregame(f,g)['reasons']

def test_future_label_poison_does_not_change_pregame_audit():
 g,f=fixture(); original=module.audit_pregame(f,g)
 g['teams']['home']['score']=999; g['teams']['away']['score']=0
 f['labels']={'home_score':999,'away_score':0,'winner':'home'}
 assert module.audit_pregame(f,g)==original

def test_rate_limit_stops_and_persists_without_retry(tmp_path, monkeypatch):
 import io, json, urllib.error, pytest
 source=P.parent/'collect.py'; spec=importlib.util.spec_from_file_location('mlb_collect_test',source); collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)
 collector.P=tmp_path;collector.E=tmp_path/'evidence';collector.E.mkdir(); calls=[]
 def limited(url, timeout):
  calls.append((url,timeout)); raise urllib.error.HTTPError(url,429,'rate limited',{},io.BytesIO(b'limited'))
 monkeypatch.setattr(collector.urllib.request,'urlopen',limited)
 with pytest.raises(urllib.error.HTTPError):collector.get('sample','/api/test')
 info=json.loads((tmp_path/'collector-stop.json').read_text(encoding='utf8')); assert info['status']==429 and info['error_body_sha256']
 with pytest.raises(RuntimeError):collector.get('sample','/api/test')
 assert len(calls)==1 and calls[0][1]==15

def test_missing_play_stream_and_team_mismatch_are_not_accepted():
 g,f=fixture();del f['liveData']['plays']['allPlays'];f['gameData']['teams']['away']['id']=88
 assert {'allPlays_missing_or_invalid','team_identity_missing_or_mismatch'} <= set(module.audit_pregame(f,g)['reasons'])
