import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from shot_context_pilot import extract, summarize


def match(h=1,a=0):
    return {'match_id':1,'match_date':'2015-08-08','home_team':{'home_team_id':1},
            'away_team':{'away_team_id':2},'home_score':h,'away_score':a}


def event(i,kind='Shot',team=1,goal=False):
    return {'id':str(i),'index':i,'period':1,'minute':i,'second':0,'team':{'id':team},
            'type':{'name':kind},'location':[100,40],'player':{'id':team*10},
            'shot':{'statsbomb_xg':.2,'outcome':{'name':'Goal' if goal else 'Saved'},'type':{'name':'Open Play'}}}


class ContextTests(unittest.TestCase):
    def test_goal_context_is_before_goal(self):
        shots,_=extract(match(),[event(1,goal=True),event(2),event(3,team=2)])
        self.assertEqual([s['score_diff'] for s in shots],[0,1,-1])

    def test_red_deduplicated_and_from_shooting_team_perspective(self):
        a=event(1,'Foul Committed');a['foul_committed']={'card':{'name':'Second Yellow'}}
        b=event(2,'Bad Behaviour');b['bad_behaviour']={'card':{'name':'Red Card'}}
        start=event(0,'Starting XI');start['tactics']={'lineup':[{'player':{'id':10}}]}
        shots,audit=extract(match(0,0),[start,a,b,event(3),event(4,team=2)])
        self.assertEqual([s['man_diff'] for s in shots],[-1,1])
        self.assertEqual(audit['dismissals'],1)

    def test_own_goal_pair_not_double_counted(self):
        shots,_=extract(match(0,1),[event(1,'Own Goal Against'),event(2,'Own Goal For',team=2),event(3)])
        self.assertEqual(shots[0]['score_diff'],-1)

    def test_missing_xg_not_zero(self):
        e=event(1);e['shot'].pop('statsbomb_xg')
        with self.assertRaisesRegex(ValueError,'xG'):extract(match(0,0),[e])

    def test_mismatch_fails_closed(self):
        with self.assertRaisesRegex(ValueError,'mismatch'):extract(match(),[event(1)])

    def test_duplicate_and_shootout(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):extract(match(0,0),[event(1),event(1)])
        e=event(1,goal=True);e['period']=5
        shots,audit=extract(match(0,0),[e])
        self.assertEqual(shots,[]);self.assertEqual(audit['non_regulation_events'],1)

    def test_summary_excludes_penalties(self):
        e=event(1,goal=True);e['shot']['type']['name']='Penalty'
        shots,_=extract(match(),[e]);self.assertEqual(summarize(shots),{})

    def test_bench_red_does_not_reduce_manpower(self):
        start=event(0,'Starting XI');start['tactics']={'lineup':[{'player':{'id':11}}]}
        red=event(1,'Bad Behaviour');red['bad_behaviour']={'card':{'name':'Red Card'}}
        shots,audit=extract(match(0,0),[start,red,event(2)])
        self.assertEqual(shots[0]['man_diff'],0)
        self.assertEqual(audit['off_field_dismissals'],1)


if __name__=='__main__':unittest.main()
