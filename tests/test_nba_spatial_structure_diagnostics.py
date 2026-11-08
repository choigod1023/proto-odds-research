import gzip, math, pathlib, tempfile, unittest
from scripts.nba_spatial_structure_diagnostics import weights, hard_zone, classify, sym_decompose, gzip_rows, sha, checked

class NBAProposalTests(unittest.TestCase):
    def test_uniform_spread_and_family_conservation(self):
        for n in (2,3):
            for r in (0,0.2,3,4,10,16,23.5,24,27,50):
                out=weights(n,{'x':25+r,'y':0})
                self.assertAlmostEqual(sum(out.values()),1)
                self.assertTrue(all(i in (range(0,4) if n==2 else range(5,8)) for i in out))
        self.assertEqual(weights(2,{'x':29,'y':0}),{0:0.5,1:0.5})
        self.assertEqual(weights(3,{'x':49,'y':0}),{5:0.5,6:0.5})
    def test_unknown_and_hard_boundary(self):
        for c in (None,{}, {'x':float('nan'),'y':0},{'x':float('inf'),'y':0},{'x':-214748340,'y':0}):
            self.assertEqual(weights(2,c),{4:1.0});self.assertEqual(weights(3,c),{8:1.0})
        self.assertEqual(hard_zone(2,{'x':29,'y':0}),1);self.assertEqual(hard_zone(3,{'x':49,'y':0}),6)
    def test_symmetric_decomposition(self):
        for qr,qm,sr,sm,p,w in [(0.5,0.25,0.6,0.33,99,2),(.3,.12,.2,.07,100,3)]:
            a,b=sym_decompose(qr,qm,sr,sm,p,w)
            self.assertAlmostEqual(a+b,w*p*(sm-qm),places=12)
    def test_result_is_independent_of_coordinate(self):
        p={'shootingPlay':True,'pointsAttempted':2,'scoringPlay':False,'scoreValue':0,'text':"Player blocks shooter's layup",'coordinate':None}
        self.assertEqual(classify(p),(2,False));p['coordinate']={'x':29,'y':0};self.assertEqual(classify(p),(2,False))
    def test_hash_fail_closed_and_gzip_deterministic(self):
        data=gzip_rows([{'x':1}]);self.assertEqual(data,gzip_rows([{'x':1}]));self.assertIn(b'"x":1',gzip.decompress(data))
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'test';p.write_bytes(data);self.assertEqual(checked(p,sha(data)),data);p.write_bytes(b'bad')
            with self.assertRaises(ValueError):checked(p,sha(data))
if __name__=='__main__':unittest.main()
