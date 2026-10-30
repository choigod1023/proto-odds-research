import importlib.util
from pathlib import Path
from datetime import timedelta
import gzip
import json
import pytest

spec = importlib.util.spec_from_file_location('basketball_rd', Path(__file__).resolve().parents[1]/'scripts/rd_basketball_features.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def game(league='KBL', periods=4):
    reg={'KBL':40,'NBA':48}[league]+5*(periods-4)
    return {'league':league,'game_id':'past','start':'2026-03-01T10:00:00+00:00',
            'end':'2026-03-01T12:00:00+00:00','periods':periods,
            'teams':{'A':{str(i):reg for i in range(5)}},
            'source_meta':[{'observed_at':'2026-10-30T00:00:00+00:00','sha256':'a'*64,'source_id':'source'}]}

@pytest.mark.parametrize('league,periods,peak,ot',[('KBL',4,40,0),('NBA',4,48,0),('KBL',5,45,25),('NBA',6,58,50)])
def test_clock_and_overtime(league,periods,peak,ot):
    values,evidence,reason=m.aggregate([game(league,periods)],league,'A',m.dt('2026-03-02T00:00:00Z'),'target')
    assert reason is None
    assert values['player_peak_minutes_72h']==peak
    assert values['ot_team_minutes_72h']==ot
    assert evidence['evidence']['availability_tier']=='retrospective_lagged'

def test_future_end_and_target_excluded():
    g=game(); cutoff=m.dt('2026-03-01T11:00:00Z')
    assert m.aggregate([g],'KBL','A',cutoff,'target')[2]=='prior_game_not_finished_at_decision'
    assert m.aggregate([g],'KBL','A',cutoff,'past')[0]=={}
    future=game(); future['start']='2026-03-05T00:00:00Z'
    assert not m.aggregate([future],'KBL','A',cutoff,'target')[0]

def test_missing_not_zero():
    g=game(); g['teams']['A']['0']=None
    assert m.aggregate([g],'KBL','A',m.dt('2026-03-02T00:00:00Z'),'target')[2]=='missing_player_minutes'
    assert m.minutes(None) is None
    assert m.minutes('0')==0
    assert m.minutes('12:30')==12.5

def test_capture_gate_and_no_season_totals():
    g=game(); cutoff=m.dt('2026-03-02T00:00:00Z')
    g['source_meta'][0]['observed_at']='2026-03-01T13:00:00Z'
    g['season_final_minutes']=99999
    vals,ev,_=m.aggregate([g],'KBL','A',cutoff,'target')
    assert vals['player_top5_minutes_72h']==200
    assert ev['evidence']['availability_tier']=='captured_pregame'
    with pytest.raises(ValueError): m.dt('2026-03-01T00:00:00')

def test_schedule_gap_not_partial_feature(tmp_path):
    row={'cohort':'historical_market_proxy','event_id':'target','league':'KBL','kickoff':'2026-03-02T01:00:00Z','available_at':'2026-03-02T00:00:00Z','home':'A','away':'B'}
    out,audit=m.extract([row],[game()],{},[],tmp_path/'out.gz',coverage={'KBL':set()})
    assert not out[0]['features']
    assert audit[0]['reason']=='schedule_window_not_covered'

def test_partial_box_rejected():
    g=game(); g['teams']['A'].pop('0')
    assert m.aggregate([g],'KBL','A',m.dt('2026-03-02T00:00:00Z'),'target')[2]=='incomplete_or_invalid_minutes_total'

def test_bounded_capture_no_untrusted_host(tmp_path):
    intake=m.Intake(tmp_path,budget=0)
    with pytest.raises(ValueError): intake.get('https://example.com/data')
    assert intake.get('https://api.kbl.or.kr/match/list')[1]['error']=='request_budget'
    assert intake.count==0

def test_espn_only_game_minutes_dnp_and_actual_end():
    data={'header':{'competitions':[{'id':'past','date':'2026-03-01T10:00:00Z',
          'status':{'type':{'completed':True}}}]},
          'plays':[{'type':{'text':'End Game'},'wallclock':'2026-03-01T12:00:00Z','period':{'number':4}}],
          'boxscore':{'players':[{'team':{'displayName':'A'},'statistics':[{'names':['MIN'],
            'athletes':[{'athlete':{'id':'1'},'stats':['48'],'seasonFinalMinutes':9999},
                        {'athlete':{'id':'2'},'stats':[],'didNotPlay':True},
                        {'athlete':{'id':'3'},'stats':[],'didNotPlay':False}]}]}]}}
    g=m.normalize_espn(data,{})
    assert g['teams']['A']=={'1':48,'3':None}
    assert g['end']=='2026-03-01T12:00:00Z'
    data['plays']=[]
    with pytest.raises(ValueError,match='wallclock'): m.normalize_espn(data,{})

def test_missing_scheduled_game_blocks_available_partial_box(tmp_path):
    row={'cohort':'test','event_id':'target','league':'KBL','kickoff':'2026-03-02T01:00:00Z',
         'available_at':'2026-03-02T00:00:00Z','home':'A','away':'B'}
    schedule=[{'league':'KBL','game_id':'missing','start':'2026-03-01T08:00:00Z','teams':['A']}]
    out,audit=m.extract([row],[game()],{},schedule,tmp_path/'partial.gz')
    assert out[0]['features']=={}
    assert audit[0]['reason']=='missing_scheduled_box'

def test_kbl_adapter_uses_game_minutes_not_rating_or_scores():
    info={'game':{'gmkey':'past','gameDate':'20260301','gameStart':'1900','gameEnd':'2110',
          'isEnded':1,'tcodeH':'06','tcodeA':'35'},
          'teamrecords':{'home':{'scoreeq':[5],'seasonFinalMinutes':100000},'away':{'scoreeq':[7]}}}
    players=[{'player':{'tcode':'06','pcode':'p'},'records':{'playMin':32,'playSec':30,'score':100,'usgRt':999}}]
    g=m.normalize_kbl(info,players,[])
    assert g['teams']['06']['p']==32.5
    assert g['periods']==5
    assert g['end']=='2026-03-01T21:10:00+09:00'
    players[0]['records'].pop('playSec')
    assert m.normalize_kbl(info,players,[])['teams']['06']['p'] is None
