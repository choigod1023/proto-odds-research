import copy,hashlib,importlib.util,json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parent));import features

NOW='2026-11-03T08:00:00+00:00'
def data():
 event={'official_game_id':'20261104AABB0','season':2026,'kickoff_at':'2026-11-04T18:30:00+09:00','home_id':'AA','away_id':'BB'}
 roster={s:{'starter':s+'p','hitters':[s+str(i) for i in range(9)]} for s in ('home','away')}
 seed={'through_date':'2026-10-31','received_at':NOW,'games':[{'fixture_id':'old','date':'2026-10-30','teams':{'AA':{'runs_for':6,'runs_against':3},'BB':{'runs_for':3,'runs_against':6}},'batters':{'home0':{'ab':4,'hit':2,'bb':1}},'pitchers':{'homep':{'er':2,'outs':9}}}]}
 game={'fixture_id':'new','date':'2026-11-02','received_at':NOW,'box_complete':True,'teams':{'AA':{'runs_for':4,'runs_against':2,'relief':{'outs':6}},'BB':{'runs_for':2,'runs_against':4,'relief':{'outs':9}}},'batters':{'home0':{'ab':2,'hit':1,'bb':0}},'pitchers':{'homep':{'er':1,'outs':6}}}
 schedule={'from_date':'2026-11-01','through_date':'2026-11-03','received_at':NOW,'uri':'https://www.koreabaseball.com/schedule','body':'official schedule bytes','sha256':hashlib.sha256(b'official schedule bytes').hexdigest(),'events':[{'fixture_id':'new','date':'2026-11-02','status':'final'}]}
 history={'seed':seed,'games':[game],'schedules':{'AA':copy.deepcopy(schedule),'BB':copy.deepcopy(schedule)}}
 return event,roster,history
class Tests(unittest.TestCase):
 def test_features_and_lineage(self):
  e,r,h=data();before=copy.deepcopy(h);x=features.build_row(e,r,h,NOW);self.assertEqual(h,before);self.assertEqual(len(x['features']),11)
  self.assertEqual(x['features']['home_team_strength']['value'],2.5);self.assertEqual(x['features']['home_rest_days']['value'],1);self.assertEqual(x['features']['home_bullpen_load']['value'],6.)
  self.assertEqual(x['features']['home_lineup_strength']['value'],4/7);self.assertAlmostEqual(x['features']['home_starter_strength']['value'],81/15)
  self.assertEqual(x['raw_features']['home_team_strength']['source_fixture_ids'],['new','old'])
 def test_unverified_or_incomplete_schedule_nulls_team_values(self):
  e,r,h=data();h['schedules'].pop('AA');x=features.build_row(e,r,h,NOW)
  for k in ('home_team_strength','home_rest_days','home_bullpen_load','home_lineup_strength','home_starter_strength'):
   self.assertIsNone(x['features'][k]['value']);self.assertIsNone(x['raw_features'][k]['value'])
  self.assertEqual(x['history_coverage']['home']['status'],'incomplete');self.assertEqual(x['history_coverage']['home']['reason'],'schedule_missing')
  e,r,h=data();h['games']=[];x=features.build_row(e,r,h,NOW)
  self.assertEqual(x['history_coverage']['home']['missing_dates'],['2026-11-02']);self.assertEqual(x['history_coverage']['home']['missing_fixture_ids'],['new'])
  self.assertIsNone(x['features']['home_lineup_strength']['value']);self.assertIsNone(x['features']['home_starter_strength']['value'])
 def test_seed_after_target_and_nonprior_rejected(self):
  e,r,h=data();h['seed']['through_date']='2026-11-04'
  with self.assertRaises(ValueError):features.build_row(e,r,h,NOW)
  e,r,h=data();h['games'][0]['date']='2026-11-04'
  with self.assertRaises(ValueError):features.build_row(e,r,h,NOW)
  e,r,h=data();h['games'][0]['fixture_id']=e['official_game_id']+'2026'
  with self.assertRaises(ValueError):features.build_row(e,r,h,NOW)
 def test_receipt_and_overlap(self):
  e,r,h=data();h['games'][0]['received_at']='2026-11-03T09:00:00+00:00'
  with self.assertRaises(ValueError):features.build_row(e,r,h,NOW)
  e,r,h=data();h['games'][0]['date']='2026-10-30'
  with self.assertRaises(ValueError):features.build_row(e,r,h,NOW)
 def test_team_snapshot_boundary_and_seed_merge(self):
  e,r,h=data();h['seed']['team_through']={'AA':'2026-10-29','BB':'2026-10-31'}
  h['schedules']['AA']['from_date']='2026-10-30';h['schedules']['AA']['events'].insert(0,{'fixture_id':'old','date':'2026-10-30','status':'final'})
  h['games'].append({'fixture_id':'old','date':'2026-10-30','received_at':NOW,'box_complete':True,'teams':{'AA':{'runs_for':6,'runs_against':3}},'batters':{'home0':{'ab':4,'hit':2,'bb':1}},'pitchers':{'homep':{'er':2,'outs':9}}})
  x=features.build_row(e,r,h,NOW);self.assertEqual(x['features']['home_team_strength']['value'],2.5)
  h['games'][-1]['teams']['AA']['runs_for']=99
  with self.assertRaises(ValueError):features.build_row(e,r,h,NOW)
 def test_unverified_schedule_body(self):
  e,r,h=data();h['schedules']['AA']['body']='modified'
  x=features.build_row(e,r,h,NOW);self.assertIsNone(x['features']['home_team_strength']['value'])
 def test_missing_player_box_invalidates_coverage(self):
  e,r,h=data();h['games'][0]['pitchers']={};x=features.build_row(e,r,h,NOW)
  self.assertEqual(x['history_coverage']['home']['status'],'incomplete')
  self.assertEqual(x['history_coverage']['home']['missing_fixture_ids'],['new'])
  self.assertIsNone(x['features']['home_lineup_strength']['value']);self.assertIsNone(x['features']['home_starter_strength']['value'])
 def test_tampered_frozen_seed_rejected(self):
  original=Path.read_bytes
  def tamper(path):
   data=original(path)
   return data+b'tamper' if path.name=='train-actual.jsonl.gz' else data
  with patch.object(Path,'read_bytes',tamper):
   with self.assertRaisesRegex(ValueError,'seed source hash mismatch'):features.seed_from_frozen(NOW,'2026-11-04')
 def test_last10(self):
  e,r,h=data();h['seed']['games']=[]
  for i in range(15):h['seed']['games'].append({'fixture_id':f'id{i:02}','date':f'2026-10-{i+1:02}','teams':{},'batters':{'home0':{'ab':1,'hit':1,'bb':0}},'pitchers':{'homep':{'er':0,'outs':3}}})
  x=features.build_row(e,r,h,NOW);self.assertEqual(len(x['raw_features']['home_lineup_strength']['value'][0]['history']),10);self.assertEqual(len(x['raw_features']['home_starter_strength']['value']['appearances']),10)
 def test_real_frozen_inference_with_simulated_event(self):
  seed=features.seed_from_frozen(NOW,'2026-11-04');self.assertLess(seed['through_date'],'2026-11-04');self.assertEqual(len(seed['provenance']['sources']),2);self.assertFalse(seed['provenance']['historical_receipt_proven'])
  import gzip
  with gzip.open(features.V/'inputs/test-actual.jsonl.gz','rt',encoding='utf8') as f:real=json.loads(next(f))
  e={'official_game_id':'20261104HTSK0','season':2026,'kickoff_at':'2026-11-04T18:30:00+09:00','home_id':real['home_id'],'away_id':real['away_id']}
  row=features.build_row(e,real['roster'],{'seed':seed,'games':[],'schedules':{}},NOW)
  f=features.ROOT/'experiments/kbo-m0-followup-20261103/forward.py';sp=importlib.util.spec_from_file_location('sim_forward',f);mod=importlib.util.module_from_spec(sp);sp.loader.exec_module(mod)
  pred,_=mod.forecast(row)
  for p in pred.values():self.assertAlmostEqual(sum(p['probabilities_3way'].values()),1.)
  self.assertIsNone(row['features']['home_team_strength']['value'])
if __name__=='__main__':unittest.main()
