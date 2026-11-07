import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from scripts.recover_hybrid_history import kickoff, pair_primary


class RecoveryTests(unittest.TestCase):
    def test_korean_kickoff(self):
        self.assertEqual(kickoff(dict(year=2026,date='08.30(일) 03:20')).isoformat(),
                         '2026-08-30T03:20:00+09:00')

    def test_optional_score_probability_does_not_block_primary(self):
        stamp=datetime.fromisoformat
        replay=SimpleNamespace(stamp=stamp,ET=timezone.utc)
        ledger=[dict(event_id='e',kickoff='2026-08-30T18:00:00+00:00',
                     captured_at='2026-08-30T11:00:00+00:00',snapshot_id='sha',
                     features=dict(options=[dict(selection='홈',final_probability=.6,odds=1.8),
                                            dict(selection='원정',final_probability=.4,odds=2.1)]))]
        games=[dict(event_id='e',home='A',away='B')]
        pool=[dict(event='2026-08-30|A|B',kickoff=stamp(ledger[0]['kickoff']),
                   observed=stamp(ledger[0]['captured_at']),id=str(i),author=str(i),
                   side='A',outcome='W',settled=stamp('2026-08-30T22:00:00+00:00')) for i in range(3)]
        rows,_,_=pair_primary(ledger,games,pool,replay,dict(A='A',B='B'))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['arms']['production'],.6)
        pool[0]['observed']=stamp('2026-08-30T13:00:00+00:00')
        self.assertEqual(pair_primary(ledger,games,pool,replay,dict(A='A',B='B'))[0],[])
