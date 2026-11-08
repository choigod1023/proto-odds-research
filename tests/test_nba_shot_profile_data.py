import gzip
import math
import pathlib
import tempfile
import unittest
from scripts.export_nba_shot_profile import classify, deterministic_gzip, digest, verified, zone

class NBAProfileTests(unittest.TestCase):
    def test_radial_boundaries(self):
        for n,r,expected in [(2,0,0),(2,3.999,0),(2,4,1),(2,10,2),(2,16,3),(3,23.999,5),(3,24,6),(3,27,7)]:
            self.assertEqual(zone(n,{'x':25+r,'y':0}),expected)
    def test_missing_nonfinite_and_sentinel_are_retained_unknown(self):
        for value in (None,{}, {'x':float('nan'),'y':1},{'x':float('inf'),'y':1},{'x':-214748340,'y':1},{'x':True,'y':1}):
            self.assertEqual(zone(2,value),4);self.assertEqual(zone(3,value),8)
    def test_result_is_independent_of_coordinates_and_blocked_miss(self):
        play={'shootingPlay':True,'pointsAttempted':2,'scoringPlay':False,'scoreValue':0,'text':"Player blocks shooter's 9-foot layup",'coordinate':{'x':25,'y':1}}
        self.assertEqual(classify(play),(2,False));play['coordinate']={'x':999999,'y':0};self.assertEqual(classify(play),(2,False));self.assertEqual(zone(2,play['coordinate']),4)
        play.update(scoringPlay=True,scoreValue=2,text='Player makes 2-foot layup'); self.assertEqual(classify(play),(2,True));play['coordinate']=None;self.assertEqual(classify(play),(2,True))
    def test_hash_tampering_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'x';p.write_bytes(b'valid');self.assertEqual(verified(p,digest(b'valid')),b'valid');p.write_bytes(b'changed')
            with self.assertRaises(ValueError):verified(p,digest(b'valid'))
    def test_deterministic_gzip(self):
        rows=[{'a':[1,2],'name':'NBA'},{'b':3}];a=deterministic_gzip(rows);self.assertEqual(a,deterministic_gzip(rows));self.assertEqual(len(gzip.decompress(a).splitlines()),2)
    def test_wrong_shot_value_is_rejected(self):
        p={'shootingPlay':True,'pointsAttempted':3,'scoringPlay':False,'scoreValue':3,'text':'Player misses three point jumper'}
        self.assertIsNone(classify(p))

if __name__=='__main__':unittest.main()
