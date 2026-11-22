import gzip,hashlib,importlib.util,json
from pathlib import Path
P=Path('experiments/nba-official-information-20261103')
spec=importlib.util.spec_from_file_location('nba_provider_parse',P/'nba_provider_parse.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def row(tmp,day,events):
    body=json.dumps({'events':events}).encode();packed=gzip.compress(body,mtime=0);name='scoreboard-'+day; file=name+'.gz';(tmp/file).write_bytes(packed)
    return {'name':name,'status':200,'file':file,'body_sha256':hashlib.sha256(body).hexdigest(),'stored_sha256':hashlib.sha256(packed).hexdigest()}

def ev(i):
    return {'id':str(i),'season':{'year':2026,'type':2},'date':'2025-11-01T23:00Z','competitions':[{'id':str(i),'type':{'abbreviation':'STD'},'competitors':[{'homeAway':'home','team':{'id':'1','displayName':'Home'}},{'homeAway':'away','team':{'id':'2','displayName':'Away'}}]}]}

def test_selection_ignores_outcomes_and_caps_month(tmp_path):
    events=[ev(i) for i in range(100,120)]; r=row(tmp_path,'2025-11-01',events);first=m.build_census([r],tmp_path,['2025-11-01']); assert len(first['selected'])==18
    for e in events:e['competitions'][0]['competitors'][0]['score']='999999'
    r=row(tmp_path,'2025-11-01',events); second=m.build_census([r],tmp_path,['2025-11-01']); assert first==second

def test_missing_day_blocks_whole_month(tmp_path):
    r=row(tmp_path,'2025-11-01',[ev(100)]);c=m.build_census([r],tmp_path,['2025-11-01','2025-11-08']);assert c['selected']==[] and c['months']['2025-11']['status']=='blocked'

def test_bad_hash_fails_closed(tmp_path):
    r=row(tmp_path,'2025-11-01',[ev(100)]);r['body_sha256']='0'*64;c=m.build_census([r],tmp_path,['2025-11-01']);assert c['selected']==[] and c['months']['2025-11']['status']=='blocked'

def test_allstar_not_regular_despite_season_flag(tmp_path):
    game=ev(100); game['competitions'][0]['type']['abbreviation']='ALLSTAR'
    r=row(tmp_path,'2025-11-01',[game]); c=m.build_census([r],tmp_path,['2025-11-01'])
    assert c['selected']==[] and c['months']['2025-11']['other_season_or_type_events']==1

def test_real_census_and_raw_bytes():
    data=P/'provider'; records=json.loads((data/'requests.json').read_text(encoding='utf8'))
    for record in records:
        if record.get('file'):m.checked_body(record,data)
    census=json.loads((data/'census.json').read_text(encoding='utf8')); post=json.loads((data/'postgame.json').read_text(encoding='utf8')); excluded=json.loads((data/'summary-exclusions.json').read_text(encoding='utf8'))
    assert len(records)==160 and len(census['selected'])==126 and len(post)==125 and len(excluded)==1
    assert excluded[0]['game_id']=='401810384'
    assert len({x['provider_game_id'] for x in census['selected']})==126
    assert all(x['namespace']=='postgame_only' and all(len(t['postgame_starters'])==5 for t in x['teams']) for x in post)
    assert all(mo['selected_count']==18 for mo in census['months'].values())
