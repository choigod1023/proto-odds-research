import copy
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.optimize import check_grad

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from backtest_baseball_league_fit import (PastState, build_features, decay_weights,
    fit_model, innings_outs, loss_gradient, measure, predict, select, win_prob)
from fetch_baseball_research_results import fetch, official_fact


@pytest.mark.parametrize('value,expected', [('6',18),('4 ⅓',13),('0 ⅔',2),('5.2',17)])
def test_innings_are_outs_not_decimal(value, expected):
    assert innings_outs(value) == expected


@pytest.mark.parametrize('distribution', ['poisson','nb'])
def test_score_model_gradient_matches_numeric(distribution):
    x = np.array([[1.,-.7],[1.,.3],[1.,1.2]])
    y = np.array([0.,3.,8.]); weights = np.array([.2,.3,.5])
    theta = np.array([1.1,.2]+([1.4] if distribution=='nb' else []))
    error = check_grad(lambda t: loss_gradient(t,x,y,weights,distribution)[0],
                       lambda t: loss_gradient(t,x,y,weights,distribution)[1],theta)
    assert error < 1e-5


def fixture(day, home='LG', away='KT', hg=2, ag=3):
    return dict(league='KBO',date=day,home=home,away=away,hg=hg,ag=ag,
                actual_starters=['A','B'],pitchers=[[['A','5 ⅓',1,2,5,4,85]],[['B','6',2,1,4,5,90]]],
                batters=[[[str(i),i,4,1,0,0,0,0] for i in range(1,10)],
                         [[str(i+10),i,4,2,1,0,0,0] for i in range(1,10)]])


def test_same_day_current_starter_score_and_box_cannot_leak():
    rows = [fixture('2023-04-01'),fixture('2023-04-06'),fixture('2023-04-06','KT','LG')]
    before = build_features(rows,'KBO',20)
    changed = copy.deepcopy(rows)
    for r in changed[1:]:
        r.update(hg=99,ag=98,actual_starters=['LEAK','LEAK'],pitchers=[[],[]],batters=[[],[]])
    after = build_features(changed[::-1],'KBO',20)
    assert [r['features'] for r in before] == [r['features'] for r in after]
    assert [r['metadata'] for r in before] == [r['metadata'] for r in after]


def test_rotation_uses_only_past_and_raw_bf_not_used_as_batters_faced():
    a = fixture('2023-04-01'); b = copy.deepcopy(a)
    b['pitchers'][0][0][-1] = 99999
    s1,s2 = PastState('KBO',20),PastState('KBO',20)
    s1.update(a);s2.update(b)
    from datetime import date
    f1,m1 = s1.offense('KT','LG',date(2023,4,6),False)
    f2,m2 = s2.offense('KT','LG',date(2023,4,6),False)
    assert m1['projected_starter'] == 'A'
    assert f1 == f2 and m1 == m2
    assert s1.pitch[('LG','A')][-1] == 16+4+2


def test_stale_detail_falls_back_and_reports_missing():
    from datetime import date
    s = PastState('KBO',20);s.update(fixture('2023-04-01'))
    f,m = s.offense('KT','LG',date(2023,4,20),False)
    assert not m['pitch_detail_known'] and m['lineup_players'] == 0
    assert f['starter_ra9'] == 0 and f['lineup_obp_proxy'] == 0


def test_decay_half_life_and_uniform():
    np.testing.assert_allclose(decay_weights(np.array([0,90]),90),[1/3,2/3])
    np.testing.assert_allclose(decay_weights(np.array([0,90]),0),[.5,.5])


@pytest.mark.parametrize('distribution,shape',[('poisson',None),('nb',2.)])
def test_win_distribution_symmetry_and_monotonicity(distribution,shape):
    assert win_prob(4,4,distribution,shape) == pytest.approx(.5)
    assert win_prob(6,3,distribution,shape) > .5
    assert win_prob(6,3,distribution,shape)+win_prob(3,6,distribution,shape) == pytest.approx(1)


def test_draw_is_refund_and_does_not_lower_hit_rate():
    rows=[dict(hg=2,ag=2,date='2024-04-01',prices=[1.5,2.2],order=['home','away']),
          dict(hg=3,ag=2,date='2024-04-02',prices=[1.5,2.2],order=['home','away'])]
    m=measure(rows,[.8,.7])
    assert m['accuracy']==1 and m['pushes']==1 and m['roi']==.25 and m['net_krw']==5000


def test_selection_uses_validation_only_and_has_distinct_objectives():
    scores={'a':dict(accuracy=.6,brier=.24),'b':dict(accuracy=.55,brier=.22)}
    assert select(scores)==dict(hit='a',brier='b')
    scores['invalid']=dict(accuracy=1.,brier=0.,eligible=False)
    assert select(scores)==dict(hit='a',brier='b')


def test_training_cutoff_is_immune_to_future_scores():
    rows=[fixture('2023-04-01'),fixture('2023-04-06'),fixture('2024-04-01')]
    features=build_features(rows,'KBO',20)
    changed=copy.deepcopy(features);changed[-1]['row'].update(hg=90,ag=90)
    a=fit_model(features,['home'],'poisson',0,2023)
    b=fit_model(changed,['home'],'poisson',0,2023)
    assert a==b and a['train_games']==2
    assert len(predict(a,features[-1:]))==1


def test_official_feed_excludes_spring_and_final_without_score():
    g=dict(gamePk=1,gameType='R',status=dict(abstractGameState='Final'),gameDate='2024-04-01T18:00:00Z',
        teams=dict(home=dict(score=3,team=dict(name='A')),away=dict(score=2,team=dict(name='B'))))
    assert official_fact(g,'url')['game_type']=='R'
    spring=copy.deepcopy(g);spring['gameType']='S'
    assert official_fact(spring,'url') is None
    missing=copy.deepcopy(g);del missing['teams']['home']['score']
    assert official_fact(missing,'url') is None


def test_start_interval_is_calendar_gap_learned_from_past_only():
    from datetime import date
    s=PastState('KBO',20)
    s.update(fixture('2023-04-01'))
    s.update(fixture('2023-04-02',hg=3))
    s.teams['LG'][-1]['starter']='C'
    s.start_intervals.extend([6]*20)
    name,rest=s.projected_starter('LG',date(2023,4,7))
    assert (name,rest)==('A',6)


def test_suspended_final_cannot_be_counted_on_original_and_resume_day(tmp_path,monkeypatch):
    import json
    first=dict(gamePk=1,gameType='R',status=dict(abstractGameState='Final'),gameDate='2024-04-01T18:00:00Z',
        teams=dict(home=dict(score=3,team=dict(name='A')),away=dict(score=2,team=dict(name='B'))))
    resumed=copy.deepcopy(first);resumed['gameDate']='2024-04-02T18:00:00Z'
    class Response:
        url='https://statsapi.mlb.com/api/v1/schedule'
        def raise_for_status(self):pass
        def json(self):return dict(dates=[dict(games=[first]),dict(games=[resumed])])
    class Session:
        def get(self,*args,**kwargs):return Response()
    monkeypatch.setattr('fetch_baseball_research_results.requests.Session',Session)
    fetch(tmp_path,[2024])
    assert (tmp_path/'mlb-official-final.jsonl').read_text(encoding='utf8')==''
    assert len((tmp_path/'mlb-ambiguous-suspended.jsonl').read_text(encoding='utf8').splitlines())==2
    assert json.loads((tmp_path/'mlb-fetch-audit.json').read_text())['skipped_missing_score']['ambiguous_original_resumption_gamePk']==1
