import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from compare_matched_tipsters import compare,validate
from matched_tipster_replay import replay_tickets,stamp
from verify_tipster_mlb_results import reconcile


class MatchedTipsterTests(unittest.TestCase):
    def row(self, **kw):
        r=dict(id='a',event='a',date='2026-09-01',model=[.6,.4],tipster=[0.,1.],odds=[1.7,2.2],y=1)
        return dict(r,**kw) if not set(r)&set(kw) else {**r,**kw}

    def test_tipsters_actually_change_decisions(self):
        r=compare([self.row()])
        self.assertEqual(r['1.0']['picks'][0]['side'],0)
        self.assertEqual(r['0.75']['picks'][0]['side'],1)
        self.assertEqual(r['0.0']['picks'][0]['side'],1)
        self.assertAlmostEqual(r['0.75']['all_matched_single']['roi_pct'],120.)

    def test_pending_not_filtered_before_selection(self):
        r=compare([self.row(y=None)])['0.0']['policies']['probability']
        self.assertEqual(r['recommendations'],1)
        self.assertEqual(r['single']['pending'],1)
        self.assertIsNone(r['single']['roi_pct'])

    def test_outcomes_do_not_change_picks(self):
        a=compare([self.row(y=0)]);b=compare([self.row(y=1)])
        for w in a:
            self.assertEqual(a[w]['picks'],b[w]['picks'])
            self.assertEqual(a[w]['policies']['probability']['selected'],b[w]['policies']['probability']['selected'])

    def test_soccer_draw_preserved(self):
        r=self.row(model=[.2,.6,.2],tipster=[0,1.,0],odds=[2.5,3.,2.5],y=1)
        self.assertEqual(compare([r])['1.0']['all_matched_single']['hits'],1)

    def test_invalid_vector_fails(self):
        for r in [self.row(tipster=[1,1]),self.row(odds=[float('nan'),2]),self.row(y=4)]:
            with self.assertRaises(ValueError):validate(r)

    def test_monthly_limit_with_unknown_results(self):
        recs=[]
        for day in range(1,22):
            for game in range(2):
                recs.append(dict(id=f'{day}/{game}',event=f'{day}/{game}',round_key='r',
                    at=f'2026-09-{day:02}T12:00:00+09:00',snapshot_at=f'2026-09-{day:02}T11:00:00+09:00',
                    kickoff=f'2026-09-{day:02}T19:00:00+09:00',probability=.7,price=1.5,edge=.05,side=0,y=None,settled_at=None))
        r=replay_tickets(recs,'probability',3,stamp('2026-09-30T23:00:00+09:00'))
        self.assertEqual(r['pending'],5)
        self.assertEqual(r['worst_case_net_krw'],-50000)

    def test_official_doubleheader_requires_time_match(self):
        teams={'teams':[{'id':1,'abbreviation':'NYY'},{'id':2,'abbreviation':'BOS'}]}
        def game(pk,time):
            return dict(gamePk=pk,gameDate=time,status={'abstractGameState':'Final'},
                 teams={'home':{'team':{'id':1},'score':3},'away':{'team':{'id':2},'score':1}})
        schedule={'dates':[{'date':'2026-09-01','games':[game(1,'2026-09-01T18:00:00Z'),game(2,'2026-09-01T23:00:00Z')]}]}
        row=self.row(home='NYY',away='BOS',kickoff='2026-09-01T18:00:00+00:00',y=None)
        rows,audit=reconcile([row],teams,schedule)
        self.assertEqual(rows[0]['official']['game_pk'],1)
        self.assertEqual(audit,{'newly_resolved':1})
        row['kickoff']='2026-09-01T20:00:00+00:00'
        with self.assertRaises(ValueError):reconcile([row],teams,schedule)


if __name__=='__main__':unittest.main()
