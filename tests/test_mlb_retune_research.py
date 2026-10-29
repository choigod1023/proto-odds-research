"""Source/cutoff invariants and independent settlement replay for MLB research."""
import copy
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import mlb_retune_research as m


class ModelGuards(unittest.TestCase):
    def test_score_distributions_symmetric_and_monotonic(self):
        for family in ('poisson', 'negbin', 'pyth'):
            self.assertAlmostEqual(m.run_probability(np.array([[4.,4.]]),family,.25)[0], .5, places=12)
            p=m.run_probability(np.array([[3.,5.],[4.,4.],[5.,3.]]),family,.25)
            self.assertTrue(p[0]<p[1]<p[2]); self.assertAlmostEqual(p[0]+p[2],1.,places=12)

    def test_two_way_shin_equals_actual_source_probabilities(self):
        for r in m.read(m.DEFAULT/'sources/external230.jsonl'):
            self.assertAlmostEqual(m.market(r['historical_prices']),r['market_probability'],places=10)
        for prices in ([0,2],[1,2],[float('nan'),2],[2]):
            with self.assertRaises(ValueError): m.market(prices)

    def test_completion_rejects_resumed_missing_and_unfinished(self):
        g=dict(status={'codedGameState':'F'},gameInfo=dict(firstPitch='2024-01-01T00:00:00Z',gameDurationMinutes=180,delayDurationMinutes=20))
        self.assertEqual(m.completion(g),m.stamp('2024-01-01T04:20:00Z'))
        for key,value in [('resumeDate','2024-01-02'),('resumedFrom','2024-01-01')]:
            self.assertIsNone(m.completion(dict(g,**{key:value})))
        self.assertIsNone(m.completion(dict(g,status={'codedGameState':'I'})))
        self.assertIsNone(m.completion(dict(g,gameInfo={})))

    def test_primary_noninferiority_and_ties(self):
        def metric(acc,roi,ll,br): return dict(accuracy=acc,roi=roi,logloss=ll,brier=br)
        scores={'market_null':metric(.6,-.1,.65,.24),'bad_roi':metric(.8,-.2,.1,.1),
                'bad_accuracy':metric(.5,.1,.2,.1),'valid':metric(.6,-.05,.6,.23)}
        self.assertEqual(m.choose(scores),dict(primary='valid',hit_leader='bad_roi',profit_leader='bad_accuracy'))
        tied={k:metric(.6,-.1,.65,.24) for k in ('z','a','market_null')}
        self.assertEqual(m.choose(tied)['primary'],'a')

    def test_draw_refund_and_price_order(self):
        rows=[dict(date='2026-01-01',hg=1,ag=1,prices=[4.,1.3],order=['away','home']),
              dict(date='2026-01-02',hg=2,ag=1,prices=[4.,1.3],order=['away','home'])]
        score=m.measure(rows,np.array([.1,.9]))
        self.assertEqual((score['pushes'],score['wins'],score['net_krw']),(1,1,3000))
        self.assertAlmostEqual(score['roi'],.15)


class ArtifactReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result=json.loads((m.DEFAULT/'results.json').read_text(encoding='utf8'))
        cls.history,cls.score_rows,cls.cohorts,cls.audit=m.load(m.DEFAULT)
        cls.val=m.read(m.DEFAULT/'validation_predictions.jsonl')
        cls.pred=m.read(m.DEFAULT/'predictions.jsonl')

    def independent_metric(self,rows,ps):
        wins=pushes=0; profit=[]; ll=[]; br=[]
        for r,p in zip(rows,ps):
            if r['hg']==r['ag']:
                pushes+=1; profit.append(0.); continue
            y=int(r['hg']>r['ag']); good=(p>=.5)==bool(y)
            wins+=good
            side='home' if p>=.5 else 'away'
            profit.append(r['prices'][r['order'].index(side)]-1 if good else -1)
            p=max(1e-10,min(1-1e-10,p)); ll.append(-math.log(p if y else 1-p)); br.append((p-y)**2)
        return dict(n=len(rows),wins=wins,pushes=pushes,accuracy=wins/(len(rows)-pushes),
            roi=sum(profit)/len(rows),net_krw=round(sum(profit)*10000),logloss=sum(ll)/len(ll),brier=sum(br)/len(br))

    def assert_metric(self,expected,rows,ps):
        actual=self.independent_metric(rows,ps)
        for k,value in actual.items(): self.assertAlmostEqual(expected[k],value,places=10,msg=k)

    def test_all_candidate_metrics_independent_settlement(self):
        for name,expected in self.result['validation'].items():
            self.assert_metric(expected,self.val,[r['candidate_probabilities'][name] for r in self.val])
        for cohort,table in self.result['periods'].items():
            rows=[r for r in self.pred if r['eval_set']==cohort]
            for period,out in table['periods'].items():
                rs=[r for r in rows if period=='all' or (period=='before_split')==(r['date']<table['split_date'])]
                for name,expected in out['all_candidates'].items():
                    self.assert_metric(expected,rs,[r['candidate_probabilities'][name] for r in rs])

    def test_all_predictions_match_frozen_coefficients(self):
        facts={(r['gamePk'],r['cutoff'],r['half_life']):r for r in m.read(m.DEFAULT/'feature-facts.jsonl.gz')}
        for rows,stage in ((self.val,'selection'),(self.pred,'final')):
            for half in (45,180):
                config=self.result['configs'][str(half)]; scoreconfig=config[stage+'_score']
                xx=[facts[r['gamePk'],r['cutoff'],half] for r in rows]
                lam=np.exp(np.log([r['mu'] for r in xx])[:,None]+np.array([r['x'] for r in xx])@scoreconfig['beta'])
                base=np.array([r['baseline_probability'] for r in rows])
                for family in ('poisson','negbin','pyth'):
                    score=m.run_probability(lam,family,scoreconfig['alpha'])
                    for link in ('pure','market75_score25','market50_score50','residual'):
                        name=f'{family}:h{half}:{link}'
                        p=m.apply_link(score,base,link,config[family][stage+'_residual'])
                        self.assertTrue(np.allclose(p,[r['candidate_probabilities'][name] for r in rows],rtol=0,atol=1e-12))

    def test_frozen_selection_coverage_and_roles(self):
        frozen=json.loads((m.DEFAULT/'frozen-selection.json').read_text(encoding='utf8'))
        self.assertEqual(m.choose(self.result['validation']),frozen['selected'])
        self.assertEqual(frozen['selected'],self.result['selected'])
        self.assertEqual(self.result['concrete_candidate_count'],24)
        self.assertEqual(len(self.result['validation']),25)
        self.assertEqual(self.audit['retained'],dict(train2023=2228,validation2024=2251,external230=230,secondary116=116))
        self.assertEqual(len(self.audit['exclusions']),0)
        for r in self.val+self.pred:
            self.assertEqual(len(r['candidate_probabilities']),25)
            for p in r['candidate_probabilities'].values(): self.assertTrue(0<p<1)
        for r in self.pred:
            for role,name in frozen['selected'].items():
                self.assertEqual(r['policies'][role],dict(candidate=name,probability=r['candidate_probabilities'][name]))

    def test_past_only_source_guards_all_feature_rows(self):
        history={r['gamePk']:r for r in self.history}
        for r in m.read(m.DEFAULT/'feature-facts.jsonl.gz'):
            cutoff=m.stamp(r['cutoff']); proof=r['proof']
            if proof['latest_completion']: self.assertLess(m.stamp(proof['latest_completion']),cutoff)
            target_day=next(g['day'] for g in self.history if g['gamePk']==r['gamePk'])
            for team,side in proof['sides'].items():
                self.assertNotIn(r['gamePk'],side['source_game_ids'])
                for pk in side['source_game_ids']:
                    g=history[pk]
                    self.assertLess(g['end'],cutoff); self.assertLess(g['day'],target_day)
                    self.assertIn(team,(g['home'],g['away']))
                    self.assertIn(g['season'],[int(target_day[:4])-1,int(target_day[:4])])

    def test_future_and_target_results_cannot_change_features(self):
        target=self.cohorts['external230'][100]
        builder=m.FeatureBuilder(self.history)
        original=builder.features(target,45)
        changed=copy.deepcopy(self.history)
        cutoff=m.stamp(target['cutoff']); mutations=0
        for g in changed:
            if g['end']>=cutoff or g['gamePk']==target['gamePk']:
                g['hg']=999; g['ag']=777; mutations+=1
        self.assertGreater(mutations,0)
        poisoned=dict(target,hg=999,ag=777)
        after=m.FeatureBuilder(changed).features(poisoned,45)
        self.assertTrue(np.array_equal(original[0],after[0])); self.assertEqual(original[1:],after[1:])

    def test_raw_feature_reconstruction_sample(self):
        index={(r['gamePk'],r['cutoff']):r for r in self.score_rows+sum(self.cohorts.values(),[])}
        facts=m.read(m.DEFAULT/'feature-facts.jsonl.gz')
        for i in np.random.default_rng(4).choice(len(facts),size=32,replace=False):
            f=facts[i]; target=index[f['gamePk'],f['cutoff']]
            x,mu,proof=m.FeatureBuilder(self.history).features(target,f['half_life'])
            np.testing.assert_allclose(x,f['x'],rtol=0,atol=1e-12); self.assertAlmostEqual(mu,f['mu'],places=12)
            self.assertEqual(json.loads(json.dumps(proof)),f['proof'])

    def test_new_variables_independently_from_raw_games(self):
        facts=m.read(m.DEFAULT/'feature-facts.jsonl.gz')
        hist={r['gamePk']:r for r in self.history}
        for f in facts[::max(1,len(facts)//48)]:
            cutoff=m.stamp(f['cutoff']); target=hist[f['gamePk']]; year=target['season']
            past=[g for g in self.history if g['gamePk']!=f['gamePk'] and g['end']<cutoff and
                  g['day']<target['day'] and g['season'] in (year-1,year)]
            env={}
            for season in (year-1,year):
                season_rows=[g for g in past if g['season']==season]
                env[season]=(sum(g['hg']+g['ag'] for g in season_rows)+270)/(2*len(season_rows)+60)
                self.assertAlmostEqual(f['proof']['environment'][str(season)],env[season],places=12)
            for team,side in f['proof']['sides'].items():
                gs=[g for g in past if team in (g['home'],g['away'])]
                self.assertEqual(set(side['source_game_ids']),{g['gamePk'] for g in gs})
                recent=[g for g in gs if 0<(m.date.fromisoformat(target['day'])-m.date.fromisoformat(g['day'])).days<=3]
                self.assertEqual(side['games3'],len(recent))
                self.assertAlmostEqual(side['long_minutes3'],sum(max(0,g['duration']-180)/180 for g in recent),places=12)
                weights=[math.exp(-math.log(2)*(cutoff-g['start']).total_seconds()/86400/f['half_life'])*(.5 if g['season']<year else 1) for g in gs]
                attack=(20+sum(w*(g['hg'] if g['home']==team else g['ag'])/env[g['season']] for w,g in zip(weights,gs)))/(20+sum(weights))
                defense=(20+sum(w*(g['ag'] if g['home']==team else g['hg'])/env[g['season']] for w,g in zip(weights,gs)))/(20+sum(weights))
                self.assertAlmostEqual(side['attack'],attack,places=12); self.assertAlmostEqual(side['defense'],defense,places=12)
                recent14=[g for g in gs if (cutoff-g['start']).total_seconds()<=14*86400]
                rate=(sum((g['ag'] if g['home']==team else g['hg'])/env[g['season']] for g in recent14)+10)/(len(recent14)+10)
                self.assertAlmostEqual(side['defense14'],rate,places=12)
            home=f['proof']['sides'][target['home']]; away=f['proof']['sides'][target['away']]
            self.assertAlmostEqual(f['x'][0][8],away['games3']/3*math.log(away['defense14']),places=12)
            self.assertAlmostEqual(f['x'][1][8],home['games3']/3*math.log(home['defense14']),places=12)


if __name__=='__main__': unittest.main()
