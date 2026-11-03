import copy,importlib.util,sys
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'experiments'/'kbo-player-model-20261103';sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('kbo_model',P/'model.py');m=importlib.util.module_from_spec(spec);sys.modules['kbo_model']=m;spec.loader.exec_module(m)
spec=importlib.util.spec_from_file_location('kbo_run',P/'run.py');r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

def synthetic():
    rows=[];labels=[]
    for year,n in [(2024,100),(2025,30),(2026,30)]:
        for i in range(n):
            kickoff=datetime(year,1,1,9,tzinfo=timezone.utc)+timedelta(days=3*i);decision=kickoff-timedelta(hours=1)
            iso=lambda x:x.isoformat();fid=f'{year}-{i}';sid='source';hist=iso(decision-timedelta(days=3));available=iso(decision-timedelta(days=1))
            sources=[{'source_id':sid,'path_or_url':'synthetic','sha256':'a'*64,'received_at':available,'availability_basis':'synthetic_received'}]
            feats={}
            for j,name in enumerate(m.FEATURES):
                feats[name]={'value': None if (i+j)%7==0 else float(((i+j)%9)/10), 'available_at':available,'availability_basis':'synthetic','source_id':sid}
                if name in m.HISTORY_FEATURES:feats[name].update(history_max_completed_at=hist,history_fixture_ids=['prior'])
            rows.append({'fixture_id':fid,'league':'KBO','season':year,'kickoff_at':iso(kickoff),'decision_at':iso(decision),'home_id':'H'+str(i%4),'away_id':'A'+str(i%4),'availability_tier':'point_in_time_verified','sources':sources,'features':feats,'market':{'market_id':fid,'kind':'full_game_2way','selections':{'home':'1.8','away':'2.0'},'odds_observed_at':available,'source_id':sid,'settlement_rule':'draw_refund'}})
            labels.append({'fixture_id':fid,'status':'final','home_score':i%6,'away_score':(i*2)%7,'completed_at':iso(kickoff+timedelta(hours=3)),'received_at':iso(kickoff+timedelta(hours=4)),'revision':'r1','market_id':fid,'settlement_rule':'draw_refund'})
    return rows,labels

@pytest.fixture(scope='module')
def prepared():
    rows,labels=synthetic();dev=[x for x in labels if not x['fixture_id'].startswith('2026-')]
    return rows,labels,r.prepare(rows,dev)

def test_probabilities_tail_and_draw():
    for a,b in [(1,1),(4,5),(15,2),(0.1,0.2)]:
        p=m.distribution(a,b);assert abs(sum(p[k] for k in ('home','draw','away'))-1)<1e-12 and p['tail_upper_bound']<=1e-10
    assert m.distribution(2,2)['home']==pytest.approx(m.distribution(2,2)['away'])

def test_split_rejects_test_labels_and_future_history():
    rows,labels=synthetic()
    with pytest.raises(ValueError,match='test labels forbidden'):m.split(rows,labels)
    dev=[x for x in labels if not x['fixture_id'].startswith('2026-')]
    blocks=m.split(rows,dev);assert list(map(len,(blocks['train'],blocks['validation'],blocks['test'])))==[100,30,30]
    bad=copy.deepcopy(rows);bad[0]['features']['home_team_strength']['value']=0.1;bad[0]['features']['home_team_strength']['history_fixture_ids']=[bad[0]['fixture_id']]
    with pytest.raises(ValueError,match='leakage'):m.split(bad,dev)
    bad=copy.deepcopy(rows);bad[0]['features']['home_team_strength']['value']=0.1;bad[0]['features']['home_team_strength']['history_max_completed_at']=None
    with pytest.raises(ValueError,match='leakage'):m.validate_rows(bad)
    bad=copy.deepcopy(rows);bad[0]['features']['home_team_strength']['value']=0.1;bad[0]['features']['home_team_strength']['available_at']=bad[0]['kickoff_at']
    with pytest.raises(ValueError,match='future'):m.validate_rows(bad)
    bad=copy.deepcopy(rows);bad.append(copy.deepcopy(bad[0]))
    with pytest.raises(ValueError,match='duplicate'):m.validate_rows(bad)

def test_training_feature_mask_and_prediction_contract(prepared):
    rows,labels,p=prepared;assert p['counts']=={'train':100,'validation':30,'test':30}
    assert set(p['models'])==set(m.MODES) and all(len(v)==3 for v in p['validation_grid'].values())
    for forecasts in p['predictions'].values():
        assert len(forecasts)==30
        for item in forecasts:
            assert sum(item['probabilities_2way'].values())==pytest.approx(1)
            assert sum(item['probabilities_3way'][k] for k in ('home','draw','away'))==pytest.approx(1)
            assert item['probabilities_3way']['tail_upper_bound']<=1e-10
            assert isinstance(item['missing_features'],list)

def test_labels_cannot_change_frozen_predictions_and_refunds(prepared):
    rows,labels,p=prepared;test=[x for x in labels if x['fixture_id'].startswith('2026-')];before=copy.deepcopy(p)
    test[0]['away_score']=test[0]['home_score'];out,ledger=r.report(rows,p,test)
    assert p==before and ledger['team'][0]['forecasts']['team']['void']
    assert ledger['team'][0]['forecasts']['team']['net']=='0'
    assert out['models']['team']['candidate']['voids']>=1
    assert out['models']['team']['candidate']['roi_including_refunds'] is not None

def test_missing_market_and_unverified_omitted(prepared):
    rows,labels,p=prepared;test=[x for x in labels if x['fixture_id'].startswith('2026-')];bad=copy.deepcopy(rows)
    i=next(i for i,x in enumerate(bad) if x['fixture_id'].startswith('2026-'));bad[i]['market']=None
    out,_=r.report(bad,p,test);assert out['models']['player']['matched_price_final']==29

def test_unseen_team_and_target_outcome_rejected(prepared):
    rows,labels,p=prepared;model=m.fit(rows[:100],labels[:100],'team',10);target=copy.deepcopy(rows[-1]);target['home_id']='never_seen'
    pred=m.predict(model,[target])[0];assert pred['unknown_team']
    target['result']='home'
    with pytest.raises(ValueError,match='result'):m.predict(model,[target])

def test_observed_zero_is_not_missing():
    rows,labels=synthetic();rows[0]['features']['home_team_strength']['value']=0.;model=m.fit(rows[:100],labels[:100],'team',10)
    assert np.isfinite(model.beta).all();assert 'home_team_strength' not in m.predict(model,[rows[0]])[0]['missing_features']

def test_three_way_draw_is_a_selection(prepared):
    rows,labels,p=prepared;test=[x for x in labels if x['fixture_id'].startswith('2026-')];bad=copy.deepcopy(rows)
    i=next(i for i,x in enumerate(bad) if x['fixture_id'].startswith('2026-'));fixture=bad[i];fixture['market']['kind']='full_game_3way';fixture['market']['selections']['draw']='3.1';fixture['market']['settlement_rule']='draw_selection'
    matching=next(x for x in test if x['fixture_id']==fixture['fixture_id']);matching['home_score']=matching['away_score'];matching['settlement_rule']='draw_selection'
    out,ledger=r.report(bad,p,test);first=next(x for x in ledger['team'] if x['fixture_id']==fixture['fixture_id'])
    assert not first['forecasts']['team']['void'];assert len(first['forecasts']['team']['probabilities'])==3
    assert out['pairwise']['player_vs_team']['weeks']>0

def test_output_freeze_refuses_overwrite(tmp_path):
    path=tmp_path/'freeze.json';r.write_once(path,{'sample':'synthetic'});before=path.read_bytes()
    with pytest.raises(FileExistsError):r.write_once(path,{'changed':True})
    assert path.read_bytes()==before

def test_unverified_price_never_enters_roi(prepared):
    rows,labels,p=prepared;test=[x for x in labels if x['fixture_id'].startswith('2026-')];bad=copy.deepcopy(rows)
    row=next(x for x in bad if x['fixture_id'].startswith('2026-'));row['market']['odds_observed_at']=None
    out,_=r.report(bad,p,test);assert out['models']['player']['matched_price_final']==29


def test_cli_entrypoint_without_pythonpath():
    import os, subprocess
    env=dict(os.environ);env.pop("PYTHONPATH",None)
    cmd=[sys.executable,str(P/"run.py"),"--help"]
    result=subprocess.run(cmd,cwd=ROOT,env=env,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert "predict" in result.stdout and "score" in result.stdout

def test_retrospective_missing_label_receipt_is_explicit_and_not_verified():
    rows,labels=synthetic()
    for row in rows:row['availability_tier']='retrospective_unproven'
    dev=[x for x in labels if not x['fixture_id'].startswith('2026-')]
    for label in dev:label['received_at']=None;label['availability_basis']='retrospective_completion_assumption'
    assert len(m.split(rows,dev)['train'])==100
    rows[0]['availability_tier']='point_in_time_verified'
    with pytest.raises(ValueError,match='tiers'):m.split(rows,dev)
    for row in rows:row['availability_tier']='point_in_time_verified'
    with pytest.raises(ValueError,match='receipt'):m.split(rows,dev)


def test_prior_adapter_excludes_target_features_and_assumes_only_prior_days():
    import importlib.util
    spec=importlib.util.spec_from_file_location('kbo_adapter',P/'adapter.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
    raw={'fixture_id':'20240412HHWO02024','league':'KBO','season':2024,'kickoff_at':'2024-04-12T18:30:00+09:00','decision_at':'2024-04-12T18:00:00+09:00','home_id':'A','away_id':'B','market':None,'availability_tier':'retrospective_unproven','sources':[],'features':{'actual_target_player_id':'secret','target_score':99}}
    before=a.sanitize_raw_metadata(raw);raw['features']={'actual_target_player_id':'poison','target_score':0}
    assert before==a.sanitize_raw_metadata(raw) and 'features' not in before
    dates={'prev':date(2024,4,11),'same':date(2024,4,12),'future':date(2024,4,13)}
    a.check_nested({'source_fixture_ids':['prev'],'date':'2024-04-11'},raw['fixture_id'],date(2024,4,12),dates)
    for item in ({'fixture_id':raw['fixture_id']},{'source_fixture_ids':['same']},{'fixture_id':'future'},{'date':'2024-04-12'}):
        with pytest.raises(ValueError):a.check_nested(item,raw['fixture_id'],date(2024,4,12),dates)
    assert a.scalar('home_starter_strength',{'appearances':[{'er':3,'outs':17}]})==pytest.approx(81/17)
    assert a.scalar('home_bullpen_load',{'games':[{'relief':{'outs':17}}]})==17
    assert a.scalar('home_lineup_strength',[{'history':[{'ab':4,'hit':2,'bb':1}]}])==pytest.approx(.6)


def test_date_only_retrospective_never_passes_strict_or_fabricates_receipts():
    rows,labels=synthetic(); row=copy.deepcopy(rows[0]);row['availability_tier']='retrospective_unproven';row['event_date']=row['kickoff_at'][:10]
    row['kickoff_at']=row['decision_at']=None;row['decision_time_basis']='unknown';row['identity_basis']='archive';row['market']=None
    for source in row['sources']:source['received_at']=None
    for feat in row['features'].values():
        feat['available_at']=None;feat['availability_basis']='prior_calendar_day_assumed_completed_not_received';feat['history_max_completed_at']=None;feat['history_latest_date']='2023-12-31'
    m.validate_rows([row],allow_retrospective_dates=True)
    with pytest.raises(ValueError):m.validate_rows([row])
    row['features']['home_team_strength']['value']=1;row['features']['home_team_strength']['history_latest_date']=row['event_date']
    with pytest.raises(ValueError,match='date-only'):m.validate_rows([row],allow_retrospective_dates=True)

def test_archive_price_is_explicit_and_never_promoted_to_verified(prepared):
    rows,labels,p=prepared; rows=copy.deepcopy(rows); labels=copy.deepcopy(labels)
    for row in rows:
        row['availability_tier']='retrospective_unproven';row['event_date']=row['kickoff_at'][:10]
        row['decision_time_basis']='hypothetical';row['identity_basis']='archive'
        row['market']['odds_observed_at']=None;row['market']['settlement_rule']='draw_void_refund'
        for source in row['sources']:source['received_at']=None
    for label in labels:label['settlement_rule']='draw_void_refund'
    target=[x for x in labels if x['fixture_id'].startswith('2026-')]
    verified,_=r.report(rows,p,target,assume_prior_calendar_day=True)
    assert verified['models']['player']['matched_price_final']==0
    assert verified['models']['player']['candidate']['roi_including_refunds'] is None
    archive,_=r.report(rows,p,target,assume_prior_calendar_day=True,archived_price_scenario=True)
    assert archive['models']['player']['matched_price_final']==30
    assert archive['price_scenario']=='archived_assumed_not_verified'
    with pytest.raises(ValueError,match='requires retrospective'):r.report(rows,p,target,archived_price_scenario=True)
