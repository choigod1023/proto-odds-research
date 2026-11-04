import importlib.util, unittest
from pathlib import Path
p=Path(__file__).with_name('audit.py');spec=importlib.util.spec_from_file_location('audit',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class SettlementTest(unittest.TestCase):
    def test_totals(self):
        self.assertEqual(m.settlement('언더오버',2.5,1,1,2),0)
        self.assertEqual(m.settlement('언더오버',2.5,2,1,2),1)
        self.assertIsNone(m.settlement('언더오버',2,1,1,2))
    def test_three_way_handicap_and_unsupported_two_way(self):
        self.assertEqual(m.settlement('핸디캡',-1,2,1,3),1)
        self.assertEqual(m.settlement('핸디캡',1,1,1,3),0)
        self.assertEqual(m.settlement('핸디캡',-1,0,2,3),2)
        self.assertIsNone(m.settlement('핸디캡',-1,2,1,2))
    def test_main_draw(self):
        self.assertEqual(m.settlement('승무패',None,1,1,3),1)
if __name__=='__main__': unittest.main()
