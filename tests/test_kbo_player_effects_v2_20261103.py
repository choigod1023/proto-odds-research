import copy,importlib.util,sys
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parents[1]/'experiments/kbo-player-effects-20261103';sys.path.insert(0,str(P));sys.path.insert(0,str(P/'v2'));import research as v1;import research_v2 as v2

def row():
 names=('home_team_strength','away_team_strength','home_rest_days','away_rest_days','park_factor','home_lineup_strength','away_lineup_strength','home_starter_strength','away_starter_strength','home_bullpen_load','away_bullpen_load');return {'fixture_id':'game','season':2023,'event_date':'2023-04-01','home_id':'A','away_id':'B','features':{k:{'value':0.} for k in names},'roster':{'home':{'hitters':[str(i) for i in range(9)],'starter':'p1'},'away':{'hitters':[str(i+9) for i in range(9)],'starter':'p2'}}}

def test_paired_roster_absent_versus_unseen_no_longer_collide():
 train=row();spec=v1.make_spec([train]);a=copy.deepcopy(train);b=copy.deepcopy(train)
 for side in ('home','away'):
  a['roster'][side]={'hitters':None,'starter':None};b['roster'][side]={'hitters':['u'+side+str(i) for i in range(9)],'starter':'unknown'+side}
 x,_=v1.design([a,b],spec,'M2');assert np.array_equal(x.toarray()[0],x.toarray()[1]);x,n=v2.design([a,b],spec);assert not np.array_equal(x.toarray()[0],x.toarray()[1]);assert n==v1.design([a],spec,'M2')[1]+12

def test_paired_null_versus_observed_zero_and_mean_no_longer_collide():
 a=row();spec=v1.make_spec([a]);b=copy.deepcopy(a)
 for side in ('home','away'):b['features'][side+'_lineup_strength']['value']=None
 x,_=v1.design([a,b],spec,'M2');assert np.array_equal(x.toarray()[0],x.toarray()[1]);x,_=v2.design([a,b],spec);assert not np.array_equal(x.toarray()[0],x.toarray()[1])

def test_single_side_missing_and_unknown_are_distinct_and_quality_swap_invariant():
 a=row();spec=v1.make_spec([a]);b=copy.deepcopy(a);b['roster']['home']={'hitters':None,'starter':None};c=copy.deepcopy(a);c['roster']['home']={'hitters':['u'+str(i) for i in range(9)],'starter':'unknown'}
 assert not np.array_equal(v2.design([b,c],spec)[0].toarray()[0],v2.design([b,c],spec)[0].toarray()[1]);sw=copy.deepcopy(b);sw['home_id'],sw['away_id']=b['away_id'],b['home_id'];sw['roster']={'home':copy.deepcopy(b['roster']['away']),'away':copy.deepcopy(b['roster']['home'])};orig,n=v1.design([b,sw],spec,'M2');assert np.allclose(orig.toarray()[0,1:],-orig.toarray()[1,1:]);assert np.array_equal(v2.quality_sums(b,spec),v2.quality_sums(sw,spec))

def test_correction_does_not_change_original_contrasts_or_player_exposures():
 a=row();spec=v1.make_spec([a]);x,n=v1.design([a],spec,'M2');z,m=v2.design([a],spec);assert np.array_equal(x.toarray()[:,:n],z.toarray()[:,:n]);assert np.array_equal(x.toarray()[:,n:],z.toarray()[:,m:]);assert v2.verify_contract()['no_new_candidate_family'] is True
