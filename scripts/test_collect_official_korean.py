"""Run: python -m unittest discover -s scripts -p test_collect_official_korean.py"""
import unittest

from collect_official_korean import normalize_kbl, normalize_kovo


class NormalizationTests(unittest.TestCase):
    def basketball(self, **updates):
        return dict(seasonGrade=1, seasonCategory='R', gameDate='20250101',
                    gameStart='1400', isEnded=1, tcodeH='06', tcodeA='60',
                    scoreH=86, scoreA=68, gmkey='S45G01N116',
                    tnameFH='수원 KT 소닉붐', tnameFA='부산 KCC 이지스', **updates)

    def volleyball(self):
        x = dict(gdate='2025-04-08', gstime='19:00', hspoint=3, aspoint=2,
                 gender='2', hcode='2004', acode='2003', hname='흥국생명',
                 aname='정관장', seasonCode='021', leagueCode='203', gnum=263)
        for i, (a, b) in enumerate([(25, 20), (20, 25), (25, 20), (20, 25), (15, 10)], 1):
            x[f'hs{i}point'], x[f'as{i}point'] = a, b
        return x

    def test_kbl_alias_time_score(self):
        x = normalize_kbl(self.basketball(), 'https://official', 2025)
        self.assertEqual((x['home'], x['away'], x['home_score'], x['away_score']),
                         ('KT소닉붐', '부산KCC', 86, 68))
        self.assertEqual(x['kickoff'], '2025-01-01T14:00:00+09:00')

    def test_kbl_excluded_competition(self):
        for field, value in [('seasonGrade', 2), ('seasonCategory', 'EA')]:
            x = self.basketball()
            x[field] = value
            self.assertIsNone(normalize_kbl(x, 'url', 2025))

    def test_kbl_unfinished_wrong_year_unknown_team(self):
        for field, value in [('isEnded', 0), ('gameDate', '20260101'), ('tcodeH', 'unknown')]:
            x = self.basketball()
            x[field] = value
            with self.assertRaises((ValueError, KeyError)):
                normalize_kbl(x, 'url', 2025)

    def test_volleyball_sets_not_points(self):
        x = normalize_kovo(self.volleyball(), 'url', 2025, '001')
        self.assertEqual((x['league'], x['home_score'], x['away_score']), ('KOVO여', 3, 2))
        self.assertEqual(x['away'], '대전정관')

    def test_volleyball_other_year(self):
        self.assertIsNone(normalize_kovo(self.volleyball(), 'url', 2024, '001'))

    def test_volleyball_inconsistent_sets_or_missing_kickoff(self):
        for field, value in [('hs5point', 0), ('gstime', '-'), ('hspoint', 1)]:
            x = self.volleyball()
            x[field] = value
            with self.assertRaises(ValueError):
                normalize_kovo(x, 'url', 2025, '001')


if __name__ == '__main__':
    unittest.main()
