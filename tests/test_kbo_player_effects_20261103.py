import importlib.util,sys,copy
from pathlib import Path
import numpy as np
from scipy.special import softmax
from scipy.stats import poisson
P=Path(__file__).resolve().parents[1]/'experiments/kbo-player-effects-20261103';sys.path.insert(0,str(P));import research as r

def row(fid,home='A',away='B'):
 names=['home_team_strength','away_team_strength','home_rest_days','away_rest_days','park_factor','home_lineup_strength','away_lineup_strength','home_starter_strength','away_starter_strength','home_bullpen_load','away_bullpen_load'];f={k:{'value':float(i)} for i,k in enumerate(names)};return {'fixture_id':fid,'season':2023,'event_date':'2023-04-01','home_id':home,'away_id':away,'features':f,'roster':{'home':{'hitters':[str(i) for i in range(9)],'starter':'p1'},'away':{'hitters':[str(i+9) for i in range(9)],'starter':'p2'}}}

def test_train_vocabulary_unknown_neutral_and_role_difference():
 a=row('a');spec=r.make_spec([a]);b=copy.deepcopy(a);b['roster']['home']['hitters'][0]='unseen';b['roster']['away']['starter']='never';x,n=r.design([b],spec,'M1');dense=x.toarray();assert dense[0,n:].sum()==sum(r.WEIGHTS[1:]);assert dense[0,n-3]>0 and dense[0,n-1]==1
 c=copy.deepcopy(a);c['roster']['home']['hitters'][0]='1';c['roster']['away']['starter']='1';x,_=r.design([c],spec,'M1');assert x.toarray()[0,n+len(spec['hitters']):].sum()==0

def test_m2_swap_rosters_reverses_player_columns():
 a=row('a');b=copy.deepcopy(a);b['home_id'],b['away_id']=a['away_id'],a['home_id'];b['roster']={'home':copy.deepcopy(a['roster']['away']),'away':copy.deepcopy(a['roster']['home'])};spec=r.make_spec([a]);x,n=r.design([a,b],spec,'M2');assert np.allclose(x.toarray()[0,n:],-x.toarray()[1,n:])

def test_zero_distinct_from_missing():
 a=row('a');b=copy.deepcopy(a);a['features']['home_lineup_strength']['value']=0.;b['features']['home_lineup_strength']['value']=None;spec=r.make_spec([row('s')]);x,_=r.design([a,b],spec,'M0');assert not np.array_equal(x.toarray()[0],x.toarray()[2])

def test_poisson_tail_and_softmax_mass():
 from scipy.stats import skellam
 h,a=4.2,3.6;p=np.array([skellam.sf(0,h,a),skellam.pmf(0,h,a),skellam.cdf(-1,h,a)]);assert abs(sum(p)-1)<1e-12
 joint=np.outer(poisson.pmf(np.arange(100),h),poisson.pmf(np.arange(100),a));assert abs(np.tril(joint,-1).sum()-p[0])<1e-12;assert abs(sum(softmax([1000.,-1000.,0.]))-1)<1e-12

def test_same_and_future_temporal_history_rejected():
 from datetime import date
 import pytest
 dates={'old':date(2023,4,1),'target':date(2023,4,2),'future':date(2023,4,3)}
 r.adapter.check_nested({'fixture_id':'old','date':'2023-04-01'},'target',dates['target'],dates)
 for bad in ('target','future'):
  with pytest.raises(ValueError):r.adapter.check_nested({'fixture_id':bad},'target',dates['target'],dates)

def test_train_only_vocab_and_standardization_and_seven_day_embargo():
 from datetime import date,timedelta
 a=row('a');b=copy.deepcopy(a);b['fixture_id']='new';b['season']=2024;b['event_date']='2024-04-05';b['roster']['home']['hitters'][0]='future_player';b['features']['home_team_strength']['value']=999.
 spec=r.make_spec([a]);assert 'future_player' not in spec['hitters'];assert 999. not in spec['mean'];assert len(r.cut_training([a,b],(2023,2024),'2024-04-10'))==1

def test_training_and_prediction_ignore_other_labels():
 rows=[];labels={}
 for i in range(25):
  x=row(str(i));x['event_date']=f'2023-04-{1+i:02d}';x['features']['home_team_strength']['value']=float(i%7);rows.append(x);labels[str(i)]={'home_score':i%5,'away_score':(i*2)%6}
 target=row('heldout');m1=r.fit(rows,labels,'M0',10.);p1=r.predict(m1,[target]);changed=copy.deepcopy(labels);changed['heldout']={'home_score':999,'away_score':0};m2=r.fit(rows,changed,'M0',10.);assert r.predict(m2,[target])==p1

def test_family_and_penalty_ties_are_stable():
 import select as st
 # Python stdlib select may already be imported: load the experiment by path instead.
 spec=importlib.util.spec_from_file_location('_effects_select_test',P/'select.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 results={str(i):{'metrics':{'logloss':1.}} for i in (1,10,100)};assert m.choose_penalty(results)==100
 assert m.choose_family({name:{'metrics':{'logloss':1.,'brier':.5}} for name in ('M0','M1','M2')})=='M0'

def test_pinned_inputs_and_contract():
 m=r.verify_inputs();assert m['contract_sha256']=='6ffdf7947559df052c248a1f9fb5e7ad871321f52e7920126934bf21771465ed'

def test_counterfactual_identity_changes_role_aware_predictions():
 a=row('a');spec=r.make_spec([a]);X,n=r.design([a],spec,'M1');beta=np.zeros(X.shape[1]);beta[0]=1.2;beta[n+spec['hitters'].index('0')]=.2;beta[n+len(spec['hitters'])+spec['starters'].index('p2')]=-.2
 model={'family':'M1','spec':spec,'beta':beta.tolist()};base=r.predict(model,[a])[0];b=copy.deepcopy(a);b['roster']['home']['hitters'][0]='other';change=r.predict(model,[b])[0];assert change['home_rate']!=base['home_rate'] and change['away_rate']==base['away_rate']
 c=copy.deepcopy(a);c['roster']['away']['starter']='unknown';change2=r.predict(model,[c])[0];assert change2['home_rate']!=base['home_rate'] and change2['away_rate']==base['away_rate']

def test_refund_roi_and_draw_decomposition():
 spec=importlib.util.spec_from_file_location('_effects_eval_test',P/'evaluate.py');e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
 z=[{'date':'2026-04-01','winner':'draw','forecasts':{'M0':{'net':'0','hit':False,'void':True,'probabilities':[.6,.4]}}},{'date':'2026-04-02','winner':'home','forecasts':{'M0':{'net':'1','hit':True,'void':False,'probabilities':[.6,.4]}}}];m=e.metric(z,'M0');assert m['bets']==2 and m['voids']==1 and m['roi_including_refunds']==.5
 spec=importlib.util.spec_from_file_location('_effects_draw_test',P/'draw_diagnostic.py');d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d);p=[{'fixture_id':'a','probabilities_3way':{'home':.5,'draw':.2,'away':.3},'probabilities_2way':{'home':.625,'away':.375}},{'fixture_id':'b','probabilities_3way':{'home':.5,'draw':.2,'away':.3},'probabilities_2way':{'home':.625,'away':.375}}];m=d.calc(p,{'a':{'home_score':1,'away_score':1},'b':{'home_score':2,'away_score':0}});import math;assert abs((m['draw_event_logloss_per_game']+m['decisive_direction_logloss_per_game'])-(-math.log(.2)-math.log(.5))/2)<1e-12

def test_same_day_outcome_scoring_order_is_deterministic():
 spec=importlib.util.spec_from_file_location('_effects_eval_order_test',P/'evaluate.py');e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
 def prediction(i):
  return {'fixture_id':str(i),'event_date':'2026-04-01','probabilities_3way':{'home':.4+i*.01,'draw':.1,'away':.5-i*.01}}
 sets={k:{str(i):prediction(i) for i in range(3)} for k in ('M0','M1','M2','old_fixed')};labels={str(i):{'home_score':i,'away_score':1} for i in range(3)}
 assert e.outcome4(sets,labels,set(['0','1','2']))==e.outcome4(sets,labels,set(['2','1','0']))
