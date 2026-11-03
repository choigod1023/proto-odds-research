import copy,gzip,hashlib,importlib.util,json,pathlib
from datetime import date,timedelta
import pytest

HERE=pathlib.Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('pitcher_factorial',HERE/'build_official_pitcher_factorial.py');p=importlib.util.module_from_spec(sp);sp.loader.exec_module(p)


def fixture():
    off={'official_game_id':'20260401HHWO0','home_team_id':'WO','away_team_id':'HH','game_date':'20260401',
         'starting_pitchers':{'home':{'player_id':'1','name':'Home'},'away':{'player_id':'2','name':'Away'}}}
    game={'G_ID':off['official_game_id'],'HOME_ID':'WO','AWAY_ID':'HH','B_PIT_P_ID':1,'B_PIT_P_NM':'Home',
          'T_PIT_P_ID':2,'T_PIT_P_NM':'Away','W_PIT_P_ID':999,'L_PIT_P_ID':888}
    return off,{'game':[game]}


def test_schedule_pitcher_only_and_reject_missing_conflicting_or_duplicate():
    off,schedule=fixture();chosen,reason=p.schedule_pitchers(off,schedule)
    assert reason is None and chosen['home']['player_id']=='1' and chosen['away']['player_id']=='2'
    for change in ('zero','unknown','mismatch','team','name','duplicate_game','same_pitcher'):
        o=copy.deepcopy(off);s=copy.deepcopy(schedule);g=s['game'][0]
        if change=='zero':g['B_PIT_P_ID']=0
        if change=='unknown':o['starting_pitchers']['home']['player_id']=None
        if change=='mismatch':o['starting_pitchers']['home']['player_id']='999'
        if change=='team':g['HOME_ID']='wrong'
        if change=='name':g['B_PIT_P_NM']='wrong'
        if change=='duplicate_game':s['game'].append(copy.deepcopy(g))
        if change=='same_pitcher':g['T_PIT_P_ID']=1;o['starting_pitchers']['away']['player_id']='1'
        value,err=p.schedule_pitchers(o,s);assert value is None and err
    # Winner/loser changes cannot alter the chosen IDs.
    altered=copy.deepcopy(schedule);altered['game'][0]['W_PIT_P_ID']=1;altered['game'][0]['L_PIT_P_ID']=2
    assert p.schedule_pitchers(off,altered)[0]==chosen


def test_pitcher_target_score_id_stat_and_future_poison_invariance():
    off,schedule=fixture();chosen,_=p.schedule_pitchers(off,schedule)
    def game(fid,day,hg):return {'source_fixture_id':fid,'date':day,'home':'A','away':'B','hg':hg,'ag':0}
    games=[game('past','2026-03-30',2),game('same','2026-04-01',3),game('future','2026-04-02',5)]
    def box(day):return {'date':day,'home':'A','away':'B','data':{'home':[{'pcode':'1','inn':'5 ⅔','er':2,'r':3,'bf':90}], 'away':[{'pcode':'2','inn':'4.1','er':1,'r':2,'bf':80}]}}
    raw={'past':box('2026-03-30'),'same':box('2026-04-01'),'future':box('2026-04-02')}
    base={'fixture_id':'same','features':{}}
    first=p.pitcher_features(base,chosen,p.pitcher_history(games,raw),'2026-04-01')
    altered=copy.deepcopy(raw);poison=copy.deepcopy(games);poison[1]['hg']=999
    altered['same']['data']['home'][0].update(pcode='999',inn='0',er=999,r=999,bf=999);altered['same']['home_score']=999
    altered['future']['data']['away'][0].update(pcode='2',inn='9',er=0)
    second=p.pitcher_features(base,chosen,p.pitcher_history(poison,altered),'2026-04-01')
    assert first==second
    assert first['home_starter_strength']['value']['appearances'][0]['outs']==17
    assert all(x['date']<'2026-04-01' for f in first.values() for x in f['value']['appearances'])
    missing=p.pitcher_features(base,chosen,{},'2026-04-01');assert all(x['value'] is None for x in missing.values())


def test_nonstarter_features_unchanged_and_h1_preserved():
    off,s=fixture();chosen,_=p.schedule_pitchers(off,s);off.update(game_date='20260401',schedule_evidence={})
    base={'fixture_id':'g','scenario':'prior','projection':{'home_starter':'old'},'features':{'home_starter_strength':{'value':1},'away_starter_strength':{'value':2},'home_lineup_strength':{'value':'batters'},'park_factor':{'value':'park'}}}
    features={'home_starter_strength':{'value':None},'away_starter_strength':{'value':None}}
    one=p.apply(base,features,chosen,off,'official_starter_only');both=p.apply(base,features,chosen,off,'official_batting_and_starter')
    assert p.nonstarter_hash([base])==p.nonstarter_hash([one])==p.nonstarter_hash([both])
    assert one['projection']==base['projection'] and both['features']['home_lineup_strength']==base['features']['home_lineup_strength']
    assert one['official_schedule_pitcher_context']['point_in_time_verified'] is False
    with pytest.raises(ValueError):p.apply(base,{'home_starter_strength':{}},chosen,off,'official_starter_only')


def test_real_outputs_and_date_provenance():
    manifest=json.loads((HERE/'official-pitcher-factorial-manifest.json').read_text(encoding='utf8'))
    assert manifest['common_cohort']==193 and manifest['excluded_games']==0 and manifest['point_in_time_verified']==0
    assert manifest['postgame_first_pitcher_crosscheck_sides']=={'match':386,'mismatch':0,'postgame_missing':0}
    with gzip.open(p.GAMES,'rt',encoding='utf8') as f:games=list(map(json.loads,f))
    dates={g['source_fixture_id']:g['date'] for g in games}
    allrows={}
    for kind,name in [('official_starter_only','official-starter-only-2026.jsonl.gz'),('official_batting_and_starter','official-batting-and-starter-2026.jsonl.gz')]:
        path=HERE/name;assert p.raw_sha(path)==manifest['outputs_sha256'][kind]
        with gzip.open(path,'rt',encoding='utf8') as f:r=list(map(json.loads,f))
        assert len(r)==193 and len({x['fixture_id'] for x in r})==193
        assert all(x['scenario']==kind for x in r)
        for x in r:
            day=dates[x['fixture_id']];assert x['official_schedule_pitcher_context']['eligible_as_pregame_feature'] is False
            for side in ('home','away'):
                feature=x['features'][side+'_starter_strength'];assert feature['completed_at'] is None and feature['received_at'] is None
                for past in (feature['value'] or {}).get('appearances',[]):assert dates[past['fixture_id']]==past['date'] and past['date']<day
        allrows[kind]=r
    assert [x['fixture_id'] for x in allrows['official_starter_only']]==[x['fixture_id'] for x in allrows['official_batting_and_starter']]
    assert manifest['input_unchanged_nonstarter_hash']==manifest['starter_only_nonstarter_hash']
    assert manifest['h1_unchanged_nonstarter_hash']==manifest['both_nonstarter_hash']


def test_exact_input_bytes_and_model_scalar():
    checks=p.verify_inputs();assert all(v['exact_bytes_equal'] for v in checks.values())
    source=pathlib.Path(r'C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\kbo-player-model-20261103\experiments\kbo-player-model-20261103\adapter.py')
    if not source.exists():pytest.skip('frozen adapter unavailable')
    assert hashlib.sha256(source.read_bytes()).hexdigest()=='136a7c96624531010c6e247cc22fc6e5452680e8563a2f94e52a64e19c4a1ae0'
    sp=importlib.util.spec_from_file_location('frozen_model_adapter',source);a=importlib.util.module_from_spec(sp);sp.loader.exec_module(a)
    with gzip.open(HERE/'official-starter-only-2026.jsonl.gz','rt',encoding='utf8') as f:rows=list(map(json.loads,f))
    scalars=[a.scalar(side+'_starter_strength',x['features'][side+'_starter_strength']['value']) for x in rows for side in ('home','away')]
    assert len(scalars)==386 and sum(x is None for x in scalars)==20
