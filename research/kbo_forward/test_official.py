import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from research.kbo_forward.official import OfficialClient, SourceError, SCHEDULE, parse_lineup


def lineup(game, confirmed=True):
    if not confirmed:
        return json.dumps([[{'LINEUP_CK': False}], [], [], [], []]).encode()
    def meta(team):
        return [{'G_ID': game['G_ID'], 'T_ID': team, 'LE_ID': game['LE_ID'], 'SR_ID': game['SR_ID'], 'SEASON_ID': game['SEASON_ID']}]
    def table():
        return [json.dumps({'rows': [{'row': [{'Text': str(i)}, {'Text': 'P'}, {'Text': 'name'+str(i)}]} for i in range(1, 10)]})]
    return json.dumps([[{'LINEUP_CK': True}], meta(game['HOME_ID']), meta(game['AWAY_ID']), table(), table()]).encode()


class Response:
    status_code = 200
    def __init__(self, payload): self.content = json.dumps(payload).encode()


class Session:
    def __init__(self, payload): self.headers = {}; self.payload = payload; self.calls = 0
    def post(self, *a, **kw): self.calls += 1; return Response(self.payload)


class OfficialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        start = datetime.now(timezone.utc) + timedelta(days=1)
        local = start.astimezone(timezone(timedelta(hours=9)))
        self.g = {'G_ID': 'game', 'G_DT': local.strftime('%Y%m%d'), 'G_TM': local.strftime('%H:%M'), 'HOME_ID': 'HH', 'AWAY_ID': 'AA', 'LE_ID': '1', 'SR_ID': '1', 'SEASON_ID': str(local.year), 'CANCEL_SC_ID': '0', 'GAME_STATE_SC': '1'}

    def test_lineup_false_never_uses_stale_rows(self):
        self.assertIsNone(parse_lineup(lineup(self.g, False), self.g))

    def test_lineup_validates_identity_and_order(self):
        self.assertEqual(len(parse_lineup(lineup(self.g), self.g)['home']), 9)
        other = dict(self.g, HOME_ID='XX')
        with self.assertRaises(SourceError): parse_lineup(lineup(self.g), other)
        obj = json.loads(lineup(self.g)); table = json.loads(obj[3][0]); table['rows'].pop(); obj[3][0] = json.dumps(table)
        with self.assertRaises(SourceError): parse_lineup(json.dumps(obj).encode(), self.g)

    def test_cache_evidence_and_corruption(self):
        session = Session({'code': '100', 'game': [self.g]})
        c = OfficialClient(self.tmp.name, session=session)
        with patch('research.kbo_forward.official.time.sleep'):
            a = c.schedule([datetime.strptime(self.g['G_DT'], '%Y%m%d').date()])
            c.schedule([datetime.strptime(self.g['G_DT'], '%Y%m%d').date()])
        self.assertEqual(session.calls, 1)
        (Path(self.tmp.name) / a['receipts'][0]['body_file']).write_bytes(b'changed')
        with self.assertRaisesRegex(SourceError, 'sha_mismatch'):
            c.fetch(SCHEDULE, a['receipts'][0]['params'])

    def test_late_capture_is_rejected_without_lineup_request(self):
        self.g['G_DT'] = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).strftime('%Y%m%d')
        self.g['G_TM'] = (datetime.now(timezone.utc) + timedelta(minutes=10)).astimezone(timezone(timedelta(hours=9))).strftime('%H:%M')
        s = Session({}); c = OfficialClient(self.tmp.name, session=s)
        x = c.snapshot({'source': self.g, 'receipt': {'finished_at': datetime.now(timezone.utc).isoformat()}})
        self.assertEqual(x['missing'], ['capture_after_t_minus_30']); self.assertEqual(s.calls, 0)

    def test_result_only_final_with_official_score_fields(self):
        game = dict(self.g, GAME_STATE_SC='3', GAME_RESULT_CK=1, B_SCORE_CN='6', T_SCORE_CN='1')
        s = Session({'code':'100', 'game':[game]}); c = OfficialClient(self.tmp.name, session=s)
        with patch('research.kbo_forward.official.time.sleep'):
            result = c.result({'source': self.g})
        self.assertEqual((result['home_score'],result['away_score']),(6,1))
        game.pop('B_SCORE_CN'); c = OfficialClient(Path(self.tmp.name)/'other', session=Session({'code':'100', 'game':[game]}))
        with patch('research.kbo_forward.official.time.sleep'):
            self.assertFalse(c.result({'source':self.g})['verified'])

    def test_limits(self):
        with self.assertRaises(ValueError): OfficialClient(self.tmp.name, max_requests=81)
        with self.assertRaises(ValueError): OfficialClient(self.tmp.name, max_lineup_requests=6)

if __name__ == '__main__': unittest.main()
