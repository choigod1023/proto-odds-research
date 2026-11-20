import copy
import gzip
import hashlib
import json
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import mlb_lineup_nonlinear as m
from mlb_lineup_nonlinear_collect import extract_snapshot, stamp, read_rows


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.cut = stamp('2026-08-01T19:00:00Z')
        self.start = stamp('2026-08-01T20:00:00Z')
        self.raw = dict(gamePk=1, metaData=dict(timeStamp='20260801_185900'),
                        gameData=dict(datetime=dict(dateTime='2026-08-01T20:00:00Z'),
                                      status=dict(codedGameState='P', detailedState='Warmup'),
                                      probablePitchers=dict(home=dict(id=100), away=dict(id=200))),
                        liveData=dict(plays=dict(allPlays=[]), boxscore=dict(teams={
                            s:dict(battingOrder=list(range(1,10)),players={}) for s in ['home','away']})))

    def test_valid_complete_announcement(self):
        r = extract_snapshot(self.raw,self.cut,self.start,1)
        self.assertEqual([p['id'] for p in r['sides']['home']['lineup']],list(range(1,10)))
        self.assertEqual(r['sides']['away']['starter']['id'],200)

    def test_late_timestamp_and_mismatched_fixture(self):
        self.raw['metaData']['timeStamp']='20260801_190001'
        with self.assertRaises(ValueError): extract_snapshot(self.raw,self.cut,self.start,1)
        self.raw['metaData']['timeStamp']='20260801_185900'
        with self.assertRaises(ValueError): extract_snapshot(self.raw,self.cut,self.start,2)

    def test_real_pitch_in_either_play_container_rejected(self):
        for key,value in [('allPlays',[dict(playEvents=[dict(isPitch=True)])]),
                          ('currentPlay',dict(playEvents=[dict(isPitch=True)]))]:
            r=copy.deepcopy(self.raw);r['liveData']['plays'][key]=value
            with self.assertRaisesRegex(ValueError,'pitch_already'): extract_snapshot(r,self.cut,self.start,1)

    def test_live_final_missing_starter_duplicate_or_short_lineup_rejected(self):
        for state in ['I','F']:
            r=copy.deepcopy(self.raw);r['gameData']['status']['codedGameState']=state
            with self.assertRaises(ValueError): extract_snapshot(r,self.cut,self.start,1)
        for order in [list(range(1,9)),[1]*9]:
            r=copy.deepcopy(self.raw);r['liveData']['boxscore']['teams']['away']['battingOrder']=order
            with self.assertRaises(ValueError): extract_snapshot(r,self.cut,self.start,1)
        self.raw['gameData']['probablePitchers']['home']={}
        with self.assertRaises(ValueError): extract_snapshot(self.raw,self.cut,self.start,1)

    def test_feed_start_is_independently_checked(self):
        self.raw['gameData']['datetime']['dateTime']='2026-08-01T18:30:00Z'
        with self.assertRaisesRegex(ValueError,'feed_start'): extract_snapshot(self.raw,self.cut,self.start,1)


class ModelUnitTests(unittest.TestCase):
    def test_boolean_labels_score_as_numeric_labels(self):
        self.assertEqual(m.scores([True,False],[.8,.2]),m.scores([1,0],[.8,.2]))

    def test_decimal_single_bet_price_order_and_push(self):
        self.assertEqual(m.settle(.5,2,1,[2.07,1.5]),dict(pick='home',correct=1,push=0,net_krw=10700))
        self.assertEqual(m.settle(.49,1,2,[2.07,1.5])['net_krw'],5000)
        self.assertEqual(m.settle(.8,1,2,[2.07,1.5])['net_krw'],-10000)
        self.assertEqual(m.settle(.8,2,2,[2.07,1.5])['net_krw'],0)

    def test_nonintercept_penalty_and_train_only_scaling(self):
        rng=np.random.default_rng(12);x=rng.normal(size=(80,len(m.LINEAR_NAMES)+len(m.MATCH_NAMES)))
        y=(x[:,0]>.1).astype(float)
        model=m.fit(x,y,'linear',.1)
        np.testing.assert_allclose(model['center'],x[:,:len(m.LINEAR_NAMES)].mean(axis=0),atol=1e-12,rtol=0)
        self.assertLess(model['max_gradient'],1e-7)
        before=json.dumps(model,sort_keys=True)
        m.predict(model,np.repeat(1000.,x.shape[1])[None,:])
        self.assertEqual(before,json.dumps(model,sort_keys=True))
        self.assertGreater(np.ptp(m.predict(model,x)),.2)

    def test_hinge_is_nonlinear_and_coefficient_replay(self):
        rng=np.random.default_rng(8);x=rng.normal(size=(100,len(m.LINEAR_NAMES)+len(m.MATCH_NAMES)))
        y=(np.abs(x[:,0])>.8).astype(float);model=m.fit(x,y,'hinge',.01)
        np.testing.assert_allclose(m.predict(model,x),m.sigmoid(m.design(model,x)@model['beta']),atol=1e-12,rtol=0)
        center=np.array(model['center']);h=center.copy();l=center.copy()
        h[0]+=model['scale'][0];l[0]-=model['scale'][0]
        z=m.design(model,np.array([h,center,l]))
        self.assertGreater(np.max(np.abs(z[0]+z[2]-2*z[1])),.9)

    def test_selection_rules_do_not_use_evaluation_or_roi(self):
        val={'a':dict(correct=7,logloss=.6,brier=.2,roi=-100),
             'b':dict(correct=8,logloss=.7,brier=.25,roi=100),
             'c':dict(correct=8,logloss=.65,brier=.23,roi=-1000)}
        self.assertEqual(m.select_heads(val),dict(primary='a',hit='c'))

    def test_evaluation_overlap_is_removed_from_validation(self):
        rows=[dict(gamePk=1,accepted=True,role='train',start_at='2024-01-01'),
              dict(gamePk=2,accepted=True,role='validation',start_at='2025-01-01'),
              dict(gamePk=3,accepted=True,role='validation',start_at='2025-02-01'),
              dict(gamePk=3,accepted=True,role='evaluation',start_at='2025-12-01')]
        tr,val,ev,over=m.split_rows(rows)
        self.assertEqual(over,[3]);self.assertEqual([r['gamePk'] for r in val],[2])


class PreservedDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables=m.load_tables(m.OUT)
        cls.rows=read_rows(m.OUT/'sources/mlb-snapshots.jsonl.gz')
        cls.accepted=[r for r in cls.rows if r['accepted']]
        cls.row=next(r for r in cls.accepted if r['role']=='train')

    def test_source_manifest(self):
        self.assertGreater(len(m.verify_sources(m.OUT)),10)

    def test_previous_year_only_and_outcome_mutation(self):
        original,proof=m.feature_row(self.row,self.tables)
        self.assertEqual(proof['prior_season'],2023)
        changed=copy.deepcopy(self.row)
        changed.update(home_score=999,away_score=-999,target={'winner':'irrelevant'})
        np.testing.assert_array_equal(original,m.feature_row(changed,self.tables)[0])
        tables=dict(self.tables);tables[2024,'batter']={};tables[2025,'pitcher']={}
        np.testing.assert_array_equal(original,m.feature_row(self.row,tables)[0])

    def test_ordered_nine_is_used_and_not_roster_mean(self):
        original,proof=m.feature_row(self.row,self.tables)
        changed=copy.deepcopy(self.row);changed['sides']['home']['lineup'].reverse()
        altered,_=m.feature_row(changed,self.tables)
        self.assertGreater(np.max(np.abs(original-altered)),1e-6)
        bats=np.array([b['rates'] for b in proof['sides']['home']['batters']])
        np.testing.assert_allclose(m.WEIGHTS@bats,proof['sides']['home']['weighted_batting'],atol=1e-12,rtol=0)

    def test_missingness_prior_shrink_and_true_bf(self):
        table=self.tables[2023,'pitcher']
        values,proof=m.player_features(dict(id=-1,pitching=dict(battersFaced=10,strikeOuts=8,
                                        baseOnBalls=2,numberOfPitches=1000)),'pitcher',table)
        expected_k=(8+100*table['means']['k'])/110
        self.assertAlmostEqual(values[2],expected_k,places=14)
        self.assertEqual(values[7:].tolist(),[1.,0.,1.])
        self.assertEqual(proof['prior_season'],2023)
        no,_=m.player_features(dict(id=-1),'batter',self.tables[2023,'batter'])
        self.assertEqual(no[-3:].tolist(),[1.,1.,1.])
        self.assertEqual(no[0],self.tables[2023,'batter']['means']['xwoba'])

    def test_prior_era_shrinks_by_bf_and_current_era_by_actual_ip(self):
        table=copy.deepcopy(self.tables[2023,'pitcher'])
        table['stats'][-100]=dict(battersFaced=100,outs=60,earnedRuns=10)
        values,_=m.player_features(dict(id=-100,pitching=dict(battersFaced=10,outs=6,earnedRuns=1)),
                                  'pitcher',table)
        previous=(4.5*100+300*table['means']['era'])/400
        self.assertAlmostEqual(values[5],(4.5*2+30*previous)/32,places=14)

    def test_matchup_features_have_cross_derivative(self):
        # Change a home batter prior skill and away starter prior skill independently.
        row=copy.deepcopy(self.row)
        pid=row['sides']['home']['lineup'][0]['id'];pit=row['sides']['away']['starter']['id']
        table=self.tables[2023,'batter'];ptable=self.tables[2023,'pitcher']
        if pid not in table['savant'] or pit not in ptable['savant']:self.skipTest('sample has no Savant')
        original=m.feature_row(row,self.tables)[0]
        tb=copy.deepcopy(self.tables);tb[2023,'batter']['savant'][pid]['est_woba']=.6
        tp=copy.deepcopy(self.tables);tp[2023,'pitcher']['savant'][pit]['est_woba']=.6
        both=copy.deepcopy(tb);both[2023,'pitcher']=tp[2023,'pitcher']
        cross=m.feature_row(row,both)[0]-m.feature_row(row,tb)[0]-m.feature_row(row,tp)[0]+original
        self.assertGreater(np.max(np.abs(cross[len(m.LINEAR_NAMES):])),.01)

    def test_external_raw_response_attests_all_accepted_rows(self):
        snaps=read_rows(m.OUT/'external-snapshots.jsonl.gz')
        targets={r['gamePk']:r for r in read_rows(m.OUT/'sources/mlb-external-features.jsonl')}
        self.assertEqual(len(snaps),230)
        for r in snaps:
            if 'response_sha256' not in r:continue
            data=gzip.open(m.OUT/r['raw_path'],'rb').read()
            self.assertEqual(hashlib.sha256(data).hexdigest(),r['response_sha256'])
            target=targets[r['gamePk']]
            if r['accepted']:
                parsed=extract_snapshot(json.loads(data),stamp(target['decision_at']),stamp(target['scheduled_start']),r['gamePk'])
                self.assertEqual(parsed['sides'],r['sides'])
                self.assertLessEqual(stamp(parsed['snapshot_at']),stamp(target['decision_at']))
            else:
                with self.assertRaises(Exception):extract_snapshot(json.loads(data),stamp(target['decision_at']),stamp(target['scheduled_start']),r['gamePk'])

    def test_frozen_selection_and_probability_application(self):
        f=m.OUT/'frozen-selection.json'
        if not f.exists():self.skipTest('run first')
        frozen=json.loads(f.read_text(encoding='utf8'))
        self.assertEqual(frozen['heads'],m.select_heads(frozen['validation']))
        self.assertEqual(len(frozen['models']),12)
        inputs={r['gamePk']:r for r in read_rows(m.OUT/'inputs.jsonl.gz')}
        ev=read_rows(m.OUT/'predictions.jsonl')
        forbidden={r['gamePk'] for r in ev}
        self.assertFalse(forbidden&set(frozen['train_ids']))
        self.assertFalse(forbidden&set(frozen['validation_ids']))
        for r in ev:
            if not r['announced']:
                for head in ['primary','hit']:
                    self.assertEqual(r['probabilities'][head],r['probabilities']['baseline'])
                    self.assertEqual(r['ledger'][head],r['ledger']['baseline'])
                continue
            raw=np.array([inputs[r['gamePk']]['features']])
            for n,model in frozen['models'].items():
                self.assertAlmostEqual(m.predict(model[r['model_stage']],raw)[0],r['candidate_probabilities'][n],delta=1e-12)
            for head,name in frozen['heads'].items():
                self.assertEqual(r['probabilities'][head],r['candidate_probabilities'][name])

    def test_home_constant_control_matches_direction_and_money(self):
        rows=read_rows(m.OUT/'predictions.jsonl')
        announced={c:[r for r in rows if r['cohort']==c and r['announced']] for c in ['old116','external230']}
        self.assertEqual({c:len(rs) for c,rs in announced.items()},{'old116':116,'external230':194})
        for r in rows:
            p=1. if r['announced'] else r['probabilities']['baseline']
            self.assertEqual(m.settle(p,r['hg'],r['ag'],r['prices']),r['ledger']['primary'])
            if not r['announced']:
                np.testing.assert_allclose(r['probabilities']['primary_blend10'],p,atol=1e-12,rtol=0)
                self.assertEqual(r['ledger']['primary_blend10'],r['ledger']['baseline'])


if __name__=='__main__':unittest.main()
