import base64,gzip,json,unittest
import hashlib
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import collect
from lineup_reference import parse_lineup,profile_name
from daily_identity import daily_roles
from export import actual_pitcher,box_names

HERE=Path(__file__).resolve().parent

class FakeCapture:
    def __init__(self,profiles=None):self.profiles=profiles or {};self.unavailable_profiles=set()
    def fetch(self,method,url,params,allow_network=True):return self.profiles.get(params['playerId'])

class TestLineups(unittest.TestCase):
    def test_windows_progress_reader_retry_and_request_limits(self):
        self.assertEqual(collect.MAX,2400);self.assertGreaterEqual(collect.DELAY,1.5)
        import hashlib
        self.assertEqual(hashlib.sha256((HERE/'contract.json').read_bytes()).hexdigest(),'512c6172eb792ed6e5f7afe7b90d4cfaa0e4087b05e0a497ec7cf9a997cb2480')
        original=collect.os.replace;calls=[]
        def flaky(src,dst):
            calls.append(1)
            if len(calls)==1:raise PermissionError('reader temporarily holds file')
            return original(src,dst)
        with tempfile.TemporaryDirectory() as folder, patch.object(collect.os,'replace',side_effect=flaky), patch.object(collect.time,'sleep'):
            p=Path(folder)/'progress.json';collect.write(p,{'done':1});self.assertEqual(json.loads(p.read_bytes()),{'done':1});self.assertEqual(len(calls),2)

    def test_confirmation_and_game_team_identity(self):
        game={'G_ID':'20250401HHKT0','HOME_ID':'KT','AWAY_ID':'HH','LE_ID':1,'SR_ID':0,'SEASON_ID':2025}
        def meta(side):return [{'G_ID':game['G_ID'],'T_ID':game[side+'_ID'],'LE_ID':1,'SR_ID':0,'SEASON_ID':2025}]
        def table():return [json.dumps({'rows':[{'row':[{'Text':str(i)},{'Text':'포수'},{'Text':'선수'+str(i)}]} for i in range(1,10)]},ensure_ascii=False)]
        value=[[{'LINEUP_CK':True}],meta('HOME'),meta('AWAY'),table(),table()]
        parsed=parse_lineup(json.dumps(value,ensure_ascii=False).encode(),game);self.assertEqual([p['order'] for p in parsed['home']],list(range(1,10)))
        value[0][0]['LINEUP_CK']=False;self.assertIsNone(parse_lineup(json.dumps(value,ensure_ascii=False).encode(),game))
        value[0][0]['LINEUP_CK']=True;value[1][0]['T_ID']='HH'
        with self.assertRaises(ValueError):parse_lineup(json.dumps(value,ensure_ascii=False).encode(),game)

    def test_encoded_year_alias_requires_unique_date_teams_and_header(self):
        game={'G_ID':'20251013SKSS0','G_DT':'20251013','G_TM':'18:30','HOME_ID':'SS','HOME_NM':'삼성','AWAY_ID':'SK','AWAY_NM':'SSG','HEADER_NO':0,'LE_ID':1,'SR_ID':0,'SEASON_ID':2025,'GAME_STATE_SC':'3','GAME_RESULT_CK':1}
        def meta(team):return [{'G_ID':game['G_ID'],'T_ID':team,'LE_ID':1,'SR_ID':0,'SEASON_ID':2025}]
        table=[json.dumps({'rows':[{'row':[{'Text':str(i)},{'Text':'포수'},{'Text':'선수'+str(i)}]} for i in range(1,10)]},ensure_ascii=False)]
        lineup=[[{'LINEUP_CK':True}],meta('SS'),meta('SK'),table,table]
        class FixtureCapture:
            def __init__(self,items):self.items=items
            def fetch(self,method,url,params):return (json.dumps({'code':'100','game':self.items},ensure_ascii=False).encode() if url==collect.SCHEDULE else json.dumps(lineup,ensure_ascii=False).encode()),{'sha256':'x','received_at':'now'}
        row={'fixture_id':'33331013SKSS02025','home_id':'삼성','away_id':'SSG','kickoff_at':'2025-10-13T18:30:00+09:00'}
        result=collect.stage1(row,FixtureCapture([game]));self.assertEqual(result['official_game_id'],'20251013SKSS0');self.assertEqual(result['fixture_match_basis'],'encoded_year_alias_exact_official_schedule_match')
        with self.assertRaises(ValueError):collect.stage1(row,FixtureCapture([game,game|{'G_ID':'20251013SKSS0x'}]))

    def test_actual_starter_marker_and_schedule_identity(self):
        table={'headers':[{'row':[{'Text':'선수명'},{'Text':'등판'}]}],
               'rows':[{'row':[{'Text':'12345'},{'Text':'선발'}]},{'row':[{'Text':'22222'},{'Text':'구원'}]}]}
        box={'tables':[{}, {}, {},table,table]};one={'date':'20250401','official_game_id':'20250401HHKT0','home_team':{'name':'KT'},'away_team':{'name':'한화'},'schedule_game':{'B_PIT_P_ID':12345,'B_PIT_P_NM':'투수','T_PIT_P_ID':12345,'T_PIT_P_NM':'투수'}}
        result=actual_pitcher(box,one,'home',None,FakeCapture(),False);self.assertTrue(result['actual_record_verified']);self.assertEqual(result['player_id'],'12345')
        box['tables'][4]['rows'][0]['row'][1]['Text']='구원'
        with self.assertRaises(ValueError):actual_pitcher(box,one,'home',None,FakeCapture(),False)

    def test_changed_schedule_does_not_override_actual(self):
        table={'headers':[{'row':[{'Text':'선수명'},{'Text':'등판'}]}],'rows':[{'row':[{'Text':'98765'},{'Text':'선발'}]}]}
        box={'tables':[{}, {}, {},table,table]};one={'date':'20250401','home_team':{'name':'KT'},'away_team':{'name':'한화'},'schedule_game':{'B_PIT_P_ID':12345,'B_PIT_P_NM':'예정투수'}}
        game={'date':'2025-04-01','home':'KT','away':'한화','data':{'home':[{'pcode':'98765','name':'실제투수'}]}}
        with self.assertRaises(ValueError):actual_pitcher(box,one,'home',game,FakeCapture(),False)
        profile=b'<span id="xplayerProfile_lblName">\xec\x8b\xa4\xec\xa0\x9c\xed\x88\xac\xec\x88\x98</span>'
        data=FakeCapture({'98765':(profile,{'sha256':hashlib.sha256(profile).hexdigest(),'received_at':'now'})})
        result=actual_pitcher(box,one,'home',game,data,False);self.assertEqual(result['player_id'],'98765');self.assertFalse(result['schedule_agrees'])

    def test_pitcher_name_change_requires_same_official_id_and_profile(self):
        table={'headers':[{'row':[{'Text':'선수명'},{'Text':'등판'}]}],'rows':[{'row':[{'Text':'최지명'},{'Text':'선발'}]}]}
        box={'tables':[{}, {}, {},table,table]};one={'date':'20250507','home_team':{'name':'두산'},'away_team':{'name':'LG'},'schedule_game':{'T_PIT_P_ID':68419,'T_PIT_P_NM':'최채흥'}}
        game={'date':'2025-05-07','home':'두산','away':'LG','data':{'away':[{'pcode':'68419','name':'최채흥'}]}}
        profile='<span id="xplayerProfile_lblName">최지명</span>'.encode();capture=FakeCapture({'68419':(profile,{'sha256':hashlib.sha256(profile).hexdigest(),'received_at':'now'})})
        result=actual_pitcher(box,one,'away',game,capture,False);self.assertEqual(result['player_id'],'68419');self.assertTrue(result['schedule_agrees']);self.assertTrue(result['name_change_or_alias']);self.assertEqual(result['name'],'최지명')

    def test_repeated_official_pitcher_name_is_ambiguous(self):
        table={'headers':[{'row':[{'Text':'선수명'},{'Text':'등판'}]}],'rows':[{'row':[{'Text':'이승현'},{'Text':'선발'}]},{'row':[{'Text':'이승현'},{'Text':'7.3'}]}]}
        box={'tables':[{}, {}, {},table,table]};one={'date':'20250501','home_team':{'name':'SSG'},'away_team':{'name':'삼성'},'schedule_game':{'T_PIT_P_ID':51454,'T_PIT_P_NM':'이승현'}}
        with self.assertRaisesRegex(ValueError,'duplicate_official_pitcher_name'):actual_pitcher(box,one,'away',None,FakeCapture(),False)

    def test_box_identity_extract_ignores_performance(self):
        table={'rows':[{'row':[{'Text':'1'},{'Text':'좌익수'},{'Text':'선수'},{'Text':'타격성적'}]}]};box={'tables':[{},table,table]}
        self.assertEqual(box_names(box,'home'),{(1,'선수')});table['rows'][0]['row'][3]['Text']='9999';self.assertEqual(box_names(box,'home'),{(1,'선수')})

    def test_final_artifacts_when_present(self):
        if not (HERE/'manifest.json').exists():self.skipTest('capture not exported')
        manifest=json.loads((HERE/'manifest.json').read_bytes());raw=gzip.decompress((HERE/'actual-lineups-2025.jsonl.gz').read_bytes())
        import hashlib
        self.assertEqual(hashlib.sha256(raw).hexdigest(),manifest['lineups_uncompressed_sha256']);rows=[json.loads(x) for x in raw.splitlines()];self.assertEqual(len(rows),736);self.assertEqual(len({x['fixture_id'] for x in rows}),736)
        identity_gz=(HERE/'identity-candidates-2025.jsonl.gz').read_bytes();identity_raw=gzip.decompress(identity_gz);self.assertEqual(hashlib.sha256(identity_gz).hexdigest(),manifest['identity_candidates_gzip_sha256']);self.assertEqual(hashlib.sha256(identity_raw).hexdigest(),manifest['identity_candidates_uncompressed_sha256']);identity={x['fixture_id']:x for x in map(json.loads,identity_raw.splitlines())};self.assertEqual(len(identity),736)
        for item in identity.values():
            for side in ('home','away'):
                for p in (item.get('batters') or {}).get(side,[]):self.assertEqual(set(p),{'slot','player_id','name'})
                for p in (item.get('pitchers') or {}).get(side,[]):self.assertEqual(set(p),{'player_id','name'})
        self.assertEqual(hashlib.sha256((HERE/'pitcher-daily-identity.json').read_bytes()).hexdigest(),manifest['daily_identity_sha256']);self.assertEqual(hashlib.sha256((HERE/'unconfirmed-review.json').read_bytes()).hexdigest(),manifest['unconfirmed_review_sha256'])
        raw_evidence=(HERE/'raw-evidence.jsonl.gz').read_bytes();self.assertEqual(hashlib.sha256(raw_evidence).hexdigest(),manifest['evidence_gzip_sha256']);bodies={}
        with gzip.open(HERE/'raw-evidence.jsonl.gz','rt',encoding='utf8') as stream:
            previous_direct=None;direct_count=0
            for line in stream:
                record=json.loads(line)
                if record.get('origin')=='new_request':
                    direct_count+=1
                    stamp=datetime.fromisoformat(record.get('received_at') or record['failed_at'])
                    if previous_direct is not None:self.assertGreaterEqual((stamp-previous_direct).total_seconds(),1.5)
                    previous_direct=stamp
                if record.get('body_base64'):
                    body=base64.b64decode(record['body_base64'],validate=True);self.assertEqual(hashlib.sha256(body).hexdigest(),record['sha256']);bodies[record['sha256']]=body
        self.assertLessEqual(direct_count,2400)
        self.assertGreater(len(bodies),0)
        self.assertTrue(all(not x['historical_receipt_proven'] and not x['historical_publication_time_proven'] for x in rows))
        profile_names={}
        for rec in rows:
            if not rec.get('lineup_evidence'):continue
            schedule=json.loads(bodies[rec['schedule_evidence']['sha256']]);games=[g for g in schedule['game'] if g['G_ID']==rec['official_game_id']];self.assertEqual(len(games),1)
            official=parse_lineup(bodies[rec['lineup_evidence']['sha256']],games[0])
            if not rec['lineup_confirmed']:self.assertIsNone(official);continue
            self.assertIsNotNone(official)
            if rec.get('fixture_match_basis')=='encoded_year_alias_exact_official_schedule_match':
                self.assertEqual(rec['fixture_id'][4:13],rec['official_game_id'][4:]);self.assertEqual(rec['fixture_id'][4:8],rec['date'][5:7]+rec['date'][8:10])
            box=json.loads(bodies[rec['box_evidence']['sha256']]) if rec.get('box_evidence') else None
            seen=set()
            for side in ('home','away'):
                players=rec[side]['ordered_hitters'];self.assertEqual([(p['slot'],p['name']) for p in players],[(p['order'],p['name']) for p in official[side]])
                for player in players:
                    if not player['player_id']:self.assertTrue(player['exclusion_reasons']);continue
                    pid=player['player_id'];self.assertNotIn(pid,seen);seen.add(pid);self.assertIsNotNone(box);self.assertIn((player['slot'],player['name']),box_names(box,side))
                    profile_sha=player['evidence']['profile_sha256']
                    if profile_sha not in profile_names:profile_names[profile_sha]=profile_name(bodies[profile_sha])
                    self.assertEqual(profile_names[profile_sha],player['name']);self.assertIn(pid,player['evidence']['candidate_player_ids'])
                    source=identity[rec['fixture_id']]['batters'][side];expected={x['player_id'] for x in source if x['slot']==player['slot'] and x['player_id'].isdigit()};self.assertEqual(set(player['evidence']['candidate_player_ids']),expected)
                    checks=player['evidence']['candidate_profile_checks'];self.assertEqual({x['player_id'] for x in checks},set(player['evidence']['candidate_player_ids']))
                    for check in checks:
                        self.assertIn('sha256',check)
                        if check['sha256'] not in profile_names:profile_names[check['sha256']]=profile_name(bodies[check['sha256']])
                        self.assertEqual(profile_names[check['sha256']],check['name'])
                    self.assertEqual([x['player_id'] for x in checks if x['name']==player['name']],[pid])
                starter=rec[side]['starter']
                if starter:
                    self.assertTrue(starter['actual_record_verified']);table=box['tables'][4 if side=='home' else 3];heads=[x['Text'].strip() for x in table['headers'][0]['row']];matches=[]
                    for entry in table['rows']:
                        cells=[str(x.get('Text') or '').strip() for x in entry['row']]
                        if cells[heads.index('등판')]=='선발':matches.append(cells[heads.index('선수명')])
                    self.assertEqual(len(matches),1);self.assertIn(matches[0],(starter['player_id'],starter['name']))
                    if starter.get('name_change_or_alias'):
                        self.assertEqual(profile_name(bodies[starter['profile_sha256']]),starter['name'])
                    if starter.get('daily_identity_checks'):
                        checks=starter['daily_identity_checks'];self.assertEqual([(x['player_id'],x['role']) for x in checks],[('51454','선발'),('60146','구원')])
                        source_pitchers=identity[rec['fixture_id']]['pitchers'][side];self.assertEqual({x['player_id'] for x in source_pitchers if x['name']=='이승현'},set(starter['identity_candidate_ids']))
                        self.assertEqual([x['profile']['throwing_hand'] for x in checks],['left','right'])
                        schedule_matches=[g for g in schedule['game'] if (g.get('G_DT'),g.get('HOME_ID'),g.get('AWAY_ID'))==(rec['date'].replace('-',''),rec['home']['team']['id'],rec['away']['team']['id'])]
                        self.assertEqual(len(schedule_matches),1);self.assertEqual(schedule_matches[0]['G_ID'],rec['official_game_id'])
                        for check in checks:
                            self.assertEqual(profile_name(bodies[check['profile']['sha256']]),'이승현')
                            matches=[d for d in daily_roles(bodies[check['daily_sha256']]) if d['month_day']==rec['date'][5:7]+'.'+rec['date'][8:10] and d['opponent']==rec['home']['team']['name']]
                            self.assertEqual(len(matches),1);self.assertEqual(matches[0]['role'],check['role'])
        review=json.loads((HERE/'unconfirmed-review.json').read_bytes());self.assertEqual(len(review),5)
        for item in review:
            self.assertFalse(item['lineup_confirmed_flag']);self.assertFalse(item['hitter_box_explicit_starter_text']);self.assertEqual(item['hitter_box_explicit_starter_fields'],[])
            self.assertIn(item['schedule_sha256'],bodies);self.assertIn(item['lineup_sha256'],bodies);self.assertIn(item['box_sha256'],bodies)
            schedule=json.loads(bodies[item['schedule_sha256']]);matches=[x for x in schedule['game'] if x.get('G_ID')==item['official_game_id']];self.assertEqual(len(matches),1);game=matches[0]
            self.assertEqual((str(game['G_DT']),str(game['LE_ID']),str(game['SR_ID']),str(game['SEASON_ID'])),(item['date'],str(item['league_id']),str(item['series_id']),str(item['season_id'])))
            expected={'leId':game['LE_ID'],'srId':game['SR_ID'],'seasonId':game['SEASON_ID'],'gameId':game['G_ID']};self.assertEqual(item['lineup_request'],expected);self.assertEqual(item['box_request'],expected)
            self.assertIsNone(parse_lineup(bodies[item['lineup_sha256']],game));box=json.loads(bodies[item['box_sha256']]);self.assertTrue(all('선발' not in json.dumps(t,ensure_ascii=False) for t in box['tables'][1:3]))

if __name__=='__main__':unittest.main()
