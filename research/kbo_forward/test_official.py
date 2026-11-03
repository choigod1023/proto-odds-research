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
    def iter_content(self, chunk_size): yield self.content
    def close(self): pass


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
        with self.assertRaisesRegex(SourceError, 'ambiguous'):
            parse_player_search(raw, 'Kim', 'SS')
        raw=raw.replace(b'Retire/Hitter.aspx', b'Retire/Pitcher.aspx')
        self.assertEqual(parse_player_search(raw, 'Kim', 'SS'), '123')
        with self.assertRaisesRegex(SourceError, 'incomplete'):
            parse_player_search(raw.replace(b'>2</span>', b'>3</span>'), 'Kim', 'SS')
        with self.assertRaisesRegex(SourceError, 'ambiguous'):
            parse_player_search(raw.replace(b'Retire/Pitcher.aspx', b'Player/HitterDetail/Basic.aspx'), 'Kim', 'SS')

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
            return {'roster':roster,'fixture_id':event['official_game_id']+str(event['season']),'home_id':event['home_id'],'away_id':event['away_id'],'season':event['season'],'event_date':event['kickoff_at'][:10], 'raw_features':{x:{'value':None,'source_fixture_ids':[]} for x in names},'features':{x:{'value':None} for x in names}, 'history_coverage':{s:{'status':'complete'} for s in ('home','away')}}
        with patch.object(off.time,'sleep'):
            game=c.schedule([datetime.strptime(self.g['G_DT'],'%Y%m%d').date()])['games'][0]
            seed={'through_date':(datetime.strptime(self.g['G_DT'],'%Y%m%d').date()-timedelta(days=1)).isoformat(), 'received_at':datetime.now(timezone.utc).isoformat(), 'games':[]}
            event={'home_id':self.g['HOME_ID'],'away_id':self.g['AWAY_ID'],'kickoff_at':datetime.strptime(self.g['G_DT']+' '+self.g['G_TM'],'%Y%m%d %H:%M').replace(tzinfo=timezone(timedelta(hours=9))).isoformat()}
            hist=c.derive_history(event,seed,[game['receipt']])
            with patch.object(off,'frozen_seed',return_value=seed):
                x=c.snapshot(game, history=hist, build_row=build)
                bad=dict(hist, schedules={})
                y=c.snapshot(game, history=bad, build_row=build)
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

    def test_completed_box_is_rebuilt_and_tampering_fails(self):
        import copy
        import research.kbo_forward.official as off
        target=datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=9))).date()+timedelta(days=1)
        day=target-timedelta(days=1); game=dict(self.g,G_DT=day.strftime('%Y%m%d'),G_ID=day.strftime('%Y%m%d')+'AAHH0',GAME_STATE_SC='3',GAME_RESULT_CK=1,B_SCORE_CN='6',T_SCORE_CN='1',HOME_NM='Home',AWAY_NM='Away')
        def table(headers,rows): return {'headers':[{'row':[{'Text':x} for x in headers]}],'rows':[{'row':[{'Text':x} for x in row]} for row in rows]}
        bh=['slot','pos','선수명','타수','안타']; ph=['선수명','등판','이닝','피안타','홈런','삼진','실점','자책']
        box={'tables':[{},table(bh,[['1','P','101','4','1']]),table(bh,[['1','P','201','5','2']]),table(ph,[['102','선발','5 2/3','2','0','3','2','2'],['103','교체','1/3','0','0','1','0','0']]),table(ph,[['202','선발','9','1','0','5','1','1']])]}
        class Multi:
            def __init__(self): self.headers={}
            def post(self,url,**kw): return Response({'code':'100','game':[game]} if url==SCHEDULE else box)
        c=OfficialClient(self.tmp.name,session=Multi())
        with patch.object(off.time,'sleep'):
            item=c.schedule([day])['games'][0]
            _,rec=c.fetch(off.BOX,{'leId':game['LE_ID'],'srId':game['SR_ID'],'seasonId':game['SEASON_ID'],'gameId':game['G_ID']})
        seed={'through_date':(day-timedelta(days=1)).isoformat(),'team_through':{'HH':(day-timedelta(days=1)).isoformat(),'AA':(day-timedelta(days=1)).isoformat()},'received_at':datetime.now(timezone.utc).isoformat(),'games':[]}
        event={'home_id':'HH','away_id':'AA','kickoff_at':target.isoformat()+'T18:30:00+09:00'}
        history=c.derive_history(event,seed,[item['receipt'],rec])
        with patch.object(off,'frozen_seed',return_value=seed):
            self.assertTrue(c.verify_history(event,history)); self.assertTrue(c.verify_history_coverage(event,history))
            no_games=copy.deepcopy(history); no_games['games']=[]
            self.assertFalse(c.verify_history_coverage(event,no_games))
            for mutate in (lambda h:h['games'][0]['teams']['HH'].__setitem__('runs_for',99),lambda h:h['games'][0].__setitem__('fixture_id','other'),lambda h:h['schedules']['HH']['events'][0].__setitem__('status','unknown'),lambda h:h['source_receipts'][1].__setitem__('sha256','0'*64),lambda h:h['games'][0]['teams'].__setitem__('wrong',{})):
                wrong=copy.deepcopy(history);mutate(wrong);self.assertFalse(c.verify_history(event,wrong))
        self.assertEqual(history['games'][0]['pitchers']['102']['outs'],17)
        self.assertIsNone(history['games'][0]['batters']['101']['bb'])
        # A rival's unresolved identity is irrelevant to the requested team;
        # its official score and relief innings still remain in the record.
        box['tables'][1]['rows'][0]['row'][2]['Text']='Unknown rival'
        for r in box['tables'][3]['rows']: r['row'][0]['Text']='Unknown rival pitcher'
        c2=OfficialClient(Path(self.tmp.name)/'selective',session=Multi())
        with patch.object(off.time,'sleep'):
            item2=c2.schedule([day])['games'][0]
            _,rec2=c2.fetch(off.BOX,{'leId':game['LE_ID'],'srId':game['SR_ID'],'seasonId':game['SEASON_ID'],'gameId':game['G_ID']})
            selected=c2.normalize_box(item2,rec2,required_team_ids=['HH'])
        self.assertEqual(selected['covered_player_teams'],['HH'])
        self.assertEqual(selected['teams']['AA']['relief']['outs'],1)
        self.assertEqual(set(selected['batters']),{'201'})
        with patch.object(c2,'fetch',side_effect=SourceError('identity_unavailable')):
            with self.assertRaisesRegex(SourceError,'identity_unavailable'):
                c2.normalize_box(item2,rec2,required_team_ids=['AA'])

    def test_historical_final_schedule_cache_and_unfinished_refresh(self):
        import research.kbo_forward.official as off
        current=datetime.now(timezone.utc)
        day=current.astimezone(timezone(timedelta(hours=9))).date()-timedelta(days=2)
        game=dict(self.g,G_DT=day.strftime('%Y%m%d'),GAME_STATE_SC='3',GAME_RESULT_CK='1')
        session=Session({'code':'100','game':[game]}); c=OfficialClient(self.tmp.name,session=session)
        with patch.object(off.time,'sleep'),patch.object(off,'now_utc',return_value=current):
            c.schedule([day])
        with patch.object(off.time,'sleep'),patch.object(off,'now_utc',return_value=current+timedelta(minutes=10)):
            c.schedule([day])
        self.assertEqual(session.calls,1)
        game['GAME_STATE_SC']='1';session=Session({'code':'100','game':[game]});c=OfficialClient(Path(self.tmp.name)/'unfinished',session=session)
        with patch.object(off.time,'sleep'),patch.object(off,'now_utc',return_value=current):
            c.schedule([day])
        with patch.object(off.time,'sleep'),patch.object(off,'now_utc',return_value=current+timedelta(minutes=10)):
            c.schedule([day])
        self.assertEqual(session.calls,2)

    def test_403_backoff_expires_and_large_response_stops(self):
        import research.kbo_forward.official as off
        class Stateful:
            def __init__(self): self.headers={}; self.calls=0
            def post(self,*args,**kwargs):
                self.calls+=1; result=Response({'ok':1}); result.status_code=403 if self.calls==1 else 200; return result
        session=Stateful(); c=OfficialClient(self.tmp.name,session=session)
        with patch.object(off.time,'sleep'):
            with self.assertRaisesRegex(SourceError,'http_403'): c.fetch(SCHEDULE,{'date':'x'})
            with self.assertRaisesRegex(SourceError,'backoff_active'): c.fetch(SCHEDULE,{'date':'x'})
            c.records[-1]['finished_at']=(datetime.now(timezone.utc)-timedelta(minutes=16)).isoformat()
            self.assertEqual(c.fetch(SCHEDULE,{'date':'x'})[1]['status'],200)
        class Huge:
            def __init__(self): self.headers={}
            def post(self,*args,**kwargs):
                result=Response({});result.content=b'x'*(4*1024*1024+1);return result
        c=OfficialClient(Path(self.tmp.name)/'huge',session=Huge())
        with patch.object(off.time,'sleep'):
            with self.assertRaisesRegex(SourceError,'response_too_large'): c.fetch(SCHEDULE,{'date':'x'})
        self.assertTrue(c.records[-1]['truncated'])

    def test_retired_candidate_requires_official_earlier_last_season(self):
        import research.kbo_forward.official as off
        search=('<div class="inquiry"><p class="title"><span class="point">2</span></p><table class="tEx"><tbody>'
                '<tr><td>61</td><td><a href="/Record/Player/PitcherDetail/Basic.aspx?playerId=53029">Kim</a></td><td>KT</td></tr>'
                '<tr><td>#</td><td><a href="/Record/Retire/Pitcher.aspx?playerId=63901">Kim</a></td><td>KT</td></tr>'
                '</tbody></table></div>')
        def retired(year):
            row='' if year is None else '<tr><td>'+str(year)+'</td><td>KT</td><td>1</td></tr>'
            return ('<span id="x_ucRetireInfo_lblName">Kim</span><img id="x_ucRetireInfo_imgProgile" src="/person/middle/63901.jpg">'
                    '<table class="tData01"><thead><tr><th>연도</th><th>팀명</th><th>G</th></tr></thead><tbody>'+row+'</tbody></table>')
        class Get:
            def __init__(self,year): self.headers={};self.year=year
            def get(self,url,**kwargs):
                r=Response({});r.content=(search if url==off.SEARCH else retired(self.year)).encode();return r
        with patch.object(off.time,'sleep'):
            client=OfficialClient(self.tmp.name,session=Get(2018))
            pid,receipts=client.resolve_identity('Kim','KT','pitcher',2026)
            self.assertEqual(pid,'53029');self.assertEqual(len(receipts),2)
            self.assertEqual(client.resolve_identity('Kim','KT','pitcher',2026,identity_receipts=receipts)[0],pid)
            for year,reason in ((2026,'retired_candidate_in_game_season'),(None,'retired_last_season_unavailable')):
                other=OfficialClient(Path(self.tmp.name)/str(year),session=Get(year))
                with self.assertRaisesRegex(SourceError,reason):other.resolve_identity('Kim','KT','pitcher',2026)
            with self.assertRaisesRegex(SourceError,'identity_receipt_missing'):
                client.resolve_identity('Kim','KT','pitcher',2026,identity_receipts=receipts[:1])

    def test_limits(self):
        with self.assertRaises(ValueError): OfficialClient(self.tmp.name, max_requests=81)
        with self.assertRaises(ValueError): OfficialClient(self.tmp.name, max_lineup_requests=6)

if __name__ == '__main__': unittest.main()
