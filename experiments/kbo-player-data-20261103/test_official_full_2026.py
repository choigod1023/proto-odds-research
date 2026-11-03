import copy,gzip,importlib.util,json,pathlib
from datetime import date
import pytest

HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('full_official_2026',HERE/'build_official_full_2026.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_gap_summary_precise_denominators_and_thresholds():
    x=m.gap_summary([1,10,61,100],2)
    assert x=={'observed':4,'no_history':2,'median_days':35.5,'p90_nearest_rank_days':100,'max_days':100,'greater_than_14_days':2,'greater_than_30_days':2,'greater_than_60_days':2,'greater_than_90_days':1}
    assert m.gap_summary([],4)['median_days'] is None


def test_current_target_and_future_poison_cannot_change_any_player_history():
    games=[{'source_fixture_id':'past','date':'2026-03-31','home':'A','away':'B','hg':1,'ag':0}, {'source_fixture_id':'target','date':'2026-04-01','home':'A','away':'B','hg':2,'ag':1}, {'source_fixture_id':'future','date':'2026-04-02','home':'A','away':'B','hg':4,'ag':2}]
    def b(day):return {'date':day,'home':'A','away':'B','data':{'home':[{'code':'1','order':1,'pos':'X','ab':3,'hit':1,'bb':0,'hr':0}],'away':[]}}
    def q(day):return {'date':day,'home':'A','away':'B','data':{'home':[{'pcode':'10','inn':'5.2','er':1,'r':2}],'away':[{'pcode':'20','inn':'4','er':2,'r':2}]}}
    bat={x['source_fixture_id']:b(x['date']) for x in games};pitch={x['source_fixture_id']:q(x['date']) for x in games}
    selected={'home':[{'player_id':'1','order':i} for i in range(1,10)],'away':[{'player_id':'1','order':i} for i in range(1,10)]}
    official={'official_game_id':'target','game_date':'20260401','home_team_id':'A','away_team_id':'B','schedule_evidence':{},'lineup_evidence':{}}
    chosen={'home':{'player_id':'10'},'away':{'player_id':'20'}}
    base={'fixture_id':'target','projection':{'metadata':'original'},'features':{'home_lineup_strength':{},'away_lineup_strength':{},'home_starter_strength':{},'away_starter_strength':{},'park_factor':{'value':None}},'scenario':'projected_from_prior_games'}
    day='2026-04-01'
    def build(g,b1,p1):
        bindex=m.h1.index_history(g,b1);pindex=m.h2.pitcher_history(g,p1)
        hr=m.h1.replace_lineups(base,selected,bindex,day,official);pf=m.h2.pitcher_features(base,chosen,pindex,day)
        return m.h2.apply(hr,pf,chosen,official,'official_batting_and_starter')
    before=build(games,bat,pitch);g=copy.deepcopy(games);b1=copy.deepcopy(bat);p1=copy.deepcopy(pitch)
    g[1]['hg']=999;g[1]['ag']=777
    b1['target']['home_score']=999;b1['target']['data']['home'][0].update(code='999',hit=999,ab=999)
    p1['target']['data']['home'][0].update(pcode='999',inn='9',er=999,r=999)
    b1['future']['data']['home'][0].update(hit=999,ab=999);p1['future']['data']['away'][0].update(er=999,inn='8')
    after=build(g,b1,p1)
    assert before['features']==after['features'] and before['projection']==after['projection']
    assert before['features']['home_lineup_strength']['value'][0]['history'][0]['fixture_id']=='past'
    assert before['features']['home_starter_strength']['value']['appearances'][0]['outs']==17


def test_fixed_dataset_hashes_and_exact_input_policies():
    checks=m.verify_frozen_inputs();assert checks['full_official_source']['exact_bytes_equal']
    for name,data in checks.items():
        if 'byte_policy' not in data:assert data['exact_bytes_equal'] and data['raw_sha256']==data['git_blob_sha256']
        else:assert data['byte_policy']=='explicit CRLF to LF for text manifest only'


@pytest.mark.skipif(not m.SOURCE.exists(),reason='external official frozen source unavailable')
def test_fixture_team_date_ids_and_ambiguous_doubleheader(monkeypatch):
    monkeypatch.setattr(m.h1,'CAPTURE',m.CAPTURE);m.h1.source_bytes.cache_clear()
    official=[json.loads(x) for x in m.SOURCE.read_bytes().splitlines()]
    item=next(x for x in official if x['fixture_match_basis']=='unique_date_home_away_schedule_match' and x['lineup_status']=='official_flag_true_profile_name_crossmatched')
    with (HERE/'rows.jsonl').open(encoding='utf8') as f:row=next(x for x in map(json.loads,f) if x['fixture_id']==item['fixture_id'])
    selected,pitch,reason=m.inspect_official(item,row);assert reason is None and len(selected['home'])==len(selected['away'])==9
    for variant in ('fixture','date','team','duplicate_player','incorrect_order','missing_pitcher'):
        obj=copy.deepcopy(item);r=copy.deepcopy(row)
        if variant=='fixture':r['fixture_id']='score:2026-01-01:A:B'
        if variant=='date':obj['game_date']='20260301'
        if variant=='team':obj['home_team_id']='bad'
        if variant=='duplicate_player':obj['lineups']['home'][1]['player_id']=obj['lineups']['home'][0]['player_id']
        if variant=='incorrect_order':obj['lineups']['home'][1]['order']=1
        if variant=='missing_pitcher':obj['starting_pitchers']['home']['player_id']='0'
        if variant=='missing_pitcher':assert m.inspect_official(obj,r)[2] is not None
        else:
            with pytest.raises(ValueError):m.inspect_official(obj,r)
    real=m.h1.check_evidence
    def fake(e):
        raw=real(e)
        if e is item['schedule_evidence']:
            data=json.loads(raw);game=next(x for x in data['game'] if x['G_ID']==item['official_game_id']);other=copy.deepcopy(game);other['G_ID']=other['G_ID'][:-1]+'9';data['game'].append(other);return json.dumps(data).encode()
        return raw
    monkeypatch.setattr(m.h1,'check_evidence',fake)
    with pytest.raises(ValueError,match='doubleheader_without_identity'):m.inspect_official(item,row)


def test_709_fixture_sets_variants_and_original_193_unchanged():
    manifest=json.loads((HERE/'official-full-2026-manifest.json').read_text(encoding='utf8'));groups=json.loads((HERE/'official-full-2026-cohorts.jsonl').read_text(encoding='utf8'))
    assert manifest['common_cohort']==709 and manifest['original_193_overlap']==193 and manifest['new_complement']==516 and manifest['exclusion_reasons']=={'lineup_player_ids_unavailable':17}
    assert len(set(groups['all']))==709 and len(groups['excluded'])==17 and set(groups['original_193']).isdisjoint(groups['new_complement']) and set(groups['original_193'])|set(groups['new_complement'])==set(groups['all'])
    assert m.digest(HERE/'official-full-2026-cohorts.jsonl')==manifest['cohort_file_sha256']
    with gzip.open(m.h2.GAMES,'rt',encoding='utf8') as f:dates={g['source_fixture_id']:g['date'] for g in map(json.loads,f)}
    with gzip.open(m.h2.PROJECTION,'rt',encoding='utf8') as f:base=m.h1.unique_index(map(json.loads,f))
    with gzip.open(m.h2.H1,'rt',encoding='utf8') as f:old_h1=m.h1.unique_index(map(json.loads,f))
    with gzip.open(HERE/'official-starter-only-2026.jsonl.gz','rt',encoding='utf8') as f:old_h2=m.h1.unique_index(map(json.loads,f))
    with gzip.open(HERE/'official-batting-and-starter-2026.jsonl.gz','rt',encoding='utf8') as f:old_both=m.h1.unique_index(map(json.loads,f))
    outputs={}
    for kind in ('official_batting_only','official_starter_only','official_batting_and_starter'):
        path=HERE/('official-full-2026-'+kind+'.jsonl.gz');assert m.digest(path)==manifest['output_sha256'][kind]
        with gzip.open(path,'rt',encoding='utf8') as f:r=list(map(json.loads,f))
        assert [x['fixture_id'] for x in r]==groups['all'] and m.hash_core(r)==manifest['unchanged_nonplayer_features_sha256']
        outputs[kind]={x['fixture_id']:x for x in r}
    for fid in groups['all']:
        a=base[fid];b=outputs['official_batting_only'][fid];c=outputs['official_starter_only'][fid];d=outputs['official_batting_and_starter'][fid]
        for side in ('home','away'):
            assert a['features'][side+'_starter_strength']==b['features'][side+'_starter_strength']
            assert a['features'][side+'_lineup_strength']==c['features'][side+'_lineup_strength']
            assert b['features'][side+'_lineup_strength']==d['features'][side+'_lineup_strength']
            assert c['features'][side+'_starter_strength']==d['features'][side+'_starter_strength']
            for player in b['features'][side+'_lineup_strength']['value']:
                assert all(dates[z['fixture_id']]==z['date'] and z['date']<dates[fid] for z in player['history'] or [])
            starter=c['features'][side+'_starter_strength']['value']
            assert all(dates[z['fixture_id']]==z['date'] and z['date']<dates[fid] for z in (starter or {}).get('appearances',[]))
        if fid in groups['original_193']:
            assert b['features']==old_h1[fid]['features'] and c['features']==old_h2[fid]['features'] and d['features']==old_both[fid]['features']


def test_frozen_scalar_shapes_and_missing_counts():
    import hashlib
    adapter=pathlib.Path(r'C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\kbo-player-model-20261103\experiments\kbo-player-model-20261103\adapter.py')
    if not adapter.exists():pytest.skip('external frozen adapter unavailable')
    assert hashlib.sha256(adapter.read_bytes()).hexdigest()=='136a7c96624531010c6e247cc22fc6e5452680e8563a2f94e52a64e19c4a1ae0'
    sp=importlib.util.spec_from_file_location('kbo_frozen_scalar',adapter);a=importlib.util.module_from_spec(sp);sp.loader.exec_module(a)
    missing={}
    for mode,suffix in [('official_batting_only','lineup_strength'),('official_starter_only','starter_strength')]:
        with gzip.open(HERE/('official-full-2026-'+mode+'.jsonl.gz'),'rt',encoding='utf8') as f:rows=list(map(json.loads,f))
        values=[a.scalar(side+'_'+suffix,r['features'][side+'_'+suffix]['value']) for r in rows for side in ('home','away')]
        assert len(values)==1418;missing[mode]=sum(x is None for x in values)
    assert missing=={'official_batting_only':0,'official_starter_only':59}


def test_freshness_independently_recomputed_from_selected_histories():
    import statistics
    manifest=json.loads((HERE/'official-full-2026-manifest.json').read_text(encoding='utf8'))
    with gzip.open(m.h2.GAMES,'rt',encoding='utf8') as f:dates={g['source_fixture_id']:g['date'] for g in map(json.loads,f)}
    with gzip.open(HERE/'official-full-2026-official_batting_only.jsonl.gz','rt',encoding='utf8') as f:bat=list(map(json.loads,f))
    with gzip.open(HERE/'official-full-2026-official_starter_only.jsonl.gz','rt',encoding='utf8') as f:pit=list(map(json.loads,f))
    for name,rows in [('batters',bat),('pitchers',pit)]:
        gaps=[];missing=0
        for row in rows:
            day=date.fromisoformat(dates[row['fixture_id']])
            for side in ('home','away'):
                if name=='batters': histories=[x['history'] for x in row['features'][side+'_lineup_strength']['value']]
                else:
                    value=row['features'][side+'_starter_strength']['value'];histories=[value['appearances'] if value else None]
                for history in histories:
                    if not history:missing+=1
                    else:gaps.append((day-max(date.fromisoformat(x['date']) for x in history)).days)
        stat=manifest['freshness'][name]['overall'];ordered=sorted(gaps)
        assert (stat['observed'],stat['no_history'],stat['median_days'],stat['max_days'])==(len(gaps),missing,statistics.median(gaps),max(gaps))
        assert stat['p90_nearest_rank_days']==ordered[(9*len(ordered)+9)//10-1]
        for threshold in (14,30,60,90):assert stat[f'greater_than_{threshold}_days']==sum(x>threshold for x in gaps)
