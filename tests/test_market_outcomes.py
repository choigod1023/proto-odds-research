import unittest
from scipy.stats import poisson
from src.market_outcomes import normalize_market, probabilities, settle


def options(kind='언더오버', line='8.5', rule='total_half', sides=('언더', '오버')):
    return [dict(market=kind, 게임번호='31', line=line, period='full_including_extra',
                 settlement_rule=rule, unit='runs', 선택=s) for s in sides]


class MarketTests(unittest.TestCase):
    def test_total_and_refund(self):
        t = normalize_market(options(), 'baseball', 'KBO')
        p = probabilities(t, 4, 4)
        self.assertAlmostEqual(sum(v['p_win'] for v in p.values()), 1)
        self.assertAlmostEqual(p['언더']['p_win'], 0.5925473414375918)
        self.assertEqual(settle(t, '언더', 4, 4, result_period=t['period'])['state'], 'win')
        self.assertEqual(settle(t, '오버', 4, 4, result_period=t['period'])['state'], 'loss')
        self.assertEqual(settle(t, '언더', 4, 4, status='cancelled', result_period=t['period'])['state'], 'hold')
        self.assertEqual(settle(t, '언더', 4, 4)['state'], 'hold')
        self.assertEqual(settle(t, '오버', status='void', result_period=t['period'])['state'], 'refund')
        t = normalize_market(options(line='8', rule='total_push'), 'baseball', 'KBO')
        self.assertEqual(settle(t, '언더', 4, 4, result_period=t['period'])['state'], 'refund')
        self.assertGreater(probabilities(t, 4, 4)['언더']['p_push'], 0)

    def test_three_way_home_signed_and_original_draw(self):
        t = normalize_market(options('핸디캡', '-1', 'handicap_3way', ('핸디홈', '핸디무', '핸디원정')), 'soccer', 'x')
        self.assertEqual(settle(t, '핸디원정', 2, 2, result_period=t['period'])['state'], 'win')
        self.assertEqual(settle(t, '핸디무', 2, 1, result_period=t['period'])['state'], 'win')
        self.assertAlmostEqual(sum(x['p_win'] for x in probabilities(t, 2, 1).values()), 1)

    def test_joint_poisson_independent_check(self):
        terms = normalize_market(options('핸디캡', '-2', 'handicap_3way', ('핸디홈', '핸디무', '핸디원정')), 'soccer', 'test')
        actual = probabilities(terms, 2.3, 1.7)
        mass = {'핸디홈': 0., '핸디무': 0., '핸디원정': 0.}
        for h in range(30):
            for a in range(30):
                side = '핸디홈' if h-a-2 > 0 else '핸디무' if h-a-2 == 0 else '핸디원정'
                mass[side] += poisson.pmf(h, 2.3)*poisson.pmf(a, 1.7)
        for side in mass:
            self.assertAlmostEqual(actual[side]['p_win'], mass[side], places=12)
            self.assertAlmostEqual(sum(actual[side][k] for k in ('p_win', 'p_push', 'p_loss')), 1.)

    def test_reject_ambiguous_or_mixed(self):
        for changed in ('게임번호', 'line', 'period', 'settlement_rule', 'unit'):
            a = options(); a[1][changed] = {'게임번호':'32', 'line':'9.5', 'period':'regulation', 'settlement_rule':'total_push', 'unit':'points'}[changed]
            with self.assertRaises(ValueError): normalize_market(a, 'baseball', 'KBO')
        for line in ('8.25', 'nan', None):
            with self.assertRaises(ValueError): normalize_market(options(line=line), 'baseball', 'KBO')
        with self.assertRaises(ValueError): normalize_market(options('핸디캡', '-1.5', 'unknown', ('핸디홈', '핸디원정')), 'baseball', 'KBO')
        with self.assertRaises(ValueError): normalize_market(options(line='8', rule='total_half'), 'baseball', 'KBO')
        with self.assertRaises(ValueError): normalize_market(options(sides=('언더', '언더')), 'baseball', 'KBO')


if __name__ == '__main__': unittest.main()
