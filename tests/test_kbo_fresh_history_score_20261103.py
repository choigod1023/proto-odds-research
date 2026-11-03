import copy,importlib.util,sys
from pathlib import Path
import pytest
P=Path(__file__).resolve().parents[1]/'experiments/kbo-player-model-20261103'
def module(name,file):
 spec=importlib.util.spec_from_file_location(name,P/file);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m
s=module('fresh_score_test','fresh_history_score.py');overlay=module('fresh_phase3_test','phase3_overlay.py')
def prediction(home):return {'probabilities_3way':{'home':home,'draw':.1,'away':.9-home},'probabilities_2way':{'home':home/.9,'away':(.9-home)/.9}}
def fixtures():
 os={};fs={};ys={}
 for fid,day,h,a in [('a','2026-07-20',0,1),('b','2026-08-03',1,1)]:
  old=prediction(.6);fresh=prediction(.3);os[fid]={'fixture_id':fid,'event_date':day,'cohort':'new_complement','settings':{k:copy.deepcopy(old) for k in s.SETTINGS},'frozen_team':copy.deepcopy(old)};fs[fid]={'fixture_id':fid,'event_date':day,'cohort':'new_complement','settings':{k:copy.deepcopy(fresh) for k in s.SETTINGS}};ys[fid]={'home_score':h,'away_score':a}
 return fs,os,ys

def test_no_labels_read_before_both_frozen_predictions(monkeypatch):
 seen=[]
 def fake(path,sha,commit,gitpath):seen.append(gitpath);raise ValueError('bad frozen prediction')
 monkeypatch.setattr(s,'frozen',fake)
 with pytest.raises(ValueError,match='bad frozen'):s.load('bad','bad')
 assert len(seen)==1 and seen[0].endswith('fresh-history/predictions.json')

def test_matched_accuracy_refund_and_roi():
 fs,os,labels=fixtures();ids=set(fs);result=s.threeway(fs,os,labels,ids)
 assert result['models']['old_original_projected']['hits']==0 and result['models']['fresh_original_projected']['hits']==1
 assert result['fresh_vs_corresponding_old']['original_projected']['changed_2way_picks']==2
 pa=[2/3,1/3];led=[]
 for fid,day,winner in [('a','2026-07-20','away'),('b','2026-08-03','draw')]:
  base={'probabilities':pa,'pick':'home','void':winner=='draw','hit':False,'net':'0' if winner=='draw' else '-1'};led.append({'fixture_id':fid,'date':day,'winner':winner,'price_source':{'origin':'original_archived','kickoff_matched':True,'prices':{'home':'2','away':'3'}},'forecasts':{'player':base,'team':base,'market':base}})
 r,_=s.price_results(fs,os,labels,ids,{'ledgers':{'strict':led}},overlay);m=r['strict']['models']['fresh_original_projected'];assert m['hits']==1 and m['voids']==1 and m['net_units']=='2' and m['roi_including_refunds']==1
