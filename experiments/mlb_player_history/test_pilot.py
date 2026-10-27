import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pilot


class Response(io.BytesIO):
    status = 200
    headers = {}


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = pilot.Store(Path(self.temp.name) / 'test.sqlite')

    def tearDown(self):
        self.store.conn.close()
        self.temp.cleanup()

    def fetch(self, body=b'{}', status=200, **kwargs):
        response = Response(body)
        response.status = status
        with patch('pilot.time.sleep'), patch('pilot.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = response
            result = self.store.fetch('stats', **kwargs)
            return result

    def test_receipt_hashes_cache_and_immutability(self):
        self.assertEqual(self.fetch(a=1)[1], {})
        with patch('pilot.urllib.request.build_opener') as opener:
            self.assertEqual(self.store.fetch('stats', a=1)[0], 1)
            opener.assert_not_called()
        url, uh, body, bh, received = self.store.conn.execute(
            'SELECT url,url_sha256,body,body_sha256,received_at FROM raw').fetchone()
        self.assertEqual(uh, pilot.digest(url.encode()))
        self.assertEqual(bh, pilot.digest(body))
        self.assertTrue(received.endswith('+00:00'))
        for sql in ('DELETE FROM raw', "UPDATE raw SET body='x'"):
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'immutable'):
                self.store.conn.execute(sql)

    def test_403_and_429_stop_all_subsequent_network(self):
        for status in (403, 429):
            with self.subTest(status=status):
                if status == 429:
                    self.store.conn.close()
                    self.store = pilot.Store(Path(self.temp.name) / '429.sqlite')
                with self.assertRaises(RuntimeError):
                    self.fetch(status=status, code=status)
                with patch('pilot.urllib.request.build_opener') as opener:
                    with self.assertRaises(RuntimeError):
                        self.store.fetch('stats', another=status)
                    opener.assert_not_called()

    def test_request_budget_includes_failed_receipts(self):
        with self.assertRaises(RuntimeError):
            self.fetch(status=400)
        with patch('pilot.MAX_REQUESTS', 1), patch('pilot.urllib.request.build_opener') as opener:
            with self.assertRaisesRegex(RuntimeError, 'budget'):
                self.store.fetch('stats', next=1)
            opener.assert_not_called()

    def test_byte_cap_persists_prefix_and_stops(self):
        with patch('pilot.MAX_BYTES', 4):
            with self.assertRaisesRegex(RuntimeError, 'cap'):
                self.fetch(b'123456789')
        self.assertEqual(self.store.conn.execute('SELECT bytes,body FROM raw').fetchone(), (4, b'1234'))
        with patch('pilot.urllib.request.build_opener') as opener:
            with self.assertRaisesRegex(RuntimeError, 'failure'):
                self.store.fetch('stats', next=1)
            opener.assert_not_called()

    def test_transport_error_is_durable_and_stops(self):
        with patch('pilot.time.sleep'), patch('pilot.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect = TimeoutError('test timeout')
            with self.assertRaises(RuntimeError):
                self.store.fetch('stats')
        self.assertIn('TimeoutError', self.store.conn.execute('SELECT error FROM raw').fetchone()[0])

    def test_denominator_reconciliation_never_invents_six_classes(self):
        stat = dict.fromkeys(pilot.FIELDS, 0)
        stat.update(atBats=8, hits=3, homeRuns=1, strikeOuts=2, baseOnBalls=1,
                    hitByPitch=1, plateAppearances=10, intentionalWalks=1)
        result = pilot.normalize(stat, 'hitting')
        self.assertTrue(result['valid'])
        self.assertEqual(result['BB_HBP'], 2)
        self.assertEqual(result['otherhit'], 2)
        self.assertEqual(result['otherout_plus_residualreach'], 3)
        self.assertFalse(result['six_class_verified'])
        stat['battersFaced'] = 10
        self.assertEqual(pilot.normalize(stat, 'pitching')['denominator_name'], 'battersFaced')
        stat['plateAppearances'] = 11
        self.assertFalse(pilot.normalize(stat, 'hitting')['valid'])
        del stat['hitByPitch']
        self.assertEqual(pilot.normalize(stat, 'pitching')['missing'], ['hitByPitch'])

    def test_page_rejects_season_total_or_identity_fallback(self):
        block = dict(type=dict(displayName='byDateRange'), group=dict(displayName='hitting'),
            totalSplits=1, splits=[dict(season='2025', player=dict(id=42), sport=dict(id=1))])
        self.assertEqual(pilot.validate_page(dict(stats=[block]), 'hitting', '2025'), block)
        block['type']['displayName'] = 'season'
        with self.assertRaisesRegex(ValueError, 'fallback'):
            pilot.validate_page(dict(stats=[block]), 'hitting', '2025')
        block['type']['displayName'] = 'byDateRange'
        block['splits'][0]['season'] = '2026'
        with self.assertRaisesRegex(ValueError, 'identity'):
            pilot.validate_page(dict(stats=[block]), 'hitting', '2025')

    def test_bulk_parameters_are_explicit(self):
        params = pilot.bulk_params('pitching', '2025-01-01', '2025-05-31', 1000)
        self.assertEqual(params['stats'], 'byDateRange')
        self.assertEqual(params['endDate'], '2025-05-31')
        self.assertEqual(params['gameType'], 'R')
        self.assertEqual(params['offset'], 1000)
        self.assertEqual(params['playerPool'], 'ALL')


if __name__ == '__main__':
    unittest.main()
