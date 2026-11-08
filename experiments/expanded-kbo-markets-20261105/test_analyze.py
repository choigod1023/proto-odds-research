import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('analysis',Path(__file__).with_name('analyze.py'))
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)

class Checks(unittest.TestCase):
    def setUp(self):
        self.w={'protoStatus':'4','gameReject':'0','mchScore':'5:5','gameResult':'4'}
        self.r={'protoStatus':'4','gameReject':'0','gameSubject':None,'drawAllot':0,'winHandi':-2.5,'loseHandi':2.5,'winAllot':2.5,'loseAllot':1.4,'mchScore':'2.5:5','gameResult':'2'}
        self.rates={'home_rate':5,'away_rate':4}
    def test_draw_handicap_is_settled(self):
        self.assertEqual(a.calc('handicap',self.r,self.w,self.rates)['outcome'],'away')
    def test_official_grade_and_score_must_match(self):
        self.r['gameResult']='0'
        with self.assertRaisesRegex(ValueError,'product_grade'): a.calc('handicap',self.r,self.w,self.rates)
        self.r['gameResult']='2';self.r['mchScore']='5:5'
        with self.assertRaisesRegex(ValueError,'product_score'): a.calc('handicap',self.r,self.w,self.rates)
    def test_reject_integer_line(self):
        self.r.update(winHandi=-2,loseHandi=2)
        with self.assertRaisesRegex(ValueError,'line'): a.calc('handicap',self.r,self.w,self.rates)
    def test_total_and_prices(self):
        self.r.update(winHandi=9.5,loseHandi=9.5,mchScore='10:0',gameResult='2')
        self.assertEqual(a.calc('total',self.r,self.w,self.rates)['outcome'],'over')
        self.r['winAllot']=1
        with self.assertRaisesRegex(ValueError,'prices'): a.calc('total',self.r,self.w,self.rates)
    def test_drawdown(self):
        rows=[{'model_selection':'home','outcome':out,'prices':{'home':2}} for out in ('home','away','away','home')]
        self.assertEqual(a.metric(rows,'model_selection')['max_drawdown_units'],2)
class IdentityChecks(unittest.TestCase):
    def test_ambiguous_fixture_is_not_unique(self):
        ids=['20260421HHLG02026','20260421HHLG12026']
        idx=a.fixture_index(ids+['score:2026-04-21:LG:HH'])
        self.assertEqual(idx[('20260421','LG','HH')],ids)
        self.assertEqual(sum(map(len,idx.values())),2)
    def test_strict_requires_both_product_codes_and_full_game(self):
        self.assertEqual(a.product_kind({'handi':9,'betId':'79','betTypId':'7'},True),'total')
        self.assertEqual(a.product_kind({'handi':23,'betId':'7','betTypId':'5'},True),'handicap')
        for row in ({'handi':9,'betId':None,'betTypId':None}, {'handi':9,'betId':'114','betTypId':'7'}, {'handi':9,'betId':'79','betTypId':'9'}):
            self.assertIsNone(a.product_kind(row,True))
        self.assertEqual(a.product_kind({'handi':9,'betId':None,'betTypId':None},False),'total')
    def test_winner_uniqueness_and_provenance(self):
        w={'matchSeq':1,'protoStatus':'4','gameReject':'0','mchScore':'4:3'}
        source={'round':'260101','sha256':'abc','path':'verified'}
        with self.assertRaisesRegex(ValueError,'winner_missing_or_ambiguous'):
            a.choose_winner({'260101':[(w,source),(w,source)]})
        chosen=a.choose_winner({'260101':[(w,source),(w,source)],'260102':[(w,source)]})
        self.assertEqual(chosen,(260102,w,source))
    def test_winner_conflicting_scores_rejected(self):
        w={'matchSeq':1,'protoStatus':'4','gameReject':'0','mchScore':'4:3'}
        with self.assertRaisesRegex(ValueError,'conflicting_fixture_scores'):
            a.choose_winner({'260101':[(w,{})],'260102':[(dict(w,mchScore='5:3'),{})]})
    def test_only_modern_full_game_winner_in_strict(self):
        self.assertTrue(a.winner_kind({'betId':'2','betTypId':'2'},True))
        self.assertFalse(a.winner_kind({'betId':'108','betTypId':'3','handi':0},True))
        self.assertFalse(a.winner_kind({'betId':None,'betTypId':None,'handi':0},True))
        self.assertTrue(a.winner_kind({'betId':None,'betTypId':None,'handi':0},False))
class AliasChecks(unittest.TestCase):
    def setUp(self):
        self.fid='score:2026-07-28:x:y'
        self.info={'official_game_id':'game-1','event_date':'2026-07-28','home_id':'LG','away_id':'WO','kickoff':'2026-07-28T18:30:00+09:00','schedule_body_sha256':'abc','betman_sources':[{'round':'260088','body_sha256':'def'}]}
    def test_index_uses_metadata_not_synthetic_id(self):
        idx=a.resolved_index({self.fid:{}},{},{'resolved':{self.fid:self.info}})
        self.assertEqual(idx[('20260728','LG','WO')],[self.fid])
    def test_reject_duplicate_official_id_or_team_date(self):
        info=dict(self.info,official_game_id='20260328HTSK0')
        with self.assertRaisesRegex(ValueError,'duplicate official'):
            a.resolved_index({self.fid:{}},{('20260328','SK','HT'):['20260328HTSK02026']},{'resolved':{self.fid:info}})
        with self.assertRaisesRegex(ValueError,'ambiguous date'):
            a.resolved_index({self.fid:{}},{('20260728','LG','WO'):['other2026']},{'resolved':{self.fid:self.info}})
    def test_kickoff_and_round_proof_must_match(self):
        ts=a.datetime.datetime.fromisoformat(self.info['kickoff']).timestamp()*1000
        self.assertTrue(a.alias_source_matches(self.info,ts,{'round':'260088','sha256':'def'}))
        self.assertFalse(a.alias_source_matches(self.info,ts+60000,{'round':'260088','sha256':'def'}))
        self.assertFalse(a.alias_source_matches(self.info,ts,{'round':'260089','sha256':'def'}))
        self.assertFalse(a.alias_source_matches(self.info,ts,{'round':'260088','sha256':'bad'}))
    def test_reject_missing_kickoff_proof(self):
        info=dict(self.info,betman_sources=[])
        with self.assertRaisesRegex(ValueError,'missing alias source'):
            a.resolved_index({self.fid:{}},{},{'resolved':{self.fid:info}})
if __name__=='__main__':unittest.main()
