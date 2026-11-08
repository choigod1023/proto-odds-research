import gzip,json
from pathlib import Path
import pytest
from scripts import research_kbo_daily_supplement as k

ROOT=Path(__file__).resolve().parents[1]/'experiments/kbo-official-record-gap-20261103'
P=ROOT/'naver-probe-v2/official-daily-probes'
S=ROOT/'samples'
RECORDS={x['official_game_id']:x for x in map(json.loads,gzip.open(ROOT/'artifacts/normalized-records.jsonl.gz','rt',encoding='utf8'))}


@pytest.mark.parametrize('pid,gid,file,bb',[
 ('66108','20260728WOLG0','66108.regular.raw.gz',1),
 ('64646','20260829WOOB0','64646.regular.raw.gz',1),
 ('50458','20261027HTSS0','50458.raw.gz',0)])
def test_three_real_official_daily_rows(pid,gid,file,bb):
    doc=k.soup(gzip.decompress((P/file).read_bytes()));game=RECORDS[gid]
    p=next(x for s in game['statistics']['batters'].values() for x in s if x['player_id']==pid)
    side=next(s for s in ('home','away') if any(x is p for x in game['statistics']['batters'][s]))
    schedule=json.loads(gzip.decompress((S/(gid+'.schedule.raw.gz')).read_bytes()))
    official=next(x for x in schedule['game'] if x['G_ID']==gid)
    opp=official['AWAY_NM' if side=='home' else 'HOME_NM'].strip()
    assert k.profile_name(doc)==p['name']
    merged=k.merge_batter(game,side,p,k.daily_rows(doc,game['series_id']),opp,schedule)
    assert merged['status']=='matched' and merged['BB']==bb
    assert merged['official_v1']=={key:p[key] for key in ('AB','H','R','RBI')}


def test_regular_public_form_preserves_state_without_credentials():
    doc=k.soup(gzip.decompress((P/'66108.raw.gz').read_bytes()));data,description=k.form_state(doc,0)
    assert description['year']=='2026' and description['series']=='0'
    assert data[description['series_field']]=='0' and data[description['year_field']]=='2026'
    assert 'Cookie' not in data and 'Authorization' not in data


def test_doubleheader_and_numeric_conflict_keep_fields_null():
    gid='20260728WOLG0';game=RECORDS[gid];p=next(x for x in game['statistics']['batters']['home'] if x['player_id']=='66108')
    sched=json.loads(gzip.decompress((S/(gid+'.schedule.raw.gz')).read_bytes()));official=next(x for x in sched['game'] if x['G_ID']==gid)
    doc=k.soup(gzip.decompress((P/'66108.regular.raw.gz').read_bytes()));daily=k.daily_rows(doc,0);opp=official['AWAY_NM'].strip()
    doubled={'code':sched['code'],'game':[official,dict(official,G_ID=gid[:-1]+'2')]}
    result=k.merge_batter(game,'home',p,daily,opp,doubled)
    assert result['status']=='ambiguous_or_missing_game' and result['BB'] is None
    reversed_home=dict(official,G_ID=gid[:-1]+'2',HOME_ID=official['AWAY_ID'],AWAY_ID=official['HOME_ID'])
    assert not k.unique_game({'code':sched['code'],'game':[official,reversed_home]},game)
    conflict=dict(p,AB=p['AB']+1);result=k.merge_batter(game,'home',conflict,daily,opp,sched)
    assert result['status']=='conflict' and result['BB'] is None and result['conflict']['AB']['daily']==p['AB']


def test_missing_id_and_invalid_numbers_are_never_zero_filled():
    game=RECORDS['20260728WOLG0'];p=dict(game['statistics']['batters']['home'][0],player_id=None)
    row=k.merge_batter(game,'home',p,[],'',{})
    assert row['status']=='missing_player_id' and row['BB'] is None
    for value in ('-1','','1.5'):
        with pytest.raises(ValueError):k.int_or_error(value)


def test_lock_excludes_other_collector(tmp_path):
    with k.lock(tmp_path):
        with pytest.raises(RuntimeError,match='collector_already_running'):
            with k.lock(tmp_path):pass


def test_full_supplement_has_stable_v1_row_indices_on_cache_miss(tmp_path,monkeypatch):
    import types
    game=RECORDS['20260728WOLG0'];sched=json.loads(gzip.decompress((S/'20260728WOLG0.schedule.raw.gz').read_bytes()))
    monkeypatch.setattr(k,'load_inputs',lambda *args:([game],{game['official_game_id']:sched}))
    def no_cache(*args,**kwargs):raise k.Stop('cache_miss_offline')
    monkeypatch.setattr(k.Fetcher,'request',no_cache)
    args=types.SimpleNamespace(v1='unused',lineups='unused',lineup_raw='unused',output=str(tmp_path),max_requests=1,offline=True,snapshot='out.jsonl.gz')
    k.run(args)
    row=json.loads(gzip.decompress((tmp_path/'out.jsonl.gz').read_bytes()))
    for side in ('home','away'):
        assert [p['v1_row_index'] for p in row['batters'][side]]==list(range(len(game['statistics']['batters'][side])))
        assert [p['v1_row_index'] for p in row['pitcher_id_supplement'][side]]==list(range(len(game['statistics']['pitchers'][side])))
        assert all(p['BB'] is None for p in row['batters'][side])


def test_team_reconciliation_preserves_both_sources_on_conflict():
    p={'status':'matched','BB':2,'HBP':0,'HR':1,'K':3,'2B':0,'3B':0,'daily_official_row':{'BB':2}}
    result=k.reconcile_team([p],[{'BB_plus_HBP':3,'HR':1,'K':3}])
    assert result['status']=='conflict' and result['daily']['BB_plus_HBP']==2 and result['opposing_pitchers']['BB_plus_HBP']==3
    assert p['status']=='team_total_conflict' and p['BB'] is None and p['daily_official_row']['BB']==2
    assert k.reconcile_team([p],[])['status']=='not_checkable_partial'


def test_merge_contract_rejects_target_day_and_no_missing_zero_fill():
    from scripts import research_kbo_daily_merge_contract as m
    import copy
    p={'name':'A','player_id':'1','AB':3,'H':1,'R':0,'RBI':0}
    original={'official_game_id':'g','fixture_id':'f','game_date':'20260801','completed':True,'status':'ok',
              'team_ids':{'home':'H','away':'A'},'statistics':{'batters':{'home':[p],'away':[]}}}
    added={'official_game_id':'g','fixture_id':'f','game_date':'20260801','source_v1_gzip_sha256':m.V1_SHA,
           'batters':{'home':[{'v1_row_index':0,'name':'A','player_id':'1','official_v1':{'AB':3,'H':1,'R':0,'RBI':0},'status':'matched','BB':1,'daily_evidence':{'sha256':'x','url':m.DAILY,'http_status':200,'query':{'playerId':'1'}}}],'away':[]}}
    assert m.prior_batter_history([original],[added],'20260801')['history']==[]
    r=m.prior_batter_history([original],[added],'20260802');assert len(r['history'])==1 and r['history'][0]['BB']==1 and 'outcome' not in r['history'][0]
    missing=copy.deepcopy(added);missing['batters']['home'][0].update(status='daily_not_captured',BB=None)
    r=m.prior_batter_history([original],[missing],'20260802');assert r['history']==[] and r['coverage']['excluded_missing_or_conflict']==1
    broken=copy.deepcopy(added);broken['batters']['home'][0]['v1_row_index']=9
    with pytest.raises(ValueError,match='batter_identity'):m.prior_batter_history([original],[broken],'20260802')
    broken=copy.deepcopy(added);broken['batters']['home'][0]['BB']=None
    with pytest.raises(ValueError,match='invalid_lagged_numeric'):m.prior_batter_history([original],[broken],'20260802')
    empty=copy.deepcopy(original);empty['statistics']['batters']['home'][0].update(AB=0,H=0)
    zero=copy.deepcopy(added);zero['batters']['home'][0]['official_v1'].update(AB=0,H=0);zero['batters']['home'][0]['BB']=0
    result=m.prior_batter_history([empty],[zero],'20260802')
    assert result['history']==[] and result['coverage']['excluded_zero_opportunities']==1


def test_pitcher_id_join_requires_matching_claim_and_official_profile():
    from scripts import research_kbo_daily_merge_contract as m
    p={'name':'P','player_id':None,'source_numeric_id_claim':'12345'}
    v={'official_game_id':'g','game_date':'20260801','statistics':{'pitchers':{'home':[p],'away':[]}}}
    a={'official_game_id':'g','game_date':'20260801','source_v1_gzip_sha256':m.V1_SHA,
       'pitcher_id_supplement':{'home':[{'v1_row_index':0,'name':'P','original_player_id':None,'source_numeric_id_claim':'12345','status':'verified_official_profile','supplement_player_id':'12345','profile_evidence':{'sha256':'x','url':m.PITCHER,'http_status':200,'query':{'playerId':'12345'}}}],'away':[]}}
    assert m.verified_pitcher_ids(v,a)[0]['player_id']=='12345'
    a['pitcher_id_supplement']['home'][0]['supplement_player_id']='99999'
    with pytest.raises(ValueError,match='invalid_profile_link'):m.verified_pitcher_ids(v,a)


def test_daily_bundle_preserves_response_and_postback_dependencies(tmp_path):
    import base64
    from scripts import pack_kbo_daily_supplement as pk
    cp=tmp_path/'cp';(cp/'raw').mkdir(parents=True);(cp/'requests').mkdir()
    raw=b'official public form';sha=k.sha(raw);(cp/'raw'/f'{sha}.gz').write_bytes(gzip.compress(raw,mtime=0))
    meta={'url':k.DAILY,'tag':'daily-get','query':{'playerId':'1'},'http_status':200,'sha256':sha,'bytes':len(raw)}
    (cp/'requests'/'a.json').write_text(json.dumps(meta),encoding='utf8')
    payload=''.join(json.dumps({'official_game_id':f'g{i}'})+'\n' for i in range(256)).encode()
    (cp/'out.gz').write_bytes(gzip.compress(payload,mtime=0))
    result=pk.pack(cp,'out.gz',tmp_path/'dest');assert result['games']==256
    entries=gzip.decompress((tmp_path/'dest'/'official-daily-raw.jsonl.gz').read_bytes()).splitlines()
    assert base64.b64decode(json.loads(entries[0])['raw_base64'])==raw
    with pytest.raises(FileExistsError):pk.pack(cp,'out.gz',tmp_path/'dest')


def test_daily_impossible_numeric_relationships_fail_closed():
    doc=k.soup(gzip.decompress((P/'50458.raw.gz').read_bytes()))
    table=next(t for t in doc.find_all('table') if 'BB' in [h.get_text(strip=True) for h in t.select('thead th')])
    headers=[h.get_text(strip=True) for h in table.select('thead th')];cells=table.select('tbody tr')[0].find_all(['td','th']);cells[headers.index('HR')].string='999'
    with pytest.raises(ValueError,match='invalid_batter_stat_relationship'):k.daily_rows(doc,5)


def test_merge_loader_requires_both_exact_input_hashes(tmp_path):
    import hashlib
    from scripts import research_kbo_daily_merge_contract as m
    v1=ROOT/'artifacts/normalized-records.jsonl.gz';s=tmp_path/'s.gz';s.write_bytes(gzip.compress(b'{}\n',mtime=0))
    games,supp=m.load_verified_sources(v1,s,hashlib.sha256(s.read_bytes()).hexdigest())
    assert len(games)==256 and supp==[{}]
    with pytest.raises(ValueError,match='input_sha_mismatch'):m.load_verified_sources(v1,s,'0'*64)


def test_unpack_restores_only_eligible_bytes_and_fails_closed(tmp_path):
    import base64
    from scripts.unpack_kbo_daily_cache import unpack
    raw=b'public source';h=k.sha(raw);meta={'method':'GET','url':k.DAILY,'query':{'playerId':'1'},'tag':'daily-get','sha256':h,'bytes':len(raw)}
    item={'metadata':meta,'raw_base64':base64.b64encode(raw).decode(),'eligible_for_supplement':True}
    excluded={'metadata':dict(meta,tag='excluded'),'raw_base64':base64.b64encode(raw).decode(),'eligible_for_supplement':False}
    daily=tmp_path/'daily.gz';daily.write_bytes(gzip.compress((json.dumps(item)+'\n'+json.dumps(excluded)+'\n').encode(),mtime=0))
    v1=tmp_path/'v1.gz';v1.write_bytes(gzip.compress((json.dumps({'kind':'official_schedule','metadata':meta,'raw_base64':base64.b64encode(raw).decode()})+'\n').encode(),mtime=0))
    result=unpack(daily,v1,tmp_path/'out');assert result=={'eligible_responses':1,'schedule_bodies':1,'network_requests':0}
    assert (tmp_path/'out'/'schedule'/h).read_bytes()==raw
    with pytest.raises(FileExistsError):unpack(daily,v1,tmp_path/'out')
