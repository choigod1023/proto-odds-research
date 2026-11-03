import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from research.kbo_forward.official import OfficialClient, SourceError, SCHEDULE, SEARCH, parse_lineup, parse_player_search


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
        s = Session({'code':'100', 'game':[self.g]}); c = OfficialClient(self.tmp.name, session=s)
        with patch('research.kbo_forward.official.time.sleep'):
            game = c.schedule([datetime.strptime(self.g['G_DT'], '%Y%m%d').date()])['games'][0]
            x = c.snapshot(game)
        self.assertEqual(x['missing'], ['capture_after_t_minus_30']); self.assertEqual(s.calls, 1)

    def test_result_only_final_with_official_score_fields(self):
        game = dict(self.g, GAME_STATE_SC='3', GAME_RESULT_CK=1, B_SCORE_CN='6', T_SCORE_CN='1')
        s = Session({'code':'100', 'game':[game]}); c = OfficialClient(self.tmp.name, session=s)
        with patch('research.kbo_forward.official.time.sleep'):
            result = c.result({'source': self.g})
        self.assertEqual((result['home_score'],result['away_score']),(6,1))
        game.pop('B_SCORE_CN'); c = OfficialClient(Path(self.tmp.name)/'other', session=Session({'code':'100', 'game':[game]}))
        with patch('research.kbo_forward.official.time.sleep'):
            self.assertFalse(c.result({'source':self.g})['verified'])

    def test_search_unique_active_team_and_completeness(self):
        raw = ('<div class="inquiry"><p class="title"><span class="point">2</span></p><table class="tEx"><tbody>'
               '<tr><td>1</td><td><a href="/Record/Player/HitterDetail/Basic.aspx?playerId=123">Kim</a></td><td>SS</td></tr>'
               '<tr><td>#</td><td><a href="/Record/Retire/Hitter.aspx?playerId=456">Kim</a></td><td>SS</td></tr>'
               '</tbody></table></div>').encode()
        self.assertEqual(parse_player_search(raw, 'Kim', 'SS'), '123')
        with self.assertRaisesRegex(SourceError, 'incomplete'):
            parse_player_search(raw.replace(b'>2</span>', b'>3</span>'), 'Kim', 'SS')
        with self.assertRaisesRegex(SourceError, 'ambiguous'):
            parse_player_search(raw.replace(b'Retire/Hitter.aspx', b'Player/HitterDetail/Basic.aspx'), 'Kim', 'SS')

    def test_delta_rejects_same_day_before_fetch(self):
        s = Session({}); c = OfficialClient(self.tmp.name, session=s)
        with self.assertRaisesRegex(SourceError, 'strictly_before'):
            c.history_delta(['2026-11-03'], ['SS'], '2026-11-03')
        self.assertEqual(s.calls, 0)

    def test_verified_announcement_and_history_hook(self):
        import research.kbo_forward.official as off
        self.g.update(HOME_NM='HH', AWAY_NM='AA', START_PIT_CK=1, B_PIT_P_ID='11', B_PIT_P_NM='HomeStarter', T_PIT_P_ID='22', T_PIT_P_NM='AwayStarter')
        obj = json.loads(lineup(self.g))
        for ti, prefix in ((3, 'H'), (4, 'A')):
            table = json.loads(obj[ti][0])
            for i, row in enumerate(table['rows']): row['row'][2]['Text'] = prefix + str(i+1)
            obj[ti][0] = json.dumps(table)
        class MultiSession:
            def __init__(self, game, body): self.headers = {}; self.game=game; self.body=body
            def post(self, url, **kw): return Response({'code':'100','game':[self.game]} if url == SCHEDULE else self.body)
            def get(self, url, **kw):
                name = kw['params']['searchWord']; team = 'HH' if name[0]=='H' else 'AA'
                pid = str((100 if team=='HH' else 200) + int(name[1:]))
                html = '<div class="inquiry"><p class="title"><span class="point">1</span></p><table class="tEx"><tbody><tr><td>1</td><td><a href="/Record/Player/HitterDetail/Basic.aspx?playerId='+pid+'">'+name+'</a></td><td>'+team+'</td></tr></tbody></table></div>'
                r=Response({}); r.content=html.encode(); return r
        c=OfficialClient(self.tmp.name,session=MultiSession(self.g,obj))
        def build(event,roster,history,received):
            names=('home_team_strength','away_team_strength','home_rest_days','away_rest_days','park_factor','home_lineup_strength','away_lineup_strength','home_starter_strength','away_starter_strength','home_bullpen_load','away_bullpen_load')
            return {'roster':roster,'fixture_id':event['official_game_id']+str(event['season']),'home_id':event['home_id'],'away_id':event['away_id'],'season':event['season'],'event_date':event['kickoff_at'][:10], 'raw_features':{x:{'value':None,'source_fixture_ids':[]} for x in names},'features':{x:{'value':None} for x in names}}
        with patch.object(off.time,'sleep'):
            game=c.schedule([datetime.strptime(self.g['G_DT'],'%Y%m%d').date()])['games'][0]
            seed={'through_date':(datetime.strptime(self.g['G_DT'],'%Y%m%d').date()-timedelta(days=1)).isoformat(), 'received_at':datetime.now(timezone.utc).isoformat(), 'games':[]}
            hist={'seed':seed,'source_receipts':[game['receipt']],'games':[]}
            x=c.snapshot(game, history=hist, build_row=build, verify_history=lambda *x: True)
            y=c.snapshot(game, history=hist, build_row=build, verify_history=lambda *x: False)
        self.assertTrue(x['source_verified']); self.assertEqual(len(x['row']['roster']['home']['hitters']),9)
        import importlib.util, sys, hashlib
        path=Path(__file__).resolve().parents[2]/'experiments/kbo-m0-followup-20261103/forward.py'
        spec=importlib.util.spec_from_file_location('test_official_forward',path); f=importlib.util.module_from_spec(spec); sys.modules[spec.name]=f; spec.loader.exec_module(f)
        payload=x['payload']; body={'odds':'fixture'}
        payload['evidence']['odds']={'uri':'https://example.org/odds','received_at':datetime.now(timezone.utc).isoformat(),'body':body,'sha256':hashlib.sha256(f.canonical(body)).hexdigest()}
        payload['market']={'kind':'full_game_2way','settlement_rule':'draw_void_refund','market_id':'test','odds':{'home':'1.8','away':'1.9'}}
        f.validate_snapshot(payload,datetime.now(timezone.utc))
        self.assertFalse(y['source_verified']); self.assertIn('history_evidence_not_verified',y['missing'])

    def test_history_requires_every_day_after_seed(self):
        target=datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).date()+timedelta(days=2)
        d1=target-timedelta(days=2); d2=target-timedelta(days=1)
        s=Session({'code':'100','game':[]}); c=OfficialClient(self.tmp.name,session=s)
        event={'kickoff_at':target.isoformat()+'T18:30:00+09:00','home_id':'HH','away_id':'AA'}
        history={'seed':{'through_date':(target-timedelta(days=3)).isoformat(),'received_at':datetime.now(timezone.utc).isoformat(),'games':[]},'games':[]}
        with patch('research.kbo_forward.official.time.sleep'):
            a=c.schedule([d1])['receipts'][0]
            history['source_receipts']=[a]
            self.assertFalse(c.verify_history_coverage(event,history))
            history['source_receipts'].append(c.schedule([d2])['receipts'][0])
            self.assertTrue(c.verify_history_coverage(event,history))

    def test_result_cancellation_requires_explicit_official_label(self):
        game=dict(self.g, CANCEL_SC_ID='1', CANCEL_SC_NM='우천취소')
        c=OfficialClient(self.tmp.name,session=Session({'code':'100','game':[game]}))
        with patch('research.kbo_forward.official.time.sleep'):
            result=c.result({'source':self.g})
        self.assertEqual(result['status'],'cancelled')
        game['CANCEL_SC_NM']='unknown'
        c=OfficialClient(Path(self.tmp.name)/'second',session=Session({'code':'100','game':[game]}))
        with patch('research.kbo_forward.official.time.sleep'):
            self.assertFalse(c.result({'source':self.g})['verified'])

    def test_limits(self):
        with self.assertRaises(ValueError): OfficialClient(self.tmp.name, max_requests=81)
        with self.assertRaises(ValueError): OfficialClient(self.tmp.name, max_lineup_requests=6)

if __name__ == '__main__': unittest.main()
