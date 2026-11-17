import copy
from datetime import datetime,timezone,timedelta
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.optimize import check_grad

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from collect_baseball_pregame_evidence import extract_snapshot,parse_npb_box
from compare_announced_baseball import features,fit,objective,player_rates,predict,stats
from audit_baseball_announcements import before_game,eligible_news
from reconcile_npb_research_history import reconcile


def snapshot():
    j=dict(metaData=dict(timeStamp='20260401_175000'),gameData=dict(status=dict(codedGameState='P',detailedState='Warmup'),
        probablePitchers=dict(home=dict(id=100),away=dict(id=200))),liveData=dict(plays=dict(allPlays=[]),boxscore=dict(teams={})))
    for side,offset in [('home',100),('away',200)]:
        j['liveData']['boxscore']['teams'][side]=dict(battingOrder=list(range(offset+1,offset+10)),
            players={'ID'+str(i):dict(person=dict(fullName='P'+str(i)),seasonStats=dict(pitching=dict(battersFaced=100),
                batting=dict(atBats=100,hits=30,baseOnBalls=10,totalBases=45)),stats=dict(batting=dict(hits=99))) for i in range(offset,offset+10)})
    return j


def extract(j):
    return extract_snapshot(j,datetime(2026,4,1,17,50,tzinfo=timezone.utc),datetime(2026,4,1,18,tzinfo=timezone.utc))


def test_warmup_snapshot_has_real_lineup_and_only_prior_season_stats():
    r=extract(snapshot());assert len(r['sides']['home']['lineup'])==9
    assert r['sides']['home']['lineup'][0]['batting']['hits']==30


@pytest.mark.parametrize('timestamp',['20260401_175001','20260401_180001'])
def test_timecode_not_honored_is_rejected(timestamp):
    j=snapshot();j['metaData']['timeStamp']=timestamp
    with pytest.raises(ValueError,match='timestamp'):extract(j)


def test_final_status_is_rejected_even_if_timestamp_old():
    j=snapshot();j['gameData']['status']['codedGameState']='F'
    with pytest.raises(ValueError,match='not_scheduled'):extract(j)


def test_current_play_pitch_rejected_when_allplays_empty():
    j=snapshot();j['liveData']['plays']['currentPlay']=dict(playEvents=[dict(isPitch=True)])
    with pytest.raises(ValueError,match='pitch_already'):extract(j)


def test_duplicate_or_missing_lineup_cannot_be_filled_from_bench():
    j=snapshot();j['liveData']['boxscore']['teams']['home']['battingOrder'][-1]=101
    with pytest.raises(ValueError,match='lineup_missing'):extract(j)


def test_batter_obp_includes_hbp_and_sacrifice_flies():
    _,_,obp,_=player_rates(dict(batting=dict(atBats=100,hits=30,baseOnBalls=10,hitByPitch=2,sacFlies=3)))
    assert obp==pytest.approx((30+10+2+150*.32)/(100+10+2+3+150))


def test_pitcher_kbb_uses_actual_bf_not_pitches():
    p=dict(pitching=dict(outs=27,earnedRuns=3,strikeOuts=10,baseOnBalls=2,battersFaced=40,numberOfPitches=170))
    a=player_rates(p);p['pitching']['numberOfPitches']=999
    assert a==player_rates(p)
    assert a[1]==pytest.approx((10-2+100*.14)/(40+100))


def test_statcast_always_uses_previous_year():
    r=extract(snapshot());r['start_at']='2026-04-01T18:00:00+00:00'
    tables={(2025,k):{} for k in ['batter','pitcher']}
    x,_=features(r,tables)
    tables[(2026,'batter')]={101:dict(pa=99999,est_woba=.9)}
    np.testing.assert_array_equal(features(r,tables)[0],x)


def test_logistic_gradient_and_prediction_replay():
    x=np.array([[1.,-1.],[1.,.2],[1.,1.5]]);y=np.array([0.,1.,1.]);b=np.array([.1,.3])
    assert check_grad(lambda t:objective(t,x,y,.1)[0],lambda t:objective(t,x,y,.1)[1],b)<1e-6
    model=fit(x[:,1:],y,.1)
    assert len(predict(model,x[:,1:]))==3


def test_asof_comparison_requires_observation_zone_and_not_hours_before_claim():
    assert before_game('2026-08-01T04:00:00Z','2026-08-01T14:00:00')
    assert not before_game('2026-08-01T06:00:00Z','2026-08-01T14:00:00')
    with pytest.raises(ValueError):before_game('2026-08-01T04:00:00','2026-08-01T14:00:00')


def test_postgame_article_with_pregame_lineup_is_not_pregame_evidence():
    ids=list(range(9));start='2026-08-01T18:00:00+09:00'
    assert not eligible_news('2026-08-01T21:03:00+09:00',None,start,ids)[0]
    assert not eligible_news('2026-08-01T16:00:00+09:00','2026-08-01T21:03:00+09:00',start,ids)[0]
    assert eligible_news('2026-08-01T16:00:00+09:00','2026-08-01T16:30:00+09:00',start,ids)[0]


def test_draw_refund_and_odds_settlement():
    rows=[dict(target=dict(hg=3,ag=2,prices=[1.5,2.2],order=['home','away'],date='2026-08-01')),
          dict(target=dict(hg=2,ag=2,prices=[1.5,2.2],order=['home','away'],date='2026-08-02'))]
    r=stats(rows,[.6,.4]);assert r['wins']==1 and r['pushes']==1 and r['roi']==.25


def test_npb_postgame_nested_innings_table_is_not_a_second_pitcher():
    def table(name):return '<h4>'+name+'</h4><table><tbody><tr><th>投手 投球数 自責点</th></tr><tr>'+''.join('<td>'+v+'</td>' for v in ['', '<a href="/bis/players/1.html">投手A</a>','90','25','<table><tr><td>5</td><td>.2</td></tr></table>','4','1','2','0','5','0','0','2','2'])+'</tr></tbody></table>'
    r=parse_npb_box(table('楽天')+table('日本ハム'),'https://npb.jp/scores/2023/0330/f-e-01/box.html')
    assert r['source_kind']=='postgame_official_training_history_only'
    assert [len(v) for v in r['pitching'].values()]==[1,1]


def test_npb_restoration_requires_matching_official_score_and_preserves_history_label():
    b=dict(accepted=True,date='2023-03-30',home='닛폰햄',away='라쿠텐',url='url',pitching={
        '北海道日本ハムファイターズ':[dict(name='加藤',runs='3')],
        '東北楽天ゴールデンイーグルス':[dict(name='田中',runs='1')]})
    h=dict(league='NPB',date=b['date'],home=b['home'],away=b['away'],hg=1,ag=3)
    out,ex=reconcile([b],[h]);assert len(out)==1
    assert out[0]['source_kind']=='postgame_training_history_not_pregame_announcement'
    assert out[0]['actual_starters']==['加藤','田中']
    h['hg']=10;out,ex=reconcile([b],[h]);assert out==[] and ex['official_archive_score_disagreement']==1


def test_compressed_source_facts_can_be_read(tmp_path):
    import gzip
    from collect_baseball_pregame_evidence import read
    with gzip.open(tmp_path/'facts.jsonl.gz','wt',encoding='utf8') as f:f.write('{"id":1}\n')
    assert read(tmp_path/'facts.jsonl')==[dict(id=1)]


@pytest.mark.parametrize('complete',[True,False])
def test_live_capture_official_fallback_is_append_only_and_checks_completion(tmp_path,monkeypatch,complete):
    import json
    import requests
    import capture_baseball_live_research as live
    class Session:
        headers={}
        def get(self,*a,**kw):raise requests.Timeout('test source unavailable')
    monkeypatch.setattr(live.requests,'Session',Session)
    game=dict(game_id='fixture',game_datetime='2099-08-01T18:00:00+09:00',source_url='https://www.koreabaseball.com/',
        lineup_status=dict(official_today=complete),starter_status=dict(state='announced'),
        collection=dict(failed_endpoints=[]),starters=dict(home=dict(name='A'),away=dict(name='B')),
        lineups={s:[dict(batting_order=i,name='P'+str(i)) for i in range(1,10)] for s in ['home','away']})
    monkeypatch.setattr(live,'collect_official',lambda existing:dict(games=[game],errors=0))
    live.capture(tmp_path);live.capture(tmp_path)
    rows=[json.loads(s) for s in (tmp_path/'direct-pregame-captures.jsonl').read_text(encoding='utf8').splitlines()]
    assert len(rows)==2 and all(r['accepted']==complete for r in rows)


def test_live_capture_needs_nine_unique_orders_even_when_source_claims_confirmed():
    from capture_baseball_live_research import complete_nine
    players=[dict(batting_order=i,name='P'+str(i)) for i in range(1,10)]
    assert complete_nine(players)
    players[-1]['batting_order']=8
    assert not complete_nine(players)
