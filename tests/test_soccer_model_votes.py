import unittest
from scripts.link_soccer_model_votes import select_vote, pick, profit, blend, summarize

class SoccerVotesTest(unittest.TestCase):
 def test_reversed_source_order_uses_selected_team(self):
  x={'market':'moneyline','teams':['FC서울','FC안양'],'side':'away','selected_team':'FC서울','evidence_excerpt':'FC서울 승'}
  self.assertEqual(select_vote(x,{'home':'안양','away':'서울'}),(2,None))
 def test_ambiguous_and_cup_held(self):
  x={'market':'moneyline','teams':['FC서울','FC안양'],'side':'draw','evidence_excerpt':'무승부 또는 서울 승'}
  self.assertEqual(select_vote(x,{'home':'안양','away':'서울'})[1],'ambiguous_or_cup')
  x['evidence_excerpt']='컵 | FC서울 승'
  self.assertEqual(select_vote(x,{'home':'안양','away':'서울'})[1],'ambiguous_or_cup')
 def test_ties_abstain_and_flat_settlement(self):
  self.assertIsNone(pick([1,1,0]))
  self.assertIsNone(profit(None,[2,3,4],0))
  self.assertEqual(profit(1,[2,3,4],1),2)
  self.assertEqual(profit(1,[2,3,4],0),-1)
 def test_market_blend_and_tie_do_not_favor_an_index(self):
  self.assertEqual(pick(blend([.4,.3,.3],[0,0,1])),2)
  self.assertIsNone(pick([.4,.4+1e-14,.2]))
  with self.assertRaises(ValueError): blend([.4,.3,.3],[0,0,0])
 def test_abstention_is_not_a_bet_in_roi(self):
  rows=[{'outcome':0,'vote_pick':0,'vote_profit':1,'market_pick':None,'market_profit':None,'market90_vote10_pick':0,'market90_vote10_profit':1}]
  m=summarize(rows)
  self.assertEqual((m['market']['events'],m['market']['bets'],m['market']['abstentions'],m['market']['roi']),(1,0,1,None))
  self.assertEqual((m['market90_vote10']['hits'],m['market90_vote10']['roi']),(1,1))
if __name__=='__main__': unittest.main()
