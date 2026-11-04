import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('collector', Path(__file__).resolve().parents[1] / 'scripts/collect_soccer_comparison_sources.py')
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)

class Tests(unittest.TestCase):
    def test_retrospective_and_identity(self):
        row = {'kickoff_kst':'2025-02-15T13:00:00+09:00','price_observed_at':None,'gmTs':250020,'matchSeq':63,'league_id':1,'league_home_name':'포항','league_away_name':'대전','response_sha256':'a'*64,'received_at':'2026-11-03T02:19:41+00:00','fixture_id':'kleague:2025:1:1','home_team_id':'K03','away_team_id':'K10','prices':{'home':1.98,'draw':3.1,'away':3.25},'gmId':'G101','official_score':[0,3]}
        r=c.price_record(row)
        self.assertEqual(r['timing_basis'],'retrospective')
        self.assertEqual(r['payload']['source_fixture_id'],'kleague:2025:1:1')
        self.assertFalse(r['payload']['execution_price_verified'])
        row['price_observed_at']='2025-02-14T00:00:00+00:00'
        with self.assertRaises(ValueError): c.price_record(row)
    def test_reject_naive_kickoff(self):
        with self.assertRaises(ValueError): c.price_record({'kickoff_kst':'2025-02-15T13:00:00','price_observed_at':None})


class SeasonTests(unittest.TestCase):
    def test_budget_and_cache_identity(self):
        import tempfile, json, gzip
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); raw=b'{"ok":true}'; params={'year':'2025'}
            (root/'one.body.gz').write_bytes(gzip.compress(raw))
            (root/'one.json').write_text(json.dumps({'url':'https://example.test','params':params,'body_sha256':c.sha(raw)}),encoding='utf8')
            b=c.Budget(0)
            with patch('requests.request',side_effect=AssertionError('unexpected network')):
                self.assertEqual(c.cached_request(root,'one','https://example.test','GET',params,b)[0],raw)
                self.assertEqual(b.used,0)
                with self.assertRaises(ValueError): c.cached_request(root,'one','https://example.test','GET',{'year':'2026'},b)
                with self.assertRaises(RuntimeError): c.cached_request(root,'missing','https://example.test','GET',params,b)
            (root/'one.body.gz').write_bytes(gzip.compress(b'tampered'))
            with self.assertRaises(ValueError): c.cached_request(root,'one','https://example.test','GET',params,c.Budget(0))
        with self.assertRaises(ValueError): c.Budget(61)

    def test_official_page_identity_and_empty_lineup(self):
        html='''<select id="year"><option selected value="2026"></option></select><select id="meetSeq"><option selected value="1"></option></select><select id="gameId"><option selected value="205"></option></select><div class="game-info"><div class="team K01"></div><div class="team K02"></div></div><div class="team-versus"><div class="versus"><p>2026/11/07 14:00</p></div></div>'''
        fixture={'year':2026,'leagueId':1,'gameId':205,'homeTeam':'K01','awayTeam':'K02','gameDate':'2026.11.07'}
        self.assertEqual(c.parse_official_page(html.encode(),fixture),('not_displayed_on_official_page',[]))
        fixture['homeTeam']='K03'
        with self.assertRaises(ValueError): c.parse_official_page(html.encode(),fixture)

    def test_completion_timestamp_only_after_full_body_and_cache_not_upgraded(self):
        import tempfile, json
        from unittest.mock import patch
        events=[]
        class Response:
            status_code=200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def iter_content(self, size):
                events.append('first_chunk'); yield b'hello'
                events.append('body_completed')
        class Clock:
            @classmethod
            def now(cls, tz):
                self.assertEqual(events[-1], 'body_completed')
                from datetime import datetime, timezone
                return datetime(2026,11,4,tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with patch('requests.request', return_value=Response()), patch.object(c,'datetime',Clock), patch.object(c.time,'sleep'):
                raw, meta=c.cached_request(root,'new','https://example.test','GET',{},c.Budget(1))
            self.assertEqual(raw,b'hello')
            self.assertEqual(meta['timestamp_basis'],'http_response_body_completed')
            old_path=root/'new.json'
            old=json.loads(old_path.read_text(encoding='utf8')); del old['timestamp_basis']
            old_path.write_text(json.dumps(old),encoding='utf8')
            old_bytes=old_path.read_bytes()
            _, cached=c.cached_request(root,'new','https://example.test','GET',{},c.Budget(0))
            self.assertIsNone(cached.get('timestamp_basis'))
            self.assertEqual(old_path.read_bytes(),old_bytes)
            self.assertEqual(cached['received_at'],meta['received_at'])

    def test_refresh_schedule_batch_resume_and_versioned_identity(self):
        import tempfile, json, gzip, sys
        from datetime import datetime, timezone
        from unittest.mock import patch
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return cls(2026,11,4,12,0,tzinfo=timezone.utc).astimezone(tz)
        old_fixture={'year':2026,'leagueId':1,'meetSeq':1,'gameId':205,'gameDate':'2026.10.31','gameTime':'14:00','homeTeam':'K01','awayTeam':'K02','homeTeamName':'A','awayTeamName':'B','gameStatus':'','endYn':'N','roundId':1}
        new_fixture={**old_fixture,'gameDate':'2026.12.06','gameTime':'16:00'}
        def schedule(rows): return json.dumps({'resultCode':'200','data':{'scheduleList':rows}}).encode()
        page=('''<select id="year"><option selected value="2026"></option></select><select id="meetSeq"><option selected value="1"></option></select><select id="gameId"><option selected value="205"></option></select><div class="game-info"><div class="team K01"></div><div class="team K02"></div></div><div class="team-versus"><div class="versus"><p>2026/12/06 16:00</p></div></div>''').encode()
        calls=[]; finished=[False]
        class Response:
            status_code=200
            def __init__(self,raw): self.raw=raw
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def iter_content(self,size): yield self.raw
        def fake_request(method,url,params=None,json=None,data=None,**kwargs):
            calls.append((url, params or json or data))
            if url==c.SCHEDULE_URL:
                month=json['month']; fixture={**new_fixture,'gameStatus':'FE','endYn':'Y','homeGoal':2,'awayGoal':1} if finished[0] else new_fixture; return Response(schedule([fixture] if month=='12' else []))
            if url==c.PAGE_URL: return Response(page)
            if url==c.URL: return Response(b'{"resultCode":"200","data":{}}')
            raise AssertionError(url)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); evidence=root/'evidence'; evidence.mkdir();out=root/'records.jsonl'
            for month in range(1,13):
                params={'leagueId':'1','teamId':'','ticketStatus':'','year':'2026','month':f'{month:02}','ticketYn':'N'}
                raw=schedule([old_fixture] if month==10 else [])
                stem=f'schedule-2026-1-{month:02}'
                (evidence/(stem+'.body.gz')).write_bytes(gzip.compress(raw))
                (evidence/(stem+'.json')).write_text(json.dumps({'url':c.SCHEDULE_URL,'params':params,'received_at':'2026-10-01T00:00:00+00:00','body_sha256':c.sha(raw)}),encoding='utf8')
            original=(evidence/'schedule-2026-1-10.json').read_bytes()
            def args(limit): return ['collector','--season-collect','--evidence',str(evidence),'--output',str(out),'--years','2026','--leagues','1','--prior-root',str(root/'missing'),'--max-requests',str(limit),'--max-lineups','0','--probe-unfinished','--forecast-fixture','kleague:2026:1:205','--refresh','--capture-id','batch-one']
            with patch.object(c,'datetime',Clock),patch('requests.request',side_effect=fake_request),patch.object(c.time,'sleep'):
                with patch.object(sys,'argv',args(2)):
                    with self.assertRaisesRegex(RuntimeError,'budget exhausted'): c.season_main()
                self.assertEqual(len(calls),2)
                with patch.object(sys,'argv',args(3)): c.season_main()
                self.assertEqual(len(calls),5)
                with patch.object(sys,'argv',args(0)): c.season_main()
                self.assertEqual(len(calls),5)
            self.assertEqual((evidence/'schedule-2026-1-10.json').read_bytes(),original)
            rows=[json.loads(line) for line in out.read_text(encoding='utf8').splitlines()]
            fixture=next(r for r in rows if r['kind']=='fixture')
            self.assertEqual(fixture['kickoff'],'2026-12-06T16:00:00+09:00')
            old_meta=json.loads(original)
            self.assertNotEqual(fixture['record_id'],c.observation_id('fixture','kleague:2026:1:205',old_meta))
            self.assertEqual(fixture['timestamp_basis'],'http_response_body_completed')
            self.assertEqual(len([r for r in rows if r['kind']=='lineup']),1)
            finished[0]=True
            with patch.object(c,'datetime',Clock),patch('requests.request',side_effect=fake_request),patch.object(c.time,'sleep'):
                second=args(3);second[second.index('batch-one')]='batch-two'
                with patch.object(sys,'argv',second): c.season_main()
            updated=[json.loads(line) for line in out.read_text(encoding='utf8').splitlines()]
            self.assertEqual(next(r for r in updated if r['kind']=='result')['payload']['score'],[2,1])
            self.assertNotEqual(fixture['record_id'],next(r for r in updated if r['kind']=='fixture')['record_id'])
            self.assertTrue((evidence/'observation-batch-one.json').exists())

if __name__ == '__main__': unittest.main()
