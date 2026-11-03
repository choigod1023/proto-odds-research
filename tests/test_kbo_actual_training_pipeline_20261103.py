import copy,sys
from datetime import date,timedelta
from pathlib import Path
import numpy as np
import pytest
P=Path(__file__).resolve().parents[1]/'experiments/kbo-actual-training-20261103';sys.path.insert(0,str(P));import pipeline as m;import source_adapter as source

def row(fid='g',day='2024-04-01'):
 return {'fixture_id':fid,'season':int(day[:4]),'event_date':day,'home_id':'A','away_id':'B','features':{k:{'value':0.} for k in m.FEATURES},'roster':{'home':{'hitters':['h'+str(i) for i in range(9)],'starter':'p1'},'away':{'hitters':['a'+str(i) for i in range(9)],'starter':'p2'}}}
def empty_entry():return {'availability_basis':'prior_calendar_day_assumed_completed_not_received','completed_at':None,'received_at':None,'value':None,'source_fixture_ids':[],'latest_source_date':None}
def test_pinned_original_models():
 m.verify_pinned_code();models=m.frozen_context_models();assert models['oldfull_M0']['family']=='M0' and models['oldfull_M2']['player_l2']==100.
def test_matched_cohort_nonplayer_and_extra_target_fields():
 a=row();b=copy.deepcopy(a);b['roster']['home']['hitters'][0]='other';assert len(m.validate_pairs([a],[b])['projected'])==1
 c=copy.deepcopy(b);c['features']['home_rest_days']['value']=1
 with pytest.raises(ValueError,match='nonplayer'):m.validate_pairs([a],[c])
 c=copy.deepcopy(b);c['features']['home_bullpen_load']['value']=1
 with pytest.raises(ValueError,match='bullpen'):m.validate_pairs([a],[c])
 c=copy.deepcopy(b);c['target_score']=5
 with pytest.raises(ValueError,match='extra target'):m.validate_pairs([a],[c])
 with pytest.raises(ValueError,match='fixture sets'):m.validate_pairs([a],[])
def test_actual_roster_must_be_unique_and_known():
 a=row();b=copy.deepcopy(a);b['roster']['home']['starter']=None
 with pytest.raises(ValueError,match='actual roster identity'):m.validate_pairs([a],[b])
 b=copy.deepcopy(a);b['roster']['away']['hitters'][0]=b['roster']['home']['hitters'][0]
 with pytest.raises(ValueError,match='opposing duplicate'):m.validate_pairs([a],[b])
def test_no_same_day_future_and_partial_bb_never_zero():
 lineage={k:empty_entry() for k in m.PLAYER};dates={'old':date(2024,3,31),'target':date(2024,4,1),'future':date(2024,4,2)}
 entry=empty_entry();entry['value']=[{'player_id':'p','history':[{'fixture_id':'old','date':'2024-03-31','ab':1,'hit':0,'bb':0,'hr':0}]}];lineage['home_lineup_strength']=entry;m.validate_lineage('target','2024-04-01',lineage,dates)
 for bad in ('target','future'):
  z=copy.deepcopy(lineage);z['home_lineup_strength']['value'][0]['history'][0]['fixture_id']=bad
  with pytest.raises(ValueError,match='temporal'):m.validate_lineage('target','2024-04-01',z,dates)
 z=copy.deepcopy(lineage);z['home_lineup_strength']['value'][0]['history'][0]['bb']=None
 with pytest.raises(ValueError,match='incomplete batting'):m.validate_lineage('target','2024-04-01',z,dates)
 z=copy.deepcopy(lineage);z['home_lineup_strength']['target_score']=5
 with pytest.raises(ValueError,match='history schema'):m.validate_lineage('target','2024-04-01',z,dates)
def test_verified_official_starter_and_receipt_flags():
 a=row();e={'fixture_id':'g','event_date':a['event_date'],'home_id':'A','away_id':'B','official_game_id':'gameid','source_sha256':'a'*64,'retrospective_only':True,'historical_receipt_verified':False,'sides':{s:{'team_id':a[s+'_id'],'hitters':a['roster'][s]['hitters'],'starter':a['roster'][s]['starter'],'batting_basis':'official_confirmed_starting_lineup','starter_basis':'official_explicit_start_flag'} for s in ('home','away')}};source.validate_official_identity(a,e,{'a'*64})
 for field,value in [('starter_basis','schedule_probable'),('starter','winner_id')]:
  z=copy.deepcopy(e);z['sides']['home'][field]=value
  with pytest.raises(ValueError):source.validate_official_identity(a,z,{'a'*64})
 e['historical_receipt_verified']=True
 with pytest.raises(ValueError,match='promotion'):source.validate_official_identity(a,e,{'a'*64})
def test_fit_partition_forbids_unmatched_future_labels():
 pairs=m.validate_pairs([row()],[row()]);labels={'future':{'assumed_completed_date':'2026-05-01'}}
 with pytest.raises(ValueError,match='labels outside'):m.fit_four(pairs,labels,(2024,),'2025-03-23')
def test_corrected_M2_separates_paired_missing_from_unknown():
 a=row();spec=m.original.make_spec([a]);b=copy.deepcopy(a);c=copy.deepcopy(a)
 for side in ('home','away'):
  b['roster'][side]={'hitters':None,'starter':None};c['roster'][side]={'hitters':['new'+side+str(i) for i in range(9)],'starter':'new'+side}
 original,_=m.original.design([b,c],spec,'M2');new,_=m.corrected.design([b,c],spec);assert np.array_equal(original.toarray()[0],original.toarray()[1]) and not np.array_equal(new.toarray()[0],new.toarray()[1])

def test_four_paired_fits_execute_on_synthetic_development():
 import runner
 start=date(2024,4,1);proj=[];actual=[];labels={}
 for i in range(126):
  day=(start+timedelta(days=i)).isoformat() if i<120 else (date(2025,4,1)+timedelta(days=i-120)).isoformat();x=row('g'+str(i),day);x['features']['home_team_strength']['value']=float(i%7);y=copy.deepcopy(x);y['roster']['home']['hitters'][0]='different';proj.append(x);actual.append(y);z=[(2,1),(1,1),(0,1)][i%3];labels[x['fixture_id']]={'status':'final','home_score':z[0],'away_score':z[1]}
 out=runner.develop({'projected':proj,'actual':actual},labels);assert out['training_n']==120 and out['validation_n']==6
 for selector in m.SELECTORS:
  assert set(out['predictions'][selector])==set(m.MODEL_NAMES[:4]);assert all(len(x)==6 for x in out['predictions'][selector].values())
 assert out['family_or_hyperparameter_selection'] is False and out['2026_labels_read'] is False

def test_blocked_gate_and_seven_day_boundary():
 a=row();labels={a['fixture_id']:{'status':'final','home_score':1,'away_score':0}}
 with pytest.raises(ValueError,match='blocked_data'):m.fit_gate([a],labels)
 rows=[row('before','2024-12-24'),row('at','2024-12-25'),row('after','2024-12-26')];selected=m.original.cut_training(rows,(2024,),'2025-01-01');assert {x['fixture_id'] for x in selected}=={'before','at'}

def test_six_models_identical_prices_refund_and_reject_sensitivity():
 import settlement
 predictions={name:[] for name in m.MODEL_NAMES};labels={}
 ledger=[];audit={}
 for i,(day,winner) in enumerate([('2026-04-01','home'),('2026-04-10','draw')]):
  fid='t'+str(i);labels[fid]={'status':'final','home_score':1,'away_score':0 if winner=='home' else 1}
  for name in m.MODEL_NAMES:
   predictions[name].append({'fixture_id':fid,'event_date':day,'probabilities_3way':{'home':.6,'draw':.1,'away':.3},'probabilities_2way':{'home':2/3,'away':1/3}})
  ledger.append({'fixture_id':fid,'date':day,'winner':winner,'price_source':{'origin':'original_archived','prices':{'home':'2','away':'3'}}});audit[fid]={'official_result':{'buyReject':'1' if i==0 else '0'}}
 result,ledgers=settlement.settle(predictions,labels,{'strict':ledger},audit);assert result['price_cohorts']['strict']['n']==2 and result['price_cohorts']['strict']['refunds']==1
 for name in m.MODEL_NAMES:
  metric=result['price_cohorts']['strict']['models'][name];assert metric['hits']==1 and metric['net_units']=='1' and metric['roi_including_refunds']==.5
 assert result['price_cohorts']['strict_current_reject_excluded']['n']==1 and result['price_cohorts']['strict_current_reject_excluded']['models']['M0_actual']['roi_including_refunds']==0

def test_non_full_commit_or_unverified_sha_is_rejected():
 with pytest.raises(ValueError,match='full immutable'):source.committed_blob(P,'abc','file','a'*64)

def test_outcomes_are_not_loaded_when_prediction_is_not_frozen(monkeypatch):
 import workflow
 called=[]
 def fail(*args):raise ValueError('prediction freeze mismatch')
 monkeypatch.setattr(workflow,'frozen_predictions',fail)
 with pytest.raises(ValueError,match='freeze'):workflow.settle_after_freeze('none','a'*40,'b'*64,P,lambda:called.append('called'))
 assert called==[]

def test_full_source_validation_detects_wrong_scalar_and_unverified_hash():
 import workflow
 a=row();b=copy.deepcopy(a)
 for k in m.PLAYER:a['features'][k]['value']=None;b['features'][k]['value']=None
 entry={k:empty_entry() for k in m.PLAYER};lineage={s:{'g':copy.deepcopy(entry)} for s in m.SELECTORS};e={'fixture_id':'g','event_date':a['event_date'],'home_id':'A','away_id':'B','official_game_id':'official','source_sha256':'a'*64,'retrospective_only':True,'historical_receipt_verified':False,'sides':{s:{'team_id':a[s+'_id'],'hitters':a['roster'][s]['hitters'],'starter':a['roster'][s]['starter'],'batting_basis':'official_confirmed_starting_lineup','starter_basis':'official_explicit_start_flag'} for s in ('home','away')}};pairs={'projected':[a],'actual':[b]};dates={'g':date(2024,4,1),**{'old'+str(i):date(2024,3,20+i) for i in range(5)}};proof={side+'_lineup':{'basis':'projected_from_prior_games','player_ids':a['roster'][side]['hitters'],'source_fixture_ids':['old0']} for side in ('home','away')};proof.update({side+'_starter':{'basis':'projected_from_prior_games','player_id':a['roster'][side]['starter'],'rotation_candidates':[{'player_id':a['roster'][side]['starter'] if i==0 else side+str(i),'date':str(dates['old'+str(i)]),'fixture_id':'old'+str(i)} for i in range(5)]} for side in ('home','away')})
 assert len(workflow.validate_snapshot(pairs,lineage,{'g':e},{'g':proof},dates,{'a'*64},{2024})['actual'])==1
 with pytest.raises(ValueError,match='source evidence'):workflow.validate_snapshot(pairs,lineage,{'g':e},{'g':proof},dates,{'b'*64},{2024})
 b['features']['home_lineup_strength']['value']=0.
 with pytest.raises(ValueError,match='scalar value'):workflow.validate_snapshot(pairs,lineage,{'g':e},{'g':proof},dates,{'a'*64},{2024})

def test_independent_dense_replay_for_both_frozen_families():
 import independent_verify as check
 sample=row('x');sample['features']['home_lineup_strength']['value']=None;models=m.frozen_context_models();preds={k:m.predict(v,[sample]) for k,v in models.items()};report=check.verify_predictions({'models':models,'predictions':{'actual':preds}},{'actual':[sample]});assert report['predictions']==2 and report['max_probability_error']<1e-9

def test_synthetic_full_six_cell_path_matches_independent_predictions():
 import runner,independent_verify
 start=date(2024,4,1);projected=[];actual=[];labels={}
 for i in range(120):
  x=row('d'+str(i),(start+timedelta(days=i)).isoformat());x['features']['home_team_strength']['value']=float(i%5);y=copy.deepcopy(x);y['roster']['home']['hitters'][0]='actual_hitter';projected.append(x);actual.append(y);h,a=[(2,1),(1,1),(0,1)][i%3];labels[x['fixture_id']]={'status':'final','home_score':h,'away_score':a}
 targets={'projected':[],'actual':[]}
 for i in range(4):
  x=row('target'+str(i),(date(2026,4,1)+timedelta(days=i)).isoformat());y=copy.deepcopy(x);y['roster']['home']['hitters'][0]='actual_hitter';targets['projected'].append(x);targets['actual'].append(y)
 frozen=runner.final_predictions({'projected':projected,'actual':actual},labels,targets);assert frozen['training_n']==120 and frozen['target_n']==4 and frozen['2026_labels_read'] is False
 assert all(set(x)==set(m.MODEL_NAMES) for x in frozen['predictions'].values());check=independent_verify.verify_predictions(frozen,targets);assert check['predictions']==48 and check['max_probability_error']<1e-9

def test_normalize_overlay_filters_target_metadata_and_rejects_team_future():
 a=row();dates={'g':date(2024,4,1)};ov={'fixture_id':'g','availability_tier':'retrospective_unproven','received_at':None,'completed_at':None,'features':{k:empty_entry() for k in m.FEATURES},'projection':{}}
 for side in ('home','away'):
  ov['projection'][side+'_lineup']={'basis':'projected_from_prior_games','player_ids':None,'source_fixture_ids':[]};ov['projection'][side+'_starter']={'basis':'projected_from_prior_games','player_id':None,'rotation_candidates':[]}
 base={**a,'target_score':123};norm,lineage=source.normalize_overlay(base,ov,'projected',None,dates,set());assert 'target_score' not in norm and norm['features']['home_lineup_strength']['value'] is None and len(lineage)==6
 ov['features']['home_team_strength']['source_fixture_ids']=['g']
 with pytest.raises(ValueError,match='temporal'):source.normalize_overlay(base,ov,'projected',None,dates,set())
