import unittest
import tempfile, pathlib, json, hashlib
from scripts.audit_nba_shots import classify, geom, box_counts, read_checked

class ShotAuditTests(unittest.TestCase):
    def test_missed_three_uses_attempt_not_score(self):
        p={'shootingPlay':True,'pointsAttempted':3,'scoringPlay':False,'scoreValue':0,'text':'Player misses 26-foot three point jumper'}
        self.assertEqual(classify(p),(3,False))
        p['scoreValue']=3
        self.assertIsNone(classify(p))
    def test_free_throw_and_inconsistent_text(self):
        p={'shootingPlay':True,'pointsAttempted':1,'scoringPlay':True,'scoreValue':1,'text':'Player makes free throw 1 of 2'}
        self.assertEqual(classify(p),(1,True))
        p['pointsAttempted']=3
        self.assertIsNone(classify(p))
    def test_hypotheses_are_not_selection(self):
        r=geom(25,15.25,10)
        self.assertEqual(r['xy_scale1_basket5.25_half'],0)
        self.assertEqual(len(r),16)
    def test_404_body_receipt_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=pathlib.Path(tmp); body=b'not found'
            (root/'401.unavailable.body').write_bytes(body)
            receipt=root/'401.unavailable.receipt.json'
            receipt.write_text(json.dumps({'status':404,'sha256':hashlib.sha256(body).hexdigest()}),encoding='utf8')
            status,data,ok=read_checked(receipt)
            self.assertEqual(status['status'],404)
            self.assertEqual(data,body)
            self.assertTrue(ok)
            (root/'401.unavailable.body').write_bytes(b'corrupt')
            self.assertFalse(read_checked(receipt)[2])
    def test_box_requires_all_metrics(self):
        self.assertIsNone(box_counts({'statistics':[]}))

if __name__=='__main__': unittest.main()
