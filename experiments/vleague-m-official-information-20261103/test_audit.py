import copy,unittest
from audit import candidate,summarize_match
class EvidenceTests(unittest.TestCase):
 def setUp(self):
  self.f={'seasonCode':'022','leagueCode':'201','gender':'1','gnum':3,'gdate':'2025-10-20','gstime':'19:00','hcode':'1006','acode':'1009','hspoint':0,'aspoint':3}
  self.r={'leagueCode':'VL001','homeId':'1006','awayId':'1009','homeName':'한국전력빅스톰','awayName':'서울우리카드우리WON','gameDate':1760954400000,'mchScore':'0:3','gameResult':'2','betId':None,'betTypId':None,'betNm':None,'handi':0,'winHandi':0,'drawHandi':0,'loseHandi':0,'drawAllot':0,'buyReject':'0','gameReject':'0','winAllot':1.9,'loseAllot':1.8}
 def test_candidate_rejects_mismatch_and_line(self):
  from datetime import datetime,timezone,timedelta
  self.r['gameDate']=int(datetime(2025,10,20,19,tzinfo=timezone(timedelta(hours=9))).timestamp()*1000)
  self.assertTrue(candidate(self.f,self.r))
  for k,v in [('homeId','2006'),('mchScore','3:0'),('handi',1.5),('gameResult','0'),('betNm','배구 핸디캡'),('buyReject','1')]:
   bad=copy.deepcopy(self.r);bad[k]=v;self.assertFalse(candidate(self.f,bad),k)
 def test_postgame_set_flags_and_libero_separated(self):
  g=dict(self.f);g.update({f'{side}{i}point':25 for side in ('hs','as') for i in range(1,6)})
  ps=[{'tcode':'1006','pcode':'s','pname':'setter','position':'S','ynS1':'-1','ynS2':'1'}, {'tcode':'1006','pcode':'l','pname':'libero','position':'L','ynS1':'-1','ynS2':'-1'}]
  d={'game':g,'player':ps};out=summarize_match(self.f,d); t=out['teams']['1006']
  self.assertEqual([p['id'] for p in t['set1_starters']],['s']);self.assertEqual(t['liberos'][0]['id'],'l');self.assertEqual(t['players'][0]['set_flags'][:2],['-1','1']);self.assertIn('postgame',out['availability'])
if __name__=='__main__':unittest.main()
