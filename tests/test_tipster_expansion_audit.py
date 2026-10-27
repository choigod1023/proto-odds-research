import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('expansion', Path(__file__).resolve().parents[1]/'scripts/audit_tipster_expansion.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class TipsterExpansionTests(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(audit.article_date('[KBL] 2025년 01월 2일 경기'), '2025-01-02')
        self.assertIsNone(audit.article_date('2025년 02일 03일'))

    def test_doubleheader_and_league_rejection(self):
        article={'title':'[야구] [MLB] 2025년 5월 1일 보스턴 vs 뉴욕양키스'}
        aliases={'보스레드':['보스턴'],'뉴욕양키':['뉴욕양키스']}
        games=[dict(league='MLB',home='보스레드',away='뉴욕양키',official_id=str(i)) for i in range(2)]
        self.assertEqual(len(audit.identity_candidates(article,games,aliases)),2)
        # Neither score nor winning side is consulted to pick one game.
        games[0]['league']='NBA'
        self.assertEqual(len(audit.identity_candidates(article,games,aliases)),1)
        self.assertEqual(audit.article_leagues('[배구] [KBL] 경기'),{'KBL'})
        self.assertEqual(audit.article_leagues('[축구] [메이저리그사커] 경기'),set())

    def test_no_ambiguous_tip(self):
        aliases={'홈':['홈팀'],'원정':['원정팀']}
        self.assertEqual(audit.parse_tip({'pick':'원정팀 승'},'홈','원정',aliases,'sc'),2)
        self.assertIsNone(audit.parse_tip({'pick':'홈팀 또는 원정팀 승'},'홈','원정',aliases,'sc'))
        self.assertIsNone(audit.parse_tip({'pick':'무승부'},'홈','원정',aliases,'vl'))
        self.assertEqual(audit.parse_tip({'pick':'무승부'},'홈','원정',aliases,'sc'),1)
        self.assertIsNone(audit.parse_tip({'pick':'홈팀','body':'승무패: 홈팀 핸디캡 승리 핸디캡: +1.5'},'홈','원정',aliases,'vl'))
        self.assertEqual(audit.parse_tip({'pick':'홈팀 패'},'홈','원정',aliases,'sc'),2)
        self.assertEqual(audit.parse_tip({'pick':'원정팀 패배'},'홈','원정',aliases,'bk'),0)
        self.assertIsNone(audit.parse_tip({'pick':'홈팀 승리 아님'},'홈','원정',aliases,'sc'))
        self.assertIsNone(audit.parse_tip({'pick':'홈팀 무패'},'홈','원정',aliases,'sc'))
        self.assertIsNone(audit.parse_tip({'pick':'홈팀'},'홈','원정',aliases,'sc'))

    def test_probability_flip_threshold(self):
        self.assertLess(audit.probabilities([.55,.45],[.55,.45],1)['research90_tipster10'][0],.5)
        self.assertGreater(audit.probabilities([.56,.44],[.56,.44],1)['research90_tipster10'][0],.5)
        self.assertEqual(audit.probabilities([.7,.3],[.7,.3],0)['research90_tipster10'],[.73,.27])

    def test_invalid_probability(self):
        with self.assertRaises(ValueError): audit.probabilities([.5,.5],[.5,.9],1)

    def row(self, day, won=False):
        return dict(date=day,kickoff=day+' 12:00',sport='bk',home='H',away='A',odds=[2,2],winner=0 if won else 1,
                    picks={'tipster':0})

    def test_cap_reserves_concurrent_losses(self):
        result=audit.capped([self.row('2025-01-01') for _ in range(20)]+[self.row('2025-01-02',True)],'tipster')
        self.assertEqual(result['n'],5)
        self.assertEqual(result['monthly_net'],{'2025-01':-50000})

    def test_month_reset(self):
        result=audit.capped([self.row('2025-01-01') for _ in range(5)]+[self.row('2025-02-01',True)],'tipster')
        self.assertEqual(result['n'],6)
        self.assertEqual(result['monthly_net']['2025-02'],10000)


if __name__=='__main__': unittest.main()
