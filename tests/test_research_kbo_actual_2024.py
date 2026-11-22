import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from research_kbo_actual_2024_finalize import parse_roster, candidate_ids, profile_name
from research_kbo_actual_2024_capture import game_day, schedule


def table(rows, headers=None):
    return json.dumps({'rows':[{'row':[{'Text':str(v)} for v in row]} for row in rows],
                       'headers':[{'row':[{'Text':v} for v in (headers or [])]}]}, ensure_ascii=False)


def sample():
    lineup = [[], [], [], [], []]
    box = {'arrHitter':[], 'arrPitcher':[]}
    game = {}
    for side, idx, prefix in [('away',4,'T'), ('home',3,'B')]:
        names = [side+str(i) for i in range(1,10)]
        lineup[idx] = [table([[i, 'position', n, '999'] for i,n in enumerate(names,1)])]
        box['arrHitter'].append({'table1':table([[i,'position',n] for i,n in enumerate(names,1)])})
        box['arrPitcher'].append({'table':table([[side+'starter','선발','승'],[side+'other','구원','패']], ['선수명','등판','결과'])})
        game[prefix+'_PIT_P_ID'] = '12345' if side == 'away' else '54321'
        game[prefix+'_PIT_P_NM'] = side+'starter'
    return lineup,box,game


class IdentityTest(unittest.TestCase):
    def test_explicit_start_and_crosscheck(self):
        lineup, box, game = sample()
        parsed = parse_roster(lineup,box,game)
        self.assertEqual(parsed['home']['starter']['player_id'], '54321')
        self.assertEqual(len(parsed['away']['ordered_hitters']),9)
        altered = copy.deepcopy(box)
        altered['arrHitter'][0]['table1'] = table([[i,'position','wrong' if i == 1 else 'away'+str(i)] for i in range(1,10)])
        with self.assertRaisesRegex(ValueError, 'lineup_actual_boxscore_mismatch'):
            parse_roster(lineup,altered,game)

    def test_winning_pitcher_is_not_starter(self):
        lineup, box, game = sample()
        box['arrPitcher'][0]['table'] = table([['awaystarter','구원','승'],['awayother','구원','패']], ['선수명','등판','결과'])
        with self.assertRaisesRegex(ValueError, 'actual_starter_not_unique'):
            parse_roster(lineup,box,game)

    def test_documented_rename_requires_identity_evidence(self):
        lineup, box, game = sample()
        game['B_PIT_P_ID'] = '68419'
        game['B_PIT_P_NM'] = '최채흥'
        box['arrPitcher'][1]['table'] = table([['최지명','선발','']], ['선수명','등판','결과'])
        with self.assertRaisesRegex(ValueError, 'schedule_actual_starter_identity_mismatch'):
            parse_roster(lineup,box,game)
        proof = {('home','68419','최채흥','최지명'):{'source':'verified'}}
        self.assertEqual(parse_roster(lineup,box,game,proof)['home']['starter']['name_alias_evidence'], {'source':'verified'})

    def test_stats_poison_does_not_change_candidate_identity(self):
        p1 = [{'slot':1,'name':'Name'}]
        p2 = copy.deepcopy(p1)
        rows = [{'order':1,'name':'Name','code':'12345','hit':0,'bf':999}]
        poisoned = [{**rows[0], 'hit':99999, 'bf':-1}]
        candidate_ids(p1,rows)
        candidate_ids(p2,poisoned)
        self.assertEqual(p1,p2)
        self.assertEqual(p1[0]['_candidate_ids'],['12345'])

    def test_profile_name_exact(self):
        self.assertEqual(profile_name('<span id="a_playerProfile_lblName">홍길동</span>'.encode()), '홍길동')
        with self.assertRaises(ValueError):
            profile_name(b'<span>no name</span>')

    def test_date_only_fixture_and_doubleheader_are_not_guessed(self):
        row = {'fixture_id':'score:2024-09-30:A:B', 'kickoff_at':None, 'home_id':'A', 'away_id':'B'}
        self.assertEqual(game_day(row), '20240930')
        games = [{'HOME_NM':'A','AWAY_NM':'B'}, {'HOME_NM':'B','AWAY_NM':'A'}]
        with patch('research_kbo_actual_2024_capture.fetch', return_value=({'code':'100','game':games}, {})):
            with self.assertRaisesRegex(ValueError, 'fallback_game_ambiguous_doubleheader'):
                schedule(row)

    def test_official_game_id_date_prefix_need_not_be_calendar_date(self):
        row = {'fixture_id':'33331005KTLG02024','kickoff_at':'2024-10-05T14:00:00+09:00', 'home_id':'LG', 'away_id':'KT'}
        game = {'HOME_NM':'LG','AWAY_NM':'KT','HOME_ID':'LG','AWAY_ID':'KT','HEADER_NO':0,
                'G_DT':'20241005','G_ID':'33331005KTLG0','SEASON_ID':2024,'LE_ID':1}
        with patch('research_kbo_actual_2024_capture.fetch', return_value=({'code':'100','game':[game]}, {})):
            self.assertEqual(schedule(row, team_codes=('KT','LG'), header='0')[0]['G_ID'],game['G_ID'])

    def test_schedule_can_include_resumed_earlier_game(self):
        row = {'fixture_id':'77771023SSHT02024','kickoff_at':'2024-10-23T18:30:00+09:00', 'home_id':'KIA', 'away_id':'삼성'}
        current = {'HOME_NM':'KIA','AWAY_NM':'삼성','HOME_ID':'HT','AWAY_ID':'SS','HEADER_NO':0,
                   'G_DT':'20241023','G_ID':'20241023SSHT0','SEASON_ID':2024,'LE_ID':1}
        resumed = {**current,'G_DT':'20241021','G_ID':'20241021SSHT0'}
        with patch('research_kbo_actual_2024_capture.fetch', return_value=({'code':'100','game':[resumed,current]}, {})):
            self.assertEqual(schedule(row, team_codes=('SS','HT'), header='0')[0]['G_ID'],current['G_ID'])


if __name__ == '__main__':
    unittest.main()
