import copy
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest

from gate import CATEGORIES, LABELS, evaluate, extract, load, open_source, run, sha256, summarize
from reconcile import reconcile


def feed(labels=('strikeout',), day='2025-06-01', game=1):
    return {'gamePk': game, 'gameData': {'datetime': {'officialDate': day},
            'status': {'abstractGameState': 'Final'}}, 'liveData': {'plays': {'allPlays': [
                {'about': {'atBatIndex': i, 'isComplete': True},
                 'result': {'type': 'atBat', 'eventType': label},
                 'matchup': {'batter': {'id': 10}, 'pitcher': {'id': 20}}}
                for i, label in enumerate(labels)]}}}


def cache(path, feeds):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript('CREATE TABLE raw(id INTEGER PRIMARY KEY,sha256 TEXT,body BLOB); '
                          'CREATE TABLE games(game_id INTEGER,official_date TEXT,final INTEGER,raw_id INTEGER);')
        for i, f in enumerate(feeds):
            body = json.dumps(f).encode()
            conn.execute('INSERT INTO raw VALUES(?,?,?)', (i, hashlib.sha256(body).hexdigest(), body))
            conn.execute('INSERT INTO games VALUES(?,?,1,?)',
                         (f['gamePk'], f['gameData']['datetime']['officialDate'], i))


class GateTests(unittest.TestCase):
    def test_mapping_and_exclusions(self):
        rows, audit = extract(feed(tuple(LABELS) + ('caught_stealing_2b', 'pickoff_2b',
            'new_unknown_label')))
        self.assertEqual([r['outcome'] for r in rows], list(LABELS.values()))
        self.assertEqual(audit['accepted'], len(LABELS))
        self.assertEqual(audit['non_pa:caught_stealing_2b'], 1)
        self.assertEqual(audit['non_pa:pickoff_2b'], 1)
        self.assertEqual(sum(v for k, v in audit.items() if k.startswith('unmapped:')), 1)
        self.assertEqual(len(CATEGORIES), 6)
        for label in ('field_error', 'catcher_interf', 'fielders_choice'):
            self.assertEqual(LABELS[label], 'residualreach')

    def test_incomplete_nonatbat_missing_identity_and_duplicate(self):
        f = feed(('strikeout',) * 4)
        ps = f['liveData']['plays']['allPlays']
        ps[0]['about']['isComplete'] = False
        ps[1]['result']['type'] = 'action'
        ps[2]['matchup'] = {}
        rows, audit = extract(f)
        self.assertEqual(len(rows), 1)
        self.assertEqual(audit['incomplete'], 1)
        self.assertEqual(audit['missing_identity'], 1)
        self.assertEqual(audit['non_atbat:strikeout'], 1)
        ps.append(copy.deepcopy(ps[3]))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            extract(f)

    def test_past_days_only_and_exact_shrinkage(self):
        rows = extract(feed(('strikeout',) * 2))[0]
        rows += extract(feed(('home_run',), '2025-06-02', 2))[0]
        rows += extract(feed(('walk',), '2025-06-02', 3))[0]
        pred = evaluate(rows)
        today = [p for p in pred if p['day'] == '2025-06-02']
        self.assertTrue(all(p['prior_pa'] == 2 and p['batter_history'] == 2 for p in today))
        candidate = next(p for p in today if p['model'] == 'batter_pitcher')
        self.assertAlmostEqual(candidate['probabilities'][0], (2 + 100 * (3 / 8)) / 102)
        self.assertAlmostEqual(sum(candidate['probabilities']), 1)
        changed = copy.deepcopy(rows)
        changed[2]['outcome'] = 'otherhit'
        changed.append(dict(rows[-1], day='2025-06-03', game=4))
        revised = evaluate(changed)
        self.assertEqual([p['probabilities'] for p in pred],
                         [p['probabilities'] for p in revised[:len(pred)]])
        self.assertEqual(pred, evaluate(list(reversed(rows))))

    def test_cold_start_and_unseen_players(self):
        rows = extract(feed())[0] + extract(feed(('single',), '2025-06-02', 2))[0]
        rows[1]['batter'], rows[1]['pitcher'] = 99, 88
        pred = evaluate(rows)
        self.assertEqual(pred[0]['probabilities'], [1 / 6] * 6)
        self.assertAlmostEqual(pred[0]['log_loss'], math.log(6))
        self.assertAlmostEqual(pred[0]['brier'], 5 / 6)
        for left, right in zip(pred[2]['probabilities'], pred[3]['probabilities']):
            self.assertAlmostEqual(left, right)
        scores = [s for s in summarize(pred) if s['day'] == 'ALL_EVALUATION']
        self.assertTrue(all(s['n'] == 1 for s in scores))

    def test_readonly_integrity_and_private_run(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / 'source.sqlite', Path(temp) / 'private'
            cache(source, [feed(), feed(('home_run',), '2025-06-02', 2)])
            before = sha256(source)
            conn = open_source(source)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute('DELETE FROM games')
            finally:
                conn.close()
            meta = run(source, output)
            self.assertEqual(meta['games'], 2)
            self.assertEqual(before, sha256(source))
            with closing(sqlite3.connect(output / 'results.sqlite')) as result:
                self.assertEqual(result.execute('SELECT count(*) FROM predictions').fetchone()[0], 4)
            self.assertIn('NOT preannounced', (output / 'report.md').read_text())
            with self.assertRaises(FileExistsError):
                run(source, output)
            with closing(sqlite3.connect(source)) as conn, conn:
                conn.execute("UPDATE raw SET sha256='bad'")
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                load(source)

    def test_nonfinal_and_identity_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.sqlite'
            cache(source, [feed()])
            with closing(sqlite3.connect(source)) as conn, conn:
                conn.execute("UPDATE games SET official_date='2025-06-02'")
            with self.assertRaisesRegex(ValueError, 'identity/date'):
                load(source)
        f = feed()
        f['gameData']['status']['abstractGameState'] = 'Live'
        self.assertEqual(extract(f)[1]['nonfinal_game'], 1)

    def test_residuals_and_actual_team_boxscore(self):
        f = feed(('field_error', 'catcher_interf', 'fielders_choice'))
        for p in f['liveData']['plays']['allPlays']:
            p['about']['halfInning'] = 'top'
        f['liveData']['boxscore'] = {'teams': {'away': {
            'teamStats': {'batting': {'plateAppearances': 3}},
            'players': {'ID10': {'person': {'id': 10}, 'stats': {'batting': {'plateAppearances': 2}}},
                        'ID11': {'person': {'id': 11}, 'stats': {'batting': {'plateAppearances': 1}}}}},
            'home': {'teamStats': {'batting': {'plateAppearances': 0}}, 'players': {}}}}
        rows = extract(f)[0]
        result = reconcile(f, rows)
        self.assertEqual(result[0]['delta'], 0)
        self.assertEqual([r['delta'] for r in result[0]['player_differences']], [1, -1])
        self.assertEqual(len(result[0]['evidence']), 3)
        f['liveData']['boxscore']['teams']['away']['teamStats']['batting']['plateAppearances'] = 4
        self.assertEqual(reconcile(f, rows)[0]['delta'], -1)
        self.assertEqual(len(rows), 3)  # Never invent a PA to match a boxscore.
        del f['liveData']['boxscore']
        self.assertEqual(reconcile(f, rows)[0]['status'], 'missing')
        f['liveData']['plays']['allPlays'][0]['about'].pop('halfInning')
        self.assertEqual(reconcile(f, rows)[-1]['status'], 'unattributed')


if __name__ == '__main__':
    unittest.main()
