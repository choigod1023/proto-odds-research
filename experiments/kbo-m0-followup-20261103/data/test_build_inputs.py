import copy,math,unittest
import build_inputs as b
class ValidationTest(unittest.TestCase):
 def setUp(self):
  self.label={'market_id':'proto:2024:37:090:full_game_2way','settlement_rule':'draw_void_refund','home_score':8,'away_score':2}
  self.market={'market_id':self.label['market_id'],'kind':'full_game_2way','settlement_rule':'draw_void_refund','execution_price_verified':False,'selections':{'home':1.67,'away':1.86}}
  self.origin={'source_id':'domestic_market','sha256':'a'*64}
  self.csv={'year':'2024','date_text':'03.23(토) 14:00','home':'LG 8','away':'2 한화','odds':'1.67,1.86','is_void':'False','market_type':'승패(2-way)','booking_class':'2-way'}
  self.game={'G_DT':'20240323','HOME_NM':'LG','AWAY_NM':'한화','HOME_ID':'LG','AWAY_ID':'HH','HEADER_NO':0,'G_ID':'20240323HHLG0'}
 def test_quote_validity_and_missing(self):
  self.assertEqual(b.quote_reasons(self.market,self.label,self.origin),[])
  self.assertEqual(b.quote_reasons(None,self.label,self.origin),['market_missing'])
  for x in (None,True,1,0,float('nan'),float('inf'),'1.5'):
   self.assertFalse(b.valid_price(x))
  self.assertTrue(b.valid_price(1.0001))
 def test_market_contract_and_two_sides(self):
  x=copy.deepcopy(self.market);x['kind']='handicap';x['settlement_rule']='other';x['selections']={'home':1.5};x['market_id']='different';x['execution_price_verified']=True
  reasons=b.quote_reasons(x,self.label,self.origin)
  for name in ('market_kind_conflict','settlement_rule_conflict','two_sided_price_missing','market_id_label_conflict','execution_status_conflict'):self.assertIn(name,reasons)
 def test_raw_quote_matching_and_conflicts(self):
  args=([self.csv],self.market,'2024-03-23','LG','한화',self.label,'2024-03-23T14:00:00+09:00')
  self.assertEqual(b.csv_quote_reasons(*args),[]);self.assertEqual(b.csv_quote_reasons([], *args[1:]),['raw_quote_source_missing']);self.assertEqual(b.csv_quote_reasons([self.csv,self.csv], *args[1:]),['raw_quote_source_duplicate'])
  x=copy.deepcopy(self.csv);x['odds']='1.67,1.91';self.assertIn('raw_quote_prices_conflict',b.csv_quote_reasons([x],*args[1:]))
  x=copy.deepcopy(self.csv);x['away']='3 한화';self.assertIn('raw_quote_score_conflict',b.csv_quote_reasons([x],*args[1:]))
  x=copy.deepcopy(self.csv);x['is_void']='True';self.assertIn('raw_quote_invalid_or_void',b.csv_quote_reasons([x],*args[1:]))
  self.assertIn('raw_quote_kickoff_timezone_conflict',b.csv_quote_reasons([self.csv],self.market,'2024-03-23','LG','한화',self.label,'2024-03-23T14:00:00+00:00'))
 def test_identity_aliases_and_doubleheader(self):
  self.assertEqual(b.fixture_check('20240323HHLG02024','2024-03-23','LG','한화',self.game),'exact_official_game_id')
  self.assertEqual(b.fixture_check('score:2024-03-23:LG:한화','2024-03-23','LG','한화',self.game),'unique_score_date_team_alias')
  self.assertEqual(b.fixture_check('77770323HHLG02024','2024-03-23','LG','한화',self.game),'encoded_year_official_game_alias')
  with self.assertRaises(ValueError):b.fixture_check('20240323HHLG12024','2024-03-23','LG','한화',self.game)
  self.assertTrue(b.score_alias_unambiguous({'game':[self.game]},'2024-03-23','LG','한화'))
  other=copy.deepcopy(self.game);other.update(HEADER_NO=1,G_ID='20240323HHLG1')
  self.assertFalse(b.score_alias_unambiguous({'game':[self.game,other]},'2024-03-23','LG','한화'))
 def test_duplicate_fixture_rejected(self):
  with self.assertRaises(ValueError):b.index([{'fixture_id':'x'},{'fixture_id':'x'}])
if __name__=='__main__':unittest.main()
