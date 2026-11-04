import importlib.util
import math
from pathlib import Path

spec=importlib.util.spec_from_file_location('audit',Path(__file__).resolve().parents[1]/'scripts/audit_m1_frozen_contract.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)

def test_null_zero_and_role_effects():
 assert audit.scalar('home_rest_days',0)==0
 assert audit.scalar('home_rest_days',None) is None
 assert audit.scalar('park_factor',42) is None
 assert audit.scalar('home_rest_days',True) is None
 # Missing mean-imputes to zero but retains an indicator; observed zero differs.
 row={'home_id':'H','away_id':'A'}
 names=['home_team_strength','away_team_strength','home_rest_days','away_rest_days','park_factor','home_lineup_strength','away_lineup_strength','home_starter_strength','away_starter_strength','home_bullpen_load','away_bullpen_load']
 src={'features':{x:{'value':None} for x in names},'projection':{}}
 for s in ('home','away'):
  src['projection'][s+'_lineup']={'player_ids':None};src['projection'][s+'_starter']={'player_id':None}
 m={'family':'M1','spec':{'teams':['A','H'],'hitters':['p'],'starters':['p'],'mean':[0]*8,'scale':[1]*8},'nbase':26,'beta':[0]*28}
 # Base size = intercept/home + four team indicators + 16 scalars + 4 flags.
 p,r=audit.manual(row,src,'projected',m)
 assert r==[1,1] and math.isclose(p[0],p[2],abs_tol=1e-14)
 m['beta'][26]=1
 src['projection']['home_lineup']['player_ids']=['p']+['u']*8
 p,r=audit.manual(row,src,'projected',m)
 assert math.isclose(r[0],math.exp(4.7/4.3)) and r[1]==1
 m['beta'][26]=0;m['beta'][27]=1
 src['projection']['away_starter']['player_id']='p'
 p,r=audit.manual(row,src,'projected',m)
 assert math.isclose(r[0],math.e) and r[1]==1

def test_refund_and_empty_subgroups():
 items=[{'net':'0','hit':False,'void':True,'different':False},{'net':'0.20','hit':True,'void':False,'different':True}]
 assert audit.stats(items)=={'n':2,'hits':1,'voids':1,'net':'0.20','roi':0.1,'m1_m0_different':1}
 assert audit.stats([])['roi'] is None
