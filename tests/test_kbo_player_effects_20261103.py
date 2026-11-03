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
