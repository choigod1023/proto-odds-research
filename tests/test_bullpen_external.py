from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import copy,json,sys

import numpy as np
import pytest
from scipy.optimize import check_grad

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from collect_npb_bullpen_history import outs,parse_box
from build_asian_bullpen_features import feature,normalize_kbo,missing_history
from build_mlb_bullpen_external import stamp,bound,features as mlb_features
from backtest_bullpen_external import objective,metrics,fit,choose,LOAD


@pytest.mark.parametrize('text,expected',[('7 .2',23),('0 ⅓',1),('5 ⅔',17),('0',0),('1.0',3),('5+',15),('0+',0)])
def test_baseball_innings_are_outs_not_decimal_hours(text,expected):assert outs(text)==expected


def test_invalid_baseball_fraction_cannot_enter_workload():
    with pytest.raises(ValueError):outs('1.5')


def box():
    header='<tr>'+''.join(f'<th>{x}</th>' for x in ['','1','2','計','H','E'])+'</tr>'
    score='<table>'+header+'<tr><td>中日ドラゴンズ</td><td>1</td><td>1</td><td>2</td><td>4</td><td>0</td></tr><tr><td>読売ジャイアンツ</td><td>2</td><td>2</td><td>4</td><td>6</td><td>0</td></tr></table>'
    def pitchers(team,pid,runs):
        h='<h3>'+team+'</h3><table><tr><th>投球数 自責点</th></tr>'
        cells=['',f'<a href="/bis/players/{pid}.html">同姓</a>','90','24','7 .2','5','0','2','0','6','0','0',str(runs),str(runs)]
        return h+'<tr>'+''.join('<td>'+c+'</td>' for c in cells)+'</tr></table>'
    return '<div>【試合終了】</div>'+score+pitchers('中日ドラゴンズ','111',4)+pitchers('読売ジャイアンツ','222',2)


def test_npb_pitcher_teams_follow_heading_not_table_order_and_ids_disambiguate_names():
    r=parse_box(box(),'https://npb.jp/scores/2024/0402/g-d-01/box.html')
    assert (r['home'],r['away'],r['hg'],r['ag'])==('요미우리','주니치',4,2)
    assert r['pitchers'][0][0]['player']=='npb:222' and r['pitchers'][1][0]['player']=='npb:111'
    assert r['pitchers'][0][0]['outs']==23


def test_official_pitching_must_reconcile_to_opponent_final_score():
    html=box().replace('<td>2</td><td>4</td><td>0</td>','<td>9</td><td>4</td><td>0</td>')
    with pytest.raises(ValueError,match='score'):parse_box(html,'https://npb.jp/scores/2024/0402/g-d-01/box.html')


def test_unplayed_or_cancelled_box_cannot_become_a_final_pitching_record():
    with pytest.raises(ValueError,match='final_status'):parse_box(box().replace('【試合終了】','【試合中止】'),'https://npb.jp/scores/2024/0402/g-d-01/box.html')


def test_asian_feature_does_not_peek_current_date_or_future_player_roles():
    r=dict(league='KBO',date='2026-05-05',home='A',away='B',hg=1,ag=9)
    def p(day,pid,outs):return dict(date=day,player=pid,outs=outs,runs=0,bb=0,k=1,fixture=day+pid)
    h={('KBO','A'):[p('2026-05-03','x',3),p('2026-05-04','x',6),p('2026-05-05','future',999),p('2026-05-06','future',999)]}
    x,proof=feature(r,h);assert x['relief_outs1']==6 and x['relief_outs3']==9 and x['relief_consecutive']==1
    assert proof['home']['top3_past_outs_ids']==['x'] and proof['home']['latest_source_date']=='2026-05-04'
    r['hg']=99;y,_=feature(r,h);assert x==y


def test_kbo_ambiguous_bf_is_never_used_as_true_batters_faced():
    p=dict(pcode='1',name='투수',inn='9',r=0,bb=2,kk=5,bf=100)
    r=dict(gameId='g1',date='2024-04-01',home='A',away='B',home_score=0,away_score=0,data=dict(home=[p],away=[p]))
    master=[dict(league='KBO',date=r['date'],home='A',away='B',hg=0,ag=0)]
    a,_=normalize_kbo({'g':r},master,{'KBO':{}});r['data']['home'][0]['bf']=999999
    b,_=normalize_kbo({'g':r},master,{'KBO':{}});assert a==b


def test_missing_past_records_are_not_mistaken_for_a_rested_bullpen():
    target=dict(league='KBO',date='2026-05-05',home='A',away='B')
    past=dict(league='KBO',date='2026-05-04',home='A',away='C')
    future=dict(past,date='2026-05-06')
    assert missing_history(target,set(),[past,future])==[('KBO','2026-05-04','A','C')]
    assert missing_history(target,{('KBO','2026-05-04','A','C')},[past,future])==[]


def test_doubleheader_ids_preserve_both_past_pitching_tables():
    p=dict(pcode='1',name='투수',inn='9',r=0,bb=0,kk=3)
    base=dict(gameId='g1',date='2024-04-02',home='A',away='B',home_score=0,away_score=0,data=dict(home=[p],away=[p]))
    second=copy.deepcopy(base);second['gameId']='g2'
    master=[dict(league='KBO',date='2024-04-01',home='A',away='B',hg=0,ag=0)]
    rows,_=normalize_kbo({'g1':base,'g2':second},master,{'KBO':{}})
    assert {r['official_id'] for r in rows}=={'g1','g2'}


def test_market_offset_gradient_is_correct():
    rng=np.random.default_rng(5);x=rng.normal(size=(35,4));y=rng.integers(0,2,35);o=rng.normal(size=35);b=rng.normal(size=4)
    assert check_grad(lambda b:objective(b,x,y,o,.1)[0],lambda b:objective(b,x,y,o,.1)[1],b)<1e-5


def test_draw_is_refunded_and_not_fitted_as_away_win():
    rows=[dict(date='2023-04-01',hg=int(i%2==0),ag=int(i%2==1),historical_prices=[1.6,2.1],features={n:(i%5-2)/10 for n in LOAD}) for i in range(60)]
    a=fit(rows,LOAD,1);draw=dict(rows[0],hg=2,ag=2,features={n:9999 for n in LOAD});b=fit(rows+[draw],LOAD,1)
    assert a==b
    m=metrics([rows[0],draw],np.array([.6,.6]));assert m['wins']==1 and m['pushes']==1 and m['accuracy']==1 and m['roi']==.3
    json.dumps(m,allow_nan=False)


def test_selection_uses_validation_not_new_period_profit():
    candidates={'a':dict(validation=dict(accuracy=.6,brier=.24),evaluation=dict(roi=99)),
                'b':dict(validation=dict(accuracy=.61,brier=.25),evaluation=dict(roi=-99))}
    assert choose(candidates)=='b'


def test_mlb_bound_excludes_resumed_games_and_naive_quote_time():
    with pytest.raises(ValueError):stamp('2026-08-01T12:00:00')
    g=dict(status=dict(codedGameState='F'),gameInfo=dict(firstPitch='2026-08-01T12:00:00Z',gameDurationMinutes=150,delayDurationMinutes=30))
    assert bound(g)==stamp('2026-08-01T16:00:00Z')
    g['resumeDate']='2026-08-03';assert bound(g) is None
