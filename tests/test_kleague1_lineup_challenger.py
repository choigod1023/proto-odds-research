import copy,datetime,sys
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path('experiments/kleague1-lineup-challenger-20261103').resolve()))
import data, model


def game(id,day,home,away,hxi,axi,score=(1,0)):
 return {'id':id,'date':datetime.date.fromisoformat(day),'teams':[home,away],'xi':[hxi,axi],'score':list(score)}


def test_history_excludes_same_day_and_future_label_poison():
 x=[str(i) for i in range(11)];z=[str(i) for i in range(1,12)]
 games=[game('a','2025-01-01','A','B',x,x),game('b','2025-01-02','A','C',z,x),game('c','2025-01-02','A','D',x,x),game('d','2025-01-03','A','B',x,x)]
 feats=model.history_features(games)
 assert feats['b'][0][0]==pytest.approx(10/11) and feats['c'][0][0]==1
 assert feats['d'][0][0]==1 and feats['d'][0][1]==1
 poisoned=copy.deepcopy(games);poisoned[2]['score']=[999,999];poisoned[3]['score']=[0,9]
 assert feats==model.history_features(poisoned)
 altered=copy.deepcopy(games);altered[-1]['xi'][0]=z
 assert feats['a']==model.history_features(altered)['a']


def test_unknown_teams_and_probability_normalization():
 x=[str(i) for i in range(11)];g=game('x','2026-01-01','new','B',x,x)
 feats={'x':[[0,0,1,1],[0,0,1,1]]};X,unknown=model.design([g],feats,{'B':0},False)
 assert unknown==[[True,False]] and X[0,2]==0
 state={'coeff':np.zeros(4),'teams':{'B':0},'candidate':False}
 _,p,_=model.predict(state,[g],feats);assert p.shape==(1,3) and p.sum()==pytest.approx(1)


def test_real_cohort_is_complete_and_test_outcomes_removed():
 games,prices=data.load_inputs();train,val,embargo,test=data.partition(games)
 assert (len(games),len(train),len(val),len(embargo),len(test))==(432,228,90,0,114)
 assert len(prices)==412 and sum(x['id'] in prices for x in test)==113
 assert all('score' not in g for g in test)
 assert max(g['date'] for g in train)<=data.TRAIN_CUTOFF and max(g['date'] for g in val)<=data.CUTOFF


def test_market_identity_invalid_market_refund_and_kickoff_rejected():
 import gzip,json
 with gzip.open(data.INPUT/'prices.jsonl.gz','rt',encoding='utf8') as f:r=next(json.loads(x) for x in f if 'kleague:2025:1:1' in x)
 games,_=data.load_inputs();g=next(x for x in games if x['id']==r['fixture_id'])
 assert data.eligible_price(g,r) is not None
 for changed in ({'gameReject':'1'},{'buyReject':'1'},{'home_team_id':'wrong'},{'kickoff_kst':'2025-01-01T13:00:00+09:00'},{'gmId':'G103'}):
  assert data.eligible_price(g,{**r,**changed}) is None
 assert data.eligible_price(g,{**r,'market_type':{**r['market_type'],'handi':1}}) is None
 assert data.eligible_price(g,{**r,'market_type':{**r['market_type'],'gameSubject':'ET'}}) is None


def test_model_rejects_training_after_cutoff():
 x=[str(i) for i in range(11)];g=game('x','2026-06-25','A','B',x,x);features=model.history_features([g])
 with pytest.raises(ValueError,match='embargo'):model.fit([g],features,1,False,data.CUTOFF)
