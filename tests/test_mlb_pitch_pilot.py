import copy
import argparse
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from src.mlb_pitch_pilot import Collector, bullpen, create_db, extract, run


def fixture():
    return {'gamePk': 1, 'gameData': {'datetime': {'officialDate': '2025-06-02'},
            'status': {'abstractGameState': 'Final'}, 'teams': {'home': {'id': 10}, 'away': {'id': 20}}},
            'liveData': {'boxscore': {'teams': {'home': {'pitchers': [100, 101],
                          'players': {f'ID{pid}': {'stats': {'pitching': {'numberOfPitches': 1}}} for pid in (100, 101)}}}},
                         'plays': {'allPlays': [
                             {'about': {'isTopInning': True, 'atBatIndex': i},
                              'matchup': {'pitcher': {'id': pitcher}},
                              'playEvents': [{'isPitch': True, 'playId': str(i), 'pitchData': {'startSpeed': 95}},
                                             {'isPitch': False}]} for i, pitcher in enumerate([100, 101])]}}}


class PilotTests(unittest.TestCase):
    def test_counts_duplicates_and_starters(self):
        f = fixture()
        f['liveData']['plays']['allPlays'].append(copy.deepcopy(f['liveData']['plays']['allPlays'][1]))
        g = extract(f)
        self.assertEqual(len(g['pitches']), 2)
        self.assertEqual(g['duplicates'], 1)
        self.assertEqual([p['is_starter'] for p in g['people']], [True, False])
        self.assertEqual(sum(p['pitch_count'] for p in g['people']), 2)

    def test_missing_stays_unknown(self):
        f = fixture()
        f['liveData']['boxscore'] = {}
        play = f['liveData']['plays']['allPlays'][0]
        play['matchup'] = {}
        play['playEvents'][0].pop('pitchData')
        p = extract(f)['pitches'][0]
        self.assertIsNone(p['pitcher_id'])
        self.assertIsNone(p['speed'])
        self.assertIsNone(p['is_starter'])

    def test_same_day_future_and_missing_excluded(self):
        g = extract(fixture())
        schedule = [dict(game_id=1, date='2025-06-02', teams=[10, 20]),
                    dict(game_id=2, date='2025-06-03', teams=[10, 20]),
                    dict(game_id=3, date='2025-06-04', teams=[10, 20])]
        result = bullpen('2025-06-03', 10, [g], schedule, '2025-05-30')
        self.assertTrue(result['coverage_complete'])
        self.assertEqual(result['bullpen_pitches'], 1)
        self.assertIsNone(bullpen('2025-06-04', 10, [g], schedule, '2025-05-30')['bullpen_pitches'])
        self.assertIsNone(bullpen('2025-06-03', 10, [g], schedule, '2025-06-01')['bullpen_pitches'])

    def test_immutable_raw_and_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            db = create_db(Path(directory) / 'private.sqlite')
            db.execute("INSERT INTO raw(received_at,sha256,body) VALUES('now','hash',X'01')")
            for sql in ('DELETE FROM raw', "UPDATE raw SET received_at='past'"):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)
            collector = Collector(db, 1, 1, 1)
            collector.used['schedule'] = 1
            collector.opener = Mock()
            with self.assertRaisesRegex(RuntimeError, 'budget'):
                collector.get('feed', 'https://statsapi.mlb.com')
            collector.opener.open.assert_not_called()
            db.close()

    def assert_incomplete_bullpen(self, feed):
        g = extract(feed)
        schedule = [dict(game_id=1, date='2025-06-02', teams=[10, 20])]
        result = bullpen('2025-06-03', 10, [g], schedule, '2025-05-30')
        self.assertFalse(result['coverage_complete'])
        self.assertIsNone(result['bullpen_pitches'])
        self.assertFalse(g['reconciliation'][0]['complete'])

    def test_truncated_final_cannot_impute_zero_bullpen(self):
        f = fixture()
        f['liveData']['plays']['allPlays'].pop()
        self.assert_incomplete_bullpen(f)

    def test_missing_boxscore_counts_are_unknown(self):
        for pid in (100, 101):
            f = fixture()
            f['liveData']['boxscore']['teams']['home']['players'][f'ID{pid}']['stats']['pitching'] = {}
            self.assert_incomplete_bullpen(f)

    def test_fallback_official_count(self):
        f = fixture()
        f['liveData']['boxscore']['teams']['home']['players']['ID101']['stats']['pitching'] = {'pitchesThrown': 1}
        self.assertTrue(extract(f)['reconciliation'][0]['complete'])

    def test_unknown_pitcher_retained_and_incomplete(self):
        f = fixture()
        f['liveData']['plays']['allPlays'][1]['matchup'] = {}
        self.assert_incomplete_bullpen(f)
        g = extract(f)
        self.assertIsNone(g['pitches'][1]['pitcher_id'])
        self.assertEqual(g['people'][1]['pitch_count'], 1)
        self.assertIsNone(g['people'][1]['pitcher_id'])

    def test_http_denial_no_retry(self):
        for status in (403, 429):
            with tempfile.TemporaryDirectory() as directory:
                db = create_db(Path(directory) / 'private.sqlite')
                collector = Collector(db, 1, 30, 31)
                collector.opener = Mock()
                collector.opener.open.side_effect = HTTPError('https://statsapi.mlb.com', status, 'denied', {}, io.BytesIO(b'denied'))
                with self.assertRaisesRegex(RuntimeError, str(status)):
                    collector.get('feed', 'https://statsapi.mlb.com')
                self.assertEqual(collector.opener.open.call_count, 1)
                self.assertEqual(db.execute('SELECT status,body FROM raw').fetchone(), (status, b'denied'))
                db.close()

    def test_first_error_keeps_partial_output(self):
        game = lambda pk: {'gamePk': pk, 'status': {'abstractGameState': 'Final'},
                           'teams': {'home': {'team': {'id': 10}}, 'away': {'team': {'id': 20}}}}
        payload = {'dates': [{'date': '2025-06-02', 'games': [game(1), game(2), game(3)]}]}
        with tempfile.TemporaryDirectory() as directory:
            args = argparse.Namespace(start='2025-06-01', end='2025-06-14', max_games=30,
                                      schedule_budget=1, feed_budget=30, request_budget=31,
                                      output=str(Path(directory) / 'new'))
            with patch.object(Collector, 'get', side_effect=[(1, payload), (2, fixture()), RuntimeError('HTTP 429')]) as get:
                summary = run(args)
            self.assertEqual(get.call_count, 3)
            self.assertEqual(summary['collected_games'], 1)
            self.assertTrue(summary['incomplete'])
            self.assertEqual(len(summary['errors']), 1)
            with self.assertRaises(FileExistsError):
                run(args)


if __name__ == '__main__':
    unittest.main()
