import copy
import json
from pathlib import Path
import pytest
from scripts import research_kbo_historical_lineups as k

SAMPLES = Path(__file__).resolve().parents[1] / 'experiments/kbo-official-lineups-20261103/samples'


def sample(year='20230401', gid='20230401HHWO0'):
    schedule = json.loads((SAMPLES / (year+'-schedule.bin')).read_bytes())
    game = next(g for g in schedule['game'] if g['G_ID'] == gid)
    return (SAMPLES / (year+'-lineup.bin')).read_bytes(), game


@pytest.mark.parametrize('year,gid', [('20230401','20230401HHWO0'),('20250322','20250322OBSK0'),('20260328','20260328HTSK0')])
def test_official_historical_response_has_game_specific_nines(year,gid):
    raw, game = sample(year,gid)
    result = k.parse_lineup(raw, game)
    assert all([x['order'] for x in result[side]] == list(range(1,10)) for side in ('home','away'))
    wrong = dict(game, G_ID=game['G_ID'][:-1]+'9')
    with pytest.raises(ValueError, match='identity_mismatch'):
        k.parse_lineup(raw, wrong)


def test_false_flag_never_promotes_fallback_lineup():
    raw, game = sample()
    payload = json.loads(raw)
    payload[0][0]['LINEUP_CK'] = False
    assert k.parse_lineup(json.dumps(payload).encode(),game) is None
    payload[0][0]['LINEUP_CK'] = 1
    with pytest.raises(ValueError):
        k.parse_lineup(json.dumps(payload).encode(),game)


def test_wrong_season_returns_fallback_and_next_day_is_distinct():
    _, game = sample()
    wrong_season = (SAMPLES/'wrong-season.bin').read_bytes()
    assert k.parse_lineup(wrong_season, game) is None
    next_day = json.loads((SAMPLES/'wrong-day.bin').read_bytes())
    assert next_day[0][0]['LINEUP_CK'] is True
    assert all(next_day[i][0]['G_ID'] == '20230402HHWO0' for i in (1,2))
    with pytest.raises(ValueError, match='G_ID'):
        k.parse_lineup(json.dumps(next_day).encode(), game)


def test_original_sample_hashes():
    for meta_file in SAMPLES.glob('*.json'):
        meta = json.loads(meta_file.read_text(encoding='utf8'))
        assert k.digest(meta_file.with_suffix('.bin').read_bytes()) == meta['sha256']


def test_publication_time_claim_is_preserved_without_inference():
    raw=json.dumps({'data':[{'ANNOUNCED_AT':'2023-01-01T01:02:03+09:00','UPDATED_AT':'not_a_publication'}]}).encode()
    assert k.publication_time_fields(raw)==[{'path':['data','0','ANNOUNCED_AT'],'value':'2023-01-01T01:02:03+09:00'}]
    assert k.publication_time_fields(sample()[0])==[]


def test_doubleheader_requires_unambiguous_game_id():
    games = [{'G_ID':'g1','HOME_NM':'H','AWAY_NM':'A'}, {'G_ID':'g2','HOME_NM':'H','AWAY_NM':'A'}]
    with pytest.raises(ValueError, match='not_uniquely'):
        k.select_schedule_game(games, None, 'H', 'A')
    assert k.select_schedule_game(games, 'g2', 'H', 'A')['G_ID'] == 'g2'


def test_missing_same_game_uses_unique_team_season_identity_only():
    raw, game = sample()
    lineup = k.parse_lineup(raw, game)
    source = {}
    for side, offset in (('home',0),('away',100)):
        team = game[side.upper()+'_NM'].strip()
        source[side] = [{'name':p['name'],'code':str(offset+p['order'])} for p in lineup[side]]
        game[side.upper()+'_NM'] = team
    season = str(game['SEASON_ID'])
    index = {(season,game[side.upper()+'_NM'],p['name']):{p['code']}
             for side in ('home','away') for p in source[side]}
    matched = k.match_from_team_season(copy.deepcopy(lineup),game,index)
    assert matched['home'][0]['_match_scope'] == 'other_game_same_team_season_name'
    first = lineup['home'][0]
    index[(season,game['HOME_NM'],first['name'])] = {'1','999'}
    with pytest.raises(ValueError, match='ambiguous'):
        k.match_from_team_season(copy.deepcopy(lineup),game,index)


def test_nine_and_team_validation():
    raw, game = sample()
    body = json.loads(raw)
    body[1][0]['T_ID'] = 'wrong'
    with pytest.raises(ValueError, match='T_ID'):
        k.parse_lineup(json.dumps(body).encode(),game)
    body = json.loads(raw)
    table = json.loads(body[3][0]); table['rows'].pop(); body[3][0] = json.dumps(table)
    with pytest.raises(ValueError, match='invalid_nine'):
        k.parse_lineup(json.dumps(body).encode(),game)


def test_postgame_match_is_unique_and_excludes_wrong_order():
    raw, game = sample()
    lineup = k.parse_lineup(raw,game)
    data = {'data': {s:[{'name':p['name'],'order':p['order'],'code':str((0 if s=='home' else 100)+p['order'])} for p in lineup[s]] for s in ('home','away')}}
    result = k.match_players(copy.deepcopy(lineup),data)
    assert result['home'][0]['player_id'] == '1'
    assert 'pending_official_profile' in result['home'][0]['player_id_basis']
    data['data']['home'].append(dict(data['data']['home'][0],code='999'))
    with pytest.raises(ValueError, match='ambiguous'):
        k.match_players(copy.deepcopy(lineup),data)


def test_abbreviated_postgame_name_requires_official_profile_check():
    raw, game = sample()
    lineup = k.parse_lineup(raw, game)
    data = {'data': {s:[{'name':p['name'],'order':p['order'],'code':str((0 if s=='home' else 100)+p['order'])} for p in lineup[s]] for s in ('home','away')}}
    data['data']['home'][0]['name'] = 'abbreviated'
    result = k.match_players(lineup, data)
    assert result['home'][0]['_candidate_ids'] == ['1']
    assert 'player_id' not in result['home'][0]


def test_profile_parser_and_failed_http_cache(tmp_path):
    assert k.profile_name('<span id="p_playerProfile_lblName">홍길동</span>'.encode()) == '홍길동'
    with pytest.raises(ValueError):
        k.profile_name(b'<html></html>')
    f=k.Fetcher(tmp_path,1,1)
    raw=b'blocked'; sha=k.digest(raw)
    (tmp_path/'raw'/sha).write_bytes(raw)
    params={'date':'x'}; key=k.digest(json.dumps(['POST',k.SCHEDULE,params],sort_keys=True,ensure_ascii=False).encode())
    (tmp_path/'requests'/f'{key}.json').write_text(json.dumps({'sha256':sha,'http_status':429}),encoding='utf8')
    with pytest.raises(k.StopCapture,match='cached_http_status:429'):
        f.fetch('POST',k.SCHEDULE,params)


def test_complete_then_resume_preserves_jsonl_and_manifest(tmp_path, monkeypatch):
    import types
    raw, game = sample()
    lineup = k.parse_lineup(raw, game)
    by_id = {}
    source = {'data':{}}
    for j, side in enumerate(('home','away')):
        source['data'][side] = []
        for p in lineup[side]:
            pid = str(10000+j*100+p['order'])
            by_id[pid] = p['name']
            source['data'][side].append({'code':pid,'name':p['name'],'order':p['order']})
    gid = game['G_ID']; fid = gid + str(game['SEASON_ID'])
    fixture = {'fixture_id':fid,'season':game['SEASON_ID'],'home_id':game['HOME_NM'].strip(),
               'away_id':game['AWAY_NM'].strip(),'kickoff_at':'2023-04-01T14:00:00+09:00'}
    rows = tmp_path/'rows.jsonl'; rows.write_text(json.dumps(fixture,ensure_ascii=False)+'\n'+json.dumps({'fixture_id':'other','season':2024})+'\n',encoding='utf8')
    batters = tmp_path/'batters.json'; batters.write_text(json.dumps({fid:source},ensure_ascii=False),encoding='utf8')
    calls = []
    def fake_fetch(self, method, url, params):
        calls.append((method,url,params))
        if url == k.SCHEDULE: response = (SAMPLES/'20230401-schedule.bin').read_bytes()
        elif url == k.LINEUP: response = raw
        else: response = f'<span id="p_playerProfile_lblName">{by_id[params["playerId"]]}</span>'.encode()
        return response, {'url':url,'sha256':k.digest(response),'received_at':'2026-11-03T00:00:00+00:00'}
    monkeypatch.setattr(k.Fetcher,'fetch',fake_fetch)
    args = types.SimpleNamespace(rows=str(rows),batters=str(batters),output=str(tmp_path/'out'),
                                 max_requests=100,delay=1,fixture=None,only_season=2023)
    k.run(args)
    output=tmp_path/'out'/'official-lineups.jsonl'; first=output.read_bytes()
    record=json.loads(first); assert record['lineup_status']=='official_flag_true_profile_name_crossmatched'
    assert record['eligible_as_pregame_feature'] is False
    assert record['historical_publication_time'] is None
    assert len(record['lineups']['home'])==len(record['lineups']['away'])==9
    before=len(calls); k.run(args)
    assert len(calls)==before and output.read_bytes()==first
    manifest=json.loads((tmp_path/'out'/'manifest.json').read_text(encoding='utf8'))
    assert (manifest['denominator'],manifest['processed'],manifest['unprocessed'])==(2,1,1)
    assert (manifest['selected_denominator'],manifest['selected_processed'],manifest['selected_unprocessed'])==(1,1,0)
    assert manifest['lineups_sha256']==k.digest(first)


def test_request_failure_stops_without_marking_fixture_completed(tmp_path, monkeypatch):
    import types
    fid='20230401HHWO02023'
    rows=tmp_path/'rows.jsonl';rows.write_text(json.dumps({'fixture_id':fid,'season':2023,'home_id':'H','away_id':'A'})+'\n',encoding='utf8')
    batters=tmp_path/'batters.json';batters.write_text('{}',encoding='utf8')
    def failed(*_args): raise k.StopCapture('http_status:429')
    monkeypatch.setattr(k.Fetcher,'fetch',failed)
    args=types.SimpleNamespace(rows=str(rows),batters=str(batters),output=str(tmp_path/'out'),max_requests=10,delay=1,fixture=None,only_season=None)
    k.run(args)
    assert not (tmp_path/'out'/'official-lineups.jsonl').exists()
    result=json.loads((tmp_path/'out'/'manifest.json').read_text(encoding='utf8'))
    assert result['stop_reason']=='http_status:429'
    assert (result['processed'],result['unprocessed'])==(0,1)


def test_two_collectors_cannot_share_the_same_output(tmp_path):
    with k.capture_lock(tmp_path):
        with pytest.raises(k.StopCapture,match='output_already_in_use'):
            with k.capture_lock(tmp_path):
                pass
    with k.capture_lock(tmp_path):
        pass


def test_checkpoint_recovery_rejects_conflict_and_preserves_source(tmp_path):
    from scripts.recover_kbo_collector_checkpoint import recover
    source=tmp_path/'old';source.mkdir();(source/'raw').mkdir();(source/'requests').mkdir()
    row={'fixture_id':'g','official_game_id':'g','lineup_status':'unresolved','reason':'x'}
    original=(json.dumps(row)+'\n')*2
    (source/'official-lineups.jsonl').write_text(original,encoding='utf8')
    recover(source,tmp_path/'new')
    assert len((tmp_path/'new'/'official-lineups.jsonl').read_text(encoding='utf8').splitlines())==1
    assert (source/'official-lineups.jsonl').read_text(encoding='utf8')==original
    changed=dict(row,reason='different')
    (source/'official-lineups.jsonl').write_text(json.dumps(row)+'\n'+json.dumps(changed)+'\n',encoding='utf8')
    with pytest.raises(ValueError,match='conflicting_duplicate'):
        recover(source,tmp_path/'another')
    (source/'official-lineups.jsonl').write_text(json.dumps(row)+'\n'+json.dumps(dict(row,starting_pitchers={'home':'other'}))+'\n',encoding='utf8')
    with pytest.raises(ValueError,match='conflicting_duplicate'):
        recover(source,tmp_path/'third')


def test_independent_audit_rejects_corrupted_profile(tmp_path):
    import subprocess, sys
    raw, game = sample()
    lineup = k.parse_lineup(raw,game)
    rawdir=tmp_path/'raw';rawdir.mkdir()
    def store(data,params=None):
        sha=k.digest(data);(rawdir/sha).write_bytes(data)
        return {'sha256':sha,'http_status':200,'params':params or {}}
    schedule=store((SAMPLES/'20230401-schedule.bin').read_bytes())
    lineup_meta=store(raw)
    last_profile=None
    for offset,side in ((0,'home'),(100,'away')):
        for player in lineup[side]:
            pid=str(10000+offset+player['order'])
            body=f'<span id="p_playerProfile_lblName">{player["name"]}</span>'.encode()
            evidence=store(body,{'playerId':pid})
            player.update(player_id=pid,official_profile_evidence=evidence)
            last_profile=rawdir/evidence['sha256']
    row={'fixture_id':game['G_ID']+'2023','official_game_id':game['G_ID'],'game_date':game['G_DT'],
         'home_team_id':game['HOME_ID'],'away_team_id':game['AWAY_ID'],'eligible_as_pregame_feature':False,
         'schedule_evidence':schedule,'lineup_evidence':lineup_meta,'lineup_status':'official_flag_true_profile_name_crossmatched',
         'lineup_flag':True,'lineups':lineup,
         'starting_pitchers':{side:{'player_id':str(game.get(prefix+'_PIT_P_ID') or ''),'name':game.get(prefix+'_PIT_P_NM')}
                              for side,prefix in [('home','B'),('away','T')]}}
    (tmp_path/'official-lineups.jsonl').write_text(json.dumps(row,ensure_ascii=False)+'\n',encoding='utf8')
    script=Path(__file__).resolve().parents[1]/'scripts/audit_kbo_historical_lineups.py'
    result=subprocess.run([sys.executable,str(script),str(tmp_path)],capture_output=True)
    assert result.returncode==0, result.stderr
    last_profile.write_bytes(b'corrupted')
    result=subprocess.run([sys.executable,str(script),str(tmp_path)],capture_output=True)
    assert result.returncode!=0 and b'raw_hash_mismatch' in result.stderr
