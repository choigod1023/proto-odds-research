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


def test_prediction_never_reads_target_score():
 class Guard(dict):
  def __getitem__(self,key):
   if key in {'score','hg','ag','y'}:raise AssertionError('target leakage')
   return super().__getitem__(key)
 x=[str(i) for i in range(11)];g=Guard(game('q','2026-07-01','A','B',x,x));feat={'q':[[0,0,1,1],[0,0,1,1]]}
 state={'coeff':np.zeros(6),'teams':{'A':0,'B':1},'candidate':False}
 assert model.predict(state,[g],feat)[1].sum()==pytest.approx(1)


def test_frozen_predictions_have_hash_no_labels_and_sum_to_one():
 import hashlib,json
 root=data.ROOT/'frozen';m=json.loads((root/'manifest.json').read_bytes());b=(root/'predictions.jsonl').read_bytes()
 assert hashlib.sha256(b).hexdigest()==m['predictions_sha256']
 rows=[json.loads(s) for s in b.splitlines()]
 assert len(rows)==114 and len({r['fixture_id'] for r in rows})==114
 assert all(not ({'score','result','y','hg','ag'}&set(r)) for r in rows)
 for r in rows:
  for key in ('team_p','lineup_p'):
   assert len(r[key])==3 and sum(r[key])==pytest.approx(1,abs=1e-10) and all(0<=x<=1 for x in r[key])


def test_actual_future_xi_and_target_label_poison_cannot_change_earlier_prediction():
 import json
 games,_=data.load_inputs();_,_,_,test=data.partition(games)
 initial=model.history_features(games);first=test[0];last=test[-1]
 assert last['date']>first['date']
 poisoned=copy.deepcopy(games)
 for g in poisoned:
  if g['id']==last['id']:
   g['score']=[999999,999999];g['xi'][0]=[f'poison{i:03d}' for i in range(11)]
 later=model.history_features(poisoned);assert initial[first['id']]==later[first['id']]
 manifest=json.loads((data.ROOT/'frozen/manifest.json').read_bytes());state=manifest['states']['lineup'];state['coeff']=np.array(state['coeff'])
 a=model.predict(state,[first],initial)[1];b=model.predict(state,[first],later)[1]
 np.testing.assert_array_equal(a,b)


def test_scoring_refund_and_mismatched_product_fail_closed():
 import json,score
 frozen=[json.loads(x) for x in (data.ROOT/'frozen/predictions.jsonl').read_bytes().splitlines()];r=next(x for x in frozen if x['price'])
 games,_=data.load_inputs(scoring=True);g=next(x for x in games if x['id']==r['fixture_id'])
 code=score.winner(g['score']);q=r['price'];src={'settlement_code':'refund','official_score':g['score'],'gmId':q['product_id'][0],'gmTs':q['product_id'][1],'matchSeq':q['product_id'][2], 'prices':dict(zip(('home','draw','away'),q['odds']))}
 full,priced,excluded=score.score_rows([r],[g],{g['id']:src})
 assert len(full)==1 and not priced and excluded=={'refund_or_nonstandard_settlement':1}
 src['settlement_code']=str(code);src['matchSeq']=-1
 with pytest.raises(ValueError,match='frozen price mismatch'):score.score_rows([r],[g],{g['id']:src})
