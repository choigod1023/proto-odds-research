import copy,importlib.util,inspect,sys
from pathlib import Path
import pytest
P=Path(__file__).resolve().parents[1]/'experiments/kbo-player-model-20261103'
def module(name,file):
 spec=importlib.util.spec_from_file_location(name,P/file);x=importlib.util.module_from_spec(spec);sys.modules[name]=x;spec.loader.exec_module(x);return x
s=module('factorial_score_test','factorial_score.py');pred=module('factorial_prediction_test','factorial_predict.py');overlay=module('factorial_overlay_test','phase3_overlay.py')
def forecast(p):return {'probabilities_3way':{'home':p,'draw':.1,'away':.9-p},'probabilities_2way':{'home':p/.9,'away':(.9-p)/.9}}
def sample():
 a=forecast(.6);b=forecast(.3);x={'fixture_id':'x','event_date':'2026-04-01','settings':{k:copy.deepcopy(a if k=='original_projected' else b) for k in s.NAMES},'frozen_team':copy.deepcopy(a)};y=copy.deepcopy(x);y['fixture_id']='y';y['event_date']='2026-04-09';return [x,y],{'x':{'home_score':0,'away_score':1},'y':{'home_score':1,'away_score':1}}
def test_freeze_checked_before_label_access(monkeypatch):
 seen=[]
 def reject(path,sha):seen.append(str(path));raise ValueError('hash mismatch')
 monkeypatch.setattr(s,'checked',reject)
 with pytest.raises(ValueError,match='hash mismatch'):s.load()
 assert len(seen)==1 and 'factorial' in seen[0] and 'predictions.json' in seen[0]
def test_synthetic_scores_and_void_refund():
 xs,labels=sample();out=s.evaluate(xs,labels);assert out['models']['original_projected']['hits']==0;assert out['models']['official_batting_only']['hits']==1;assert out['versus_original']['official_batting_only']['changed_2way_picks']==2
 pa=[2/3,1/3];old=[]
 for fid,day,winner in [('x','2026-04-01','away'),('y','2026-04-09','draw')]:
  old.append({'fixture_id':fid,'date':day,'winner':winner,'price_source':{'origin':'original_archived','kickoff_matched':True,'prices':{'home':'2','away':'3'}},'forecasts':{'player':{'probabilities':pa},'team':{'probabilities':pa,'pick':'home','void':winner=='draw','hit':False,'net':'0' if winner=='draw' else '-1'},'market':{'probabilities':pa,'pick':'home','void':winner=='draw','hit':False,'net':'0' if winner=='draw' else '-1'}}})
 price,_=s.price_results(xs,labels,{'ledgers':{'strict':old}},overlay);m=price['strict']['models']['official_batting_only'];assert m['hits']==1 and m['voids']==1 and m['net_units']=='2' and m['roi_including_refunds']==1

def test_training_calls_blocked_and_no_outcome_read_in_runner():
 text=inspect.getsource(pred.build);assert 'model.fit=m.forbid_training' in text and 'model.minimize=m.forbid_training' in text
 assert 'test-labels' not in inspect.getsource(pred.load) and 'test-labels' not in text
