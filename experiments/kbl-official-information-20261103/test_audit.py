import copy, json, unittest
from pathlib import Path
from analyze import validate
P=Path(__file__).resolve().parent
class AuditTests(unittest.TestCase):
 def setUp(self):
  c=json.loads((P/'census.json').read_text(encoding='utf8'))
  self.f=next(x for x in c['regular_season_fixtures'] if x['gmkey']=='S47G01N1')
  self.h=json.loads((P/'evidence/postgame-S47G01N1-header.body').read_bytes())
  self.p=json.loads((P/'evidence/postgame-S47G01N1-player-stat.body').read_bytes())
 def test_real_sample(self):
  a=validate(self.f,self.h,self.p); self.assertEqual(a['starters'],10); self.assertEqual(a['players'],24); self.assertEqual(a['dnp_zero_time'],4)
 def test_wrong_teams(self):
  h=copy.deepcopy(self.h);h['game']['tcodeH']='99'
  with self.assertRaises(ValueError):validate(self.f,h,self.p)
 def test_duplicate_player(self):
  p=copy.deepcopy(self.p);p[1]['player']['pcode']=p[0]['player']['pcode']
  with self.assertRaises(ValueError):validate(self.f,self.h,p)
 def test_missing_starter(self):
  p=copy.deepcopy(self.p);next(x for x in p if x['startFlag']==1)['startFlag']=0
  with self.assertRaises(ValueError):validate(self.f,self.h,p)
if __name__=='__main__':unittest.main()
