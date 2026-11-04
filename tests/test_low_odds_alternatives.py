import unittest
from datetime import datetime, timezone
from scripts.study_low_odds_alternatives import CUTOFF, price, pregame, alternative, exact_official, stats

class LowOddsStudyTests(unittest.TestCase):
    def test_fixed_boundary(self):
        self.assertLessEqual(price('1.20'), CUTOFF)
        self.assertGreater(price('1.2001'), CUTOFF)
        for p in ('nan', 'inf', 1, None): self.assertIsNone(price(p))

    def test_capture_must_precede_kickoff(self):
        r={'kickoff':'2026-01-01T10:00:00Z','as_of':'2026-01-01T09:00:00Z','market_observed_at':'2026-01-01T09:00:00Z','captured_at':'2026-01-01T10:00:00Z'}
        self.assertFalse(pregame(r)); r['captured_at']='2026-01-01T09:59:59Z'; self.assertTrue(pregame(r))

    def test_alternative_is_same_product_and_never_uses_result(self):
        original={'offer_id':'a','selection_id':'a','market':'승무패','label':'','line':None,'odds':1.2}
        valid={'offer_id':'b','selection_id':'b','market':'승무패','label':'','line':None,'odds':3,'final_probability':0.2}
        wrong={'offer_id':'c','selection_id':'c','market':'핸디캡','label':'H -1','line':-1,'odds':2,'final_probability':0.9}
        r={'predictions':original,'features':{'options':[original,valid,wrong]},'result':'irrelevant'}
        self.assertEqual(alternative(r)['selection_id'],'b'); r['result']='changed'; self.assertEqual(alternative(r)['selection_id'],'b')

    def test_exact_product_line_time_and_period(self):
        r={'kickoff':'2026-09-06T01:30:00Z','predictions':{'market':'핸디캡','label':'H -2.0'}}
        c={'market_text':'축구 핸디캡','label':'H -2.0','kickoff_text':'20260906103000','game_no':'1','evidence_sha256':'hash'}
        s={'source':{'official_source':c}}
        self.assertIsNotNone(exact_official(r,s,{}))
        for key,value in [('label','H +2.0'),('kickoff_text','20260906113000'),('market_text','축구 전반 핸디캡')]:
            old=c[key]; c[key]=value; self.assertIsNone(exact_official(r,s,{})); c[key]=old
        self.assertIsNone(exact_official(r,{'source':{'official_scores':[{'official_source':c},{'official_source':c}], 'mode':'official_score_with_frozen_terms'}},{}))

    def test_roi_void_and_pending(self):
        rows=[{'event_id':str(i),'kickoff':f'2026-01-0{i+1}','odds':2.0,'result':v} for i,v in enumerate(['hit','miss','void',None])]
        x=stats(rows); self.assertEqual((x['settled'],x['pending'],x['hit'],x['miss'],x['void']), (3,1,1,1,1)); self.assertEqual(x['roi_settled_stake'],0)

if __name__=='__main__': unittest.main()
