import copy,gzip,importlib.util,json,pathlib
import pytest

HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('official_overlay',HERE/'build_official_lineup_overlay.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_unique_fixture_and_duplicate_player():
    with pytest.raises(ValueError):m.unique_index([{'fixture_id':'x'},{'fixture_id':'x'}])
    with pytest.raises(ValueError):m.unique_index([{'fixture_id':None}])


def test_target_future_and_same_day_poison_never_change_histories():
    target={'source_fixture_id':'target','date':'2026-04-02','home':'A','away':'B','hg':5,'ag':1}
    games=[{'source_fixture_id':'past','date':'2026-04-01','home':'A','away':'B','hg':1,'ag':0},target,{'source_fixture_id':'future','date':'2026-04-03','home':'A','away':'B','hg':1,'ag':1}]
    def box(date,hit,code='10'):
        return {'date':date,'home':'A','away':'B','home_score':10,'away_score':9,'data':{'home':[{'code':code,'ab':3,'hit':hit,'bb':0,'hr':0}],'away':[]}}
    bat={'past':box('2026-04-01',1),'target':box('2026-04-02',2),'future':box('2026-04-03',3)}
    selected={'home':[{'player_id':'10','order':i} for i in range(1,10)],'away':[{'player_id':'10','order':i} for i in range(1,10)]}
    # Selection is supplied by official document alone; target box IDs never select players.
    base={'fixture_id':'target','scenario':'projected_from_prior_games','projection':{'home_lineup':'baseline'},'features':{'home_lineup_strength':{},'away_lineup_strength':{},'home_starter_strength':{'value':123},'away_bullpen_load':{'value':7}},'warning':'old'}
    official={'official_game_id':'target','game_date':'20260402','home_team_id':'A','away_team_id':'B','schedule_evidence':{},'lineup_evidence':{}}
    first=m.replace_lineups(base,selected,m.index_history(games,bat),'2026-04-02',official)
    g=copy.deepcopy(games);b=copy.deepcopy(bat);g[1]['hg']=999;g[1]['ag']=999
    b['target']['data']['home'][0].update(code='999',ab=1000,hit=777);b['target']['home_score']=999
    b['future']['data']['home'][0].update(code='10',ab=5000,hit=5000)
    again=m.replace_lineups(base,selected,m.index_history(g,b),'2026-04-02',official)
    assert first['features']==again['features']
    assert first['features']['home_lineup_strength']['value'][0]['history']==[{'fixture_id':'past','date':'2026-04-01','ab':3,'hit':1,'bb':0,'hr':0}]
    assert first['features']['home_starter_strength']==base['features']['home_starter_strength']
    assert first['projection']==base['projection'] and first['official_lineup_context']['eligible_as_pregame_feature'] is False


def test_exact_git_blob_and_checkout_policy(monkeypatch):
    checked=m.verify_base();assert len(checked)==4 and all(x['exact_bytes_equal'] for x in checked.values())
    original=m.subprocess.check_output
    def crlf(*args,**kwargs):return original(*args,**kwargs)+b'\r\n'
    monkeypatch.setattr(m.subprocess,'check_output',crlf)
    with pytest.raises(ValueError,match='raw worktree/git blob mismatch'):m.verify_base()
    assert m.sha(b'line\n')!=m.sha(b'line\r\n')


@pytest.mark.skipif(not (m.CAPTURE/'official-lineups.jsonl').exists(),reason='external frozen official evidence unavailable')
def test_reject_wrong_date_team_doubleheader_duplicate_and_unconfirmed():
    data=[json.loads(s) for s in (m.CAPTURE/'official-lineups.jsonl').read_text(encoding='utf8').splitlines()]
    doc=next(x for x in data if x['season_id']==2026)
    with (HERE/'rows.jsonl').open(encoding='utf8') as f: source=next(x for x in map(json.loads,f) if x['fixture_id']==doc['fixture_id'])
    selected=m.validate(doc,source);assert len(selected['home'])==len(selected['away'])==9
    for change in ('date','team','doubleheader','duplicate','flag','pit'):
        o=copy.deepcopy(doc);r=copy.deepcopy(source)
        if change=='date':o['game_date']='20260329'
        if change=='team':o['home_team_id']='WRONG'
        if change=='doubleheader':o['official_game_id']=o['official_game_id'][:-1]+'2'
        if change=='duplicate':o['lineups']['home'][1]['player_id']=o['lineups']['home'][0]['player_id']
        if change=='flag':o['lineup_flag']=False
        if change=='pit':o['eligible_as_pregame_feature']=True
        with pytest.raises(ValueError):m.validate(o,r)


def test_real_overlay_no_non_lineup_changes_and_prior_dates():
    manifest=json.loads((HERE/'official-lineup-overlay-2026-manifest.json').read_text(encoding='utf8'))
    path=HERE/'official-lineup-overlay-2026.jsonl.gz';assert m.bytes_sha(path)==manifest['output_sha256']
    assert manifest['non_lineup_features_sha256_before']==manifest['non_lineup_features_sha256_after']
    with gzip.open(path,'rt',encoding='utf8') as f:output=list(map(json.loads,f))
    with gzip.open(HERE/'prior-projected-features.jsonl.gz','rt',encoding='utf8') as f:base=m.unique_index(map(json.loads,f))
    assert len(output)==193 and len({x['fixture_id'] for x in output})==193
    missing=0
    for row in output:
        old=base[row['fixture_id']];day=row['official_lineup_context']['official_game_date'];day=day[:4]+'-'+day[4:6]+'-'+day[6:]
        assert row['scenario']==m.SCENARIO and row['projection']==old['projection']
        assert row['official_lineup_context']['point_in_time_verified'] is False
        for k,v in old['features'].items():
            if k not in ('home_lineup_strength','away_lineup_strength'):assert row['features'][k]==v
        for side in ('home','away'):
            players=row['official_lineup_context']['lineups'][side]; hist=row['features'][side+'_lineup_strength']['value']
            assert [x['player_id'] for x in players]==[x['player_id'] for x in hist]
            for x in hist:
                missing+=int(x['history'] is None)
                assert all(z['date']<day and z['fixture_id']!=row['fixture_id'] for z in x['history'] or [])
    assert missing==manifest['player_history_missing']==10


def test_all_nested_history_source_dates_match_archive():
    import collections
    with gzip.open(m.GAMES,'rt',encoding='utf8') as f: games=list(map(json.loads,f))
    dates={x['source_fixture_id']:x['date'] for x in games}
    with gzip.open(HERE/'official-lineup-overlay-2026.jsonl.gz','rt',encoding='utf8') as f:
        rows=list(map(json.loads,f))
    for r in rows:
        day=dates[r['fixture_id']]
        for side in ('home','away'):
            for player in r['features'][side+'_lineup_strength']['value']:
                for h in player['history'] or []:
                    assert dates[h['fixture_id']]==h['date'] and h['date']<day


def test_frozen_model_lineup_scalar_accepts_history_shape():
    import hashlib
    path=pathlib.Path(r'C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\kbo-player-model-20261103\experiments\kbo-player-model-20261103\adapter.py')
    if not path.exists():pytest.skip('external frozen model adapter unavailable')
    assert hashlib.sha256(path.read_bytes()).hexdigest()=='136a7c96624531010c6e247cc22fc6e5452680e8563a2f94e52a64e19c4a1ae0'
    spec=importlib.util.spec_from_file_location('frozen_model_adapter',path);adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
    with gzip.open(HERE/'official-lineup-overlay-2026.jsonl.gz','rt',encoding='utf8') as f:rows=list(map(json.loads,f))
    values=[adapter.scalar(side+'_lineup_strength',r['features'][side+'_lineup_strength']['value']) for r in rows for side in ('home','away')]
    assert len(values)==386 and all(v is not None for v in values)
