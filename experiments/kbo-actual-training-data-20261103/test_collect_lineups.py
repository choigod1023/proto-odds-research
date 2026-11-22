import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import collect_lineups as c

def lineup(game):
 body=[[{'LINEUP_CK':True}]]
 for side in ('home','away'):body.append([{'G_ID':game['G_ID'],'T_ID':game[side.upper()+'_ID'],'LE_ID':game['LE_ID'],'SR_ID':game['SR_ID'],'SEASON_ID':game['SEASON_ID']}])
 for side in ('home','away'):
  rows=[{'row':[{'Text':str(i)},{'Text':'OF'},{'Text':side+str(i)}]} for i in range(1,10)]
  body.append([json.dumps({'rows':rows})])
 return body
class CollectorTest(unittest.TestCase):
 def setUp(self):self.game={'G_ID':'20231019OBNC0','HOME_ID':'NC','AWAY_ID':'OB','LE_ID':1,'SR_ID':4,'SEASON_ID':2023}
 def test_lineup_flag_and_game_team_match_required(self):
  b=lineup(self.game);self.assertEqual(len(c.parse_lineup(json.dumps(b).encode(),self.game)['home']),9)
  b[0][0]['LINEUP_CK']=False;self.assertIsNone(c.parse_lineup(json.dumps(b).encode(),self.game))
  b=lineup(self.game);b[1][0]['T_ID']='WRONG'
  with self.assertRaises(ValueError):c.parse_lineup(json.dumps(b).encode(),self.game)
 def test_order_is_not_inferred_from_boxscore(self):
  b=lineup(self.game);s=json.loads(b[3][0]);s['rows'][0]['row'][0]['Text']='2';b[3][0]=json.dumps(s)
  with self.assertRaises(ValueError):c.parse_lineup(json.dumps(b).encode(),self.game)
 def test_429_backoff_counts_requests_and_cache(self):
  with tempfile.TemporaryDirectory() as tmp, patch.object(c.time,'sleep') as sleep:
   f=c.Fetcher(Path(tmp),3,1.5);f.session.request=unittest.mock.Mock(side_effect=[SimpleNamespace(status_code=429,content=b'rate'),SimpleNamespace(status_code=200,content=b'ok')])
   raw,meta=f.fetch('GET','https://www.koreabaseball.com/example',{'id':'1'});self.assertEqual(raw,b'ok');self.assertEqual(f.requests,2);self.assertTrue(sleep.called)
   self.assertEqual(len((Path(tmp)/'request-failures.jsonl').read_text().splitlines()),1)
   f.fetch('GET','https://www.koreabaseball.com/example',{'id':'1'});self.assertEqual(f.requests,2)
if __name__=='__main__':unittest.main()
