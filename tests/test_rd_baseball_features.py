import importlib.util
from datetime import timedelta
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('rd',Path(__file__).parents[1]/'scripts/rd_baseball_features.py')
rd=importlib.util.module_from_spec(spec); spec.loader.exec_module(rd)

class WorkloadTests(unittest.TestCase):
    def setUp(self):
        self.cut=rd.dt('2026-04-03T12:00:00+00:00')
    def row(self,game=1,player=10,hours=12,pitches=20):
        return dict(game_id=game,player_id=player,team_id=7,pitches=pitches,end_time=(self.cut-timedelta(hours=hours)).isoformat())
    def test_future_exact_cutoff_target_and_old_excluded(self):
        rows=[self.row(),self.row(2,hours=-1),self.row(3,hours=0),self.row(4,hours=73),self.row(99)]
        values,used=rd.workload(rows,7,self.cut,99)
        self.assertEqual(values['relief_pitches_72h'],20)
        self.assertEqual(len(used),1)
    def test_doubleheader_counts_games_not_duplicate_rows_or_dates(self):
        a=self.row(1,pitches=18); b=self.row(2,pitches=17)
        v,_=rd.workload([a,a,b],7,self.cut,99)
        self.assertEqual(v['relief_pitches_72h'],35)
        self.assertEqual(v['relievers_ge30_pitches_72h'],1)
        self.assertEqual(v['relievers_used_ge2_dates_72h'],0)
    def test_48_and_72_windows_and_two_dates(self):
        v,_=rd.workload([self.row(hours=12),self.row(2,hours=60)],7,self.cut,99)
        self.assertEqual(v['relief_pitches_48h'],20)
        self.assertEqual(v['relief_pitches_72h'],40)
        self.assertEqual(v['relievers_used_ge2_dates_72h'],1)
    def test_lineup_requires_nine_exact_starters(self):
        players=[dict(id=i,battingOrder=str(i*100)) for i in range(1,10)]
        self.assertTrue(rd.complete_lineup(players))
        self.assertFalse(rd.complete_lineup(players[:-1]))
        players[-1]['battingOrder']='801'
        self.assertFalse(rd.complete_lineup(players))
    def test_missing_end_and_target_never_read_box(self):
        self.assertEqual(rd.prior_relief({},dict(gamePk=1),99,self.cut),[])
        self.assertEqual(rd.prior_relief({},dict(gamePk=99,end_time=(self.cut-timedelta(hours=1)).isoformat()),99,self.cut),[])
    def test_season_totals_ignored(self):
        player=dict(person={'id':10},stats={'pitching':{'gamesStarted':0,'numberOfPitches':15}},seasonStats={'pitching':{'numberOfPitches':99999}})
        box={'teams':{s:{'team':{'id':7},'players':{'ID10':player}} for s in ['home','away']}}
        g=dict(gamePk=1,end_time=(self.cut-timedelta(hours=1)).isoformat(),source_sha256='hash',observed_at='2026-10-30T00:00:00+00:00')
        self.assertEqual(rd.prior_relief(box,g,99,self.cut)[0]['pitches'],15)
    def test_calendar_date_lag_excludes_recent_and_target_dates(self):
        rows=[dict(gamePk=i,player=10,date=f'2026-04-{i:02}',pitches=20) for i in range(1,7)]
        value,used=rd.calendar_workload(rows,rd.dt('2026-04-06T12:00:00+00:00'))
        self.assertEqual(value,1)
        self.assertEqual({r['date'] for r in used},{'2026-04-02','2026-04-03','2026-04-04'})
    def test_calendar_missing_pitch_count_is_not_zero(self):
        with self.assertRaises(ValueError):
            rd.calendar_workload([dict(gamePk=1,player=10,date='2026-04-02',pitches=None)],rd.dt('2026-04-06T12:00:00+00:00'))
    def test_missing_relief_pitches_fails_closed(self):
        player=dict(person={'id':10},stats={'pitching':{'gamesStarted':0}})
        box={'teams':{s:{'team':{'id':7},'players':{'ID10':player}} for s in ['home','away']}}
        g=dict(gamePk=1,end_time=(self.cut-timedelta(hours=1)).isoformat(),source_sha256='hash',observed_at='2026-10-30T00:00:00+00:00')
        with self.assertRaises(ValueError): rd.prior_relief(box,g,99,self.cut)
    def test_fact_date_is_latest_selected_not_window_ceiling(self):
        cutoff=rd.dt('2026-04-06T12:00:00+00:00')
        value,used=rd.calendar_workload([dict(gamePk=1,player=10,date='2026-04-02',pitches=5)],cutoff)
        self.assertEqual(value,0)
        self.assertEqual(rd.calendar_fact_dates(used),dict(max_fact_date='2026-04-02',fact_date_timezone='UTC',selected_fact_dates=['2026-04-02']))
    def test_fact_dates_preserve_actual_selected_dates(self):
        evidence=rd.calendar_fact_dates([{'date':'2026-04-04'},{'date':'2026-04-02'},{'date':'2026-04-04'}])
        self.assertEqual(evidence['max_fact_date'],'2026-04-04')
        self.assertEqual(evidence['selected_fact_dates'],['2026-04-02','2026-04-04'])
        self.assertNotIn('max_fact_time',evidence)
    def test_fact_dates_do_not_invent_missing_coverage(self):
        for records in ([],[{}],[{'date':None}]):
            with self.assertRaises(ValueError): rd.calendar_fact_dates(records)
    def test_timezone_required(self):
        with self.assertRaises(ValueError): rd.dt('2026-04-03T12:00:00')

if __name__=='__main__': unittest.main()
