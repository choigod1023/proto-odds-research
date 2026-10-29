from datetime import datetime,timedelta,timezone
from pathlib import Path
import copy,gzip,json,sys

import numpy as np
import pytest
from scipy.optimize import check_grad

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from collect_baseball_event_history import stamp,extract_announcement
from build_baseball_event_features import completion_bound,eligible_logs,features,build
from backtest_baseball_event_form import objective,fit,predict,metrics,RECENT,BULLPEN,CHANGES
from audit_baseball_event_prices import revision_price_window

UTC=timezone.utc


def game():
    return dict(gamePk=1,officialDate='2026-05-05',status=dict(codedGameState='F'),
                teams={s:dict(team=dict(id=i)) for s,i in [('home',10),('away',20)]},
                gameInfo=dict(firstPitch='2026-05-05T20:00:00Z',gameDurationMinutes=150,delayDurationMinutes=20))


def test_completion_bound_includes_delay_and_padding_and_rejects_resumption():
    g=game();assert completion_bound(g)==stamp('2026-05-05T23:50:00Z')
    g['resumeDate']='2026-05-07';assert completion_bound(g) is None
    del g['resumeDate'];g['status']['codedGameState']='L';assert completion_bound(g) is None


def test_naive_observation_timestamp_is_never_interpreted_in_machine_local_time():
    with pytest.raises(ValueError,match='timezone'):stamp('2026-05-05T20:00:00')
    assert stamp('20260505_200000')==stamp('2026-05-06T05:00:00+09:00')


def test_recent_window_rejects_current_or_unfinished_or_too_old_games():
    c=stamp('2026-05-05T20:00:00Z')
    def r(days,hours):return dict(played_at=c-timedelta(days=days),completed_bound=c-timedelta(hours=hours))
    old=r(15,300);unfinished=r(1,-1);valid=r(2,40);current=r(0,0)
    assert eligible_logs([old,unfinished,valid,current],c,14)==[valid]


def announcement():
    return dict(gamePk=1,metaData=dict(timeStamp='20260505_194000'),
        gameData=dict(status=dict(codedGameState='P'),teams={s:dict(id=i) for s,i in [('home',10),('away',20)]},
            probablePitchers={s:dict(id=i) for s,i in [('home',100),('away',200)]}),
        liveData=dict(boxscore=dict(teams={s:dict(battingOrder=list(range(i+1,i+10)),pitchers=[i]) for s,i in [('home',100),('away',200)]}),plays=dict(allPlays=[])))


def test_pregame_box_registration_is_distinct_from_probable_only():
    j=announcement();r=extract_announcement(j,stamp('2026-05-05T19:50:00Z'),stamp('2026-05-05T20:00:00Z'))
    assert r['sides']['home']['starter_kind']=='pregame_box_listed'
    j['liveData']['boxscore']['teams']['home']['pitchers']=[]
    r=extract_announcement(j,stamp('2026-05-05T19:50:00Z'),stamp('2026-05-05T20:00:00Z'))
    assert r['sides']['home']['starter_kind']=='probable_only'


def test_future_returned_replay_and_first_real_pitch_are_rejected():
    j=announcement()
    with pytest.raises(ValueError,match='after_requested'):extract_announcement(j,stamp('2026-05-05T18:00:00Z'),stamp('2026-05-05T20:00:00Z'))
    j['liveData']['plays']['allPlays']=[dict(playEvents=[dict(isPitch=True)])]
    with pytest.raises(ValueError,match='already_thrown'):extract_announcement(j,stamp('2026-05-05T19:50:00Z'),stamp('2026-05-05T20:00:00Z'))
    j['liveData']['plays']={'allPlays':[],'currentPlay':dict(playEvents=[dict(isPitch=True)])}
    with pytest.raises(ValueError,match='already_thrown'):extract_announcement(j,stamp('2026-05-05T19:50:00Z'),stamp('2026-05-05T20:00:00Z'))


def setup_history():
    g=game();s=dict(gamePk=1,start_at='2026-05-05T20:00:00Z',home_score=99)
    a=extract_announcement(announcement(),stamp('2026-05-05T19:50:00Z'),stamp(s['start_at']))
    def r(pk,day,pid,pitches,holds=0):
        return dict(gamePk=pk,date=f'2026-05-{day:02}',player=pid,team=10,played_at=stamp(f'2026-05-{day:02}T20:00:00Z'),
            completed_bound=stamp(f'2026-05-{day:02}T23:00:00Z'),
            stat=dict(gamesStarted=0,numberOfPitches=pitches,holds=holds,outs=3,battersFaced=4,strikeOuts=1,baseOnBalls=0))
    rows=[r(2,3,9,20,1),r(3,4,9,25,1),r(4,4,8,30),r(5,6,99,999,99)]
    return s,{(10,'pitching'):rows},{1:g},{(1,10):a}


def test_bullpen_past_workload_and_roles_do_not_use_future_players_or_current_score():
    s,t,g,a=setup_history();x,p=features(s,{},t,g,a)
    assert x['relief_pitches1']==55 and x['relief_pitches3']==75 and x['relief_consecutive']==1
    assert 99 not in p['sides']['home']['top3_past_save_hold_ids'] and 5 not in p['source_games']
    s['home_score']=0;y,q=features(s,{},t,g,a);assert x==y and p==q


def test_missing_or_only_probable_starter_cannot_silently_enter_full_input_comparison():
    s,t,g,a=setup_history();a[(1,10)]['sides']['home']['starter_kind']='probable_only'
    with pytest.raises(ValueError,match='box_listed'):features(s,{},t,g,a)


def test_zero_price_movement_without_before_and_after_observations_is_not_an_event_test():
    a=dict(observed_at='2026-05-05T18:00:00Z');b=dict(observed_at='2026-05-05T19:00:00Z')
    qs=[dict(observed_at='2026-05-05T18:30:00Z',market_home_probability=.6)]
    assert revision_price_window(a,b,qs) is None
    qs=[dict(observed_at='2026-05-05T17:59:00Z',market_home_probability=.6),dict(observed_at='2026-05-05T19:01:00Z',market_home_probability=.6)]
    assert revision_price_window(a,b,qs)['small_observed_move'] is True


def test_logistic_gradient_matches_finite_difference():
    rng=np.random.default_rng(4);x=rng.normal(size=(30,4));y=rng.integers(0,2,30);o=rng.normal(size=30);b=rng.normal(size=4)
    f=lambda b:objective(b,x,y,o,.1)[0];grad=lambda b:objective(b,x,y,o,.1)[1]
    assert check_grad(f,grad,b)<1e-5


def test_zero_residual_exactly_preserves_market_and_refund_is_correct():
    rows=[dict(features={n:0. for n in RECENT+BULLPEN+CHANGES},date=f'2024-04-{i+1:02}',hg=int(i%2==0),ag=int(i%2==1),historical_prices=[1.6,2.1]) for i in range(20)]
    m=fit(rows,'bullpen',1.);m['beta']=[0.]*len(BULLPEN)
    np.testing.assert_allclose(predict(m,rows),predict(None,rows),atol=1e-12)
    probe=[dict(rows[0],hg=3,ag=2),dict(rows[0],hg=2,ag=2)]
    result=metrics(probe,np.array([.6,.6]));assert result['roi']==.3 and result['accuracy']==1 and result['net_krw']==6000
    json.dumps(result,allow_nan=False)  # numpy bool counts must be converted to native int


def test_empty_other_period_does_not_claim_zero_profit_or_accuracy():
    assert metrics([],[])==dict(n=0,accuracy=None,roi=None)


def test_official_box_repair_replaces_incomplete_roster_log_in_self_contained_replay(tmp_path):
    source=tmp_path/'sources';source.mkdir()
    metadata=dict(url='https://statsapi.mlb.com/test',response_sha256='fixture',collected_at='2026-05-06T00:00:00Z')
    current=game();previous=copy.deepcopy(current);previous.update(gamePk=2,officialDate='2026-05-04')
    previous['gameInfo']['firstPitch']='2026-05-04T20:00:00Z'
    def put(name,value):
        with gzip.open(source/name,'wt',encoding='utf8') as f:f.write(json.dumps(value)+'\n')
    put('schedule-2026.jsonl.gz',dict(games=[previous,current],**metadata))
    bad=dict(gamePk=2,player=9,name='fixture',team=10,year=2026,kind='pitching',date='2026-05-04',
        stat=dict(gamesStarted=0,outs=3,battersFaced=4,numberOfPitches=999))
    put('logs-2026-10.jsonl.gz',dict(accepted=True,rows=[bad],**metadata))
    fixed=copy.deepcopy(bad);fixed['stat']['numberOfPitches']=25
    put('repair-2.jsonl.gz',dict(accepted=True,gamePk=2,rows=[fixed],**metadata))
    row=dict(gamePk=1,role='train',date='2026-05-05',start_at='2026-05-05T20:00:00Z',hg=2,ag=1,historical_prices=[1.6,2.1])
    (tmp_path/'cohort-inputs.jsonl').write_text(json.dumps(row)+'\n',encoding='utf8')
    a=extract_announcement(announcement(),stamp('2026-05-05T19:50:00Z'),stamp(row['start_at']))
    with gzip.open(tmp_path/'announcements.jsonl.gz','wt',encoding='utf8') as f:f.write(json.dumps(dict(a,gamePk=1,minutes_before=10))+'\n')
    build(tmp_path,None,None,tmp_path)
    output=json.loads((tmp_path/'event-features.jsonl').read_text(encoding='utf8'))
    assert output['features']['relief_pitches1']==25
    assert output['proof']['source_games']==[2]
