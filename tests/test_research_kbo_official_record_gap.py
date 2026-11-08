import copy
import gzip
import json
from pathlib import Path
import pytest
from scripts import research_kbo_official_record_gap as k

HERE=Path(__file__).resolve().parents[1]/'experiments/kbo-official-record-gap-20261103/samples'
GAMES={x['official_game_id']:x for x in map(json.loads,(HERE/'lineups.jsonl').read_text(encoding='utf8').splitlines())}


def sample(gid):
    game=GAMES[gid];out=[]
    for kind in ('schedule','box','scroll'):
        meta=json.loads((HERE/f'{gid}.{kind}.json').read_text(encoding='utf8'))
        body=gzip.decompress((HERE/f'{gid}.{kind}.raw.gz').read_bytes())
        assert k.sha(body)==meta['sha256']
        out.append(json.loads(body))
    schedule=next(x for x in out[0]['game'] if x['G_ID']==gid)
    hi,pi=k.indexes(GAMES.values())
    return game,schedule,out[1],out[2],hi,pi


@pytest.mark.parametrize('gid',['20260728WOLG0','20260829WOOB0','20261027HTSS0'])
def test_three_real_official_boxscores_and_units(gid):
    game,schedule,box,scroll,hi,pi=sample(gid)
    result=k.normalize(game,schedule,json.dumps(box).encode(),json.dumps(scroll).encode(),hi,pi)
    assert result['status']=='ok' and result['historical_receipt_proven'] is False
    for side in ('home','away'):
        batters=result['statistics']['batters'][side]; pitchers=result['statistics']['pitchers'][side]
        assert sum(x['announced_lineup_match'] for x in batters)>=6
        assert sum(p['is_starter'] for p in pitchers)==1
        assert all(x['BB'] is None and x['HR'] is None and x['K'] is None for x in batters)
        assert all(p['BB'] is None and p['pitch_count'] is not None for p in pitchers)
        assert all('era' not in p for p in pitchers)
    if gid=='20260728WOLG0':
        starter=next(p for p in result['statistics']['pitchers']['away'] if p['is_starter'])
        assert (starter['outs'],starter['pitch_count'],starter['batters_faced'])==(15,96,24)
        home_starter=next(p for p in result['statistics']['pitchers']['home'] if p['is_starter'])
        assert home_starter['source_numeric_id_claim']=='55130' and home_starter['player_id']=='55130'
        assert result['outcome']=={'home_score':5,'away_score':3,'namespace':'postgame_label_never_current_target_feature'}


def test_official_team_earned_runs_can_differ_from_individual_sum():
    game,schedule,box,scroll,hi,pi=sample('20260729HTSS0')
    result=k.normalize(game,schedule,json.dumps(box).encode(),json.dumps(scroll).encode(),hi,pi)
    quality=result['quality']['pitchers']['home']
    assert (quality['team_earned_runs'],quality['individual_earned_runs_sum'],quality['team_equals_individual_earned_runs'])==(6,7,False)
    assert quality['team_equals_individual_outs'] is True


@pytest.mark.parametrize('value,outs',[('5.2',17),('5 2/3',17),('2/3',2),('1/3',1),('0/3',0),('0',0),('6',18)])
def test_baseball_innings_are_outs(value,outs): assert k.innings_outs(value)==outs


@pytest.mark.parametrize('value',['-1','5.3','1 3/3','1.5','garbage'])
def test_invalid_innings_rejected(value):
    with pytest.raises(ValueError):k.innings_outs(value)


def test_numeric_blanks_and_negative_guard():
    assert k.number('&nbsp;') is None and k.number('0')==0
    with pytest.raises(ValueError):k.number('-1')


def test_winning_reliever_is_not_promoted_to_starter():
    game,schedule,box,scroll,hi,pi=sample('20260728WOLG0')
    altered=copy.deepcopy(box);other=copy.deepcopy(scroll)
    target=altered['tables'][3];st=json.loads(other['arrPitcher'][0]['table'])
    result_index=k.header(target).index('결과')
    target['rows'][1]['row'][result_index]['Text']='승';st['rows'][1]['row'][result_index]['Text']='승'
    players,_=k.pitcher_table(target,st,game,'away',pi)
    assert players[1]['result_label']=='승' and players[1]['is_starter'] is False


def test_archived_pitcher_id_requires_same_official_numeric_code():
    game,schedule,box,scroll,hi,pi=sample('20260728WOLG0')
    target=box['tables'][4];table=json.loads(scroll['arrPitcher'][1]['table'])
    name=table['rows'][1]['row'][0]['Text'].strip();code=target['rows'][1]['row'][0]['Text'].strip()
    assert code.isdigit()
    key=(game['home_team_id'],name)
    players,_=k.pitcher_table(target,table,game,'home',{}, {key:{code}})
    assert players[1]['player_id']==code
    assert players[1]['player_id_basis']=='official_numeric_and_prior_archive_team_name_crossmatch'
    players,_=k.pitcher_table(target,table,game,'home',{}, {key:{'99999'}})
    assert players[1]['player_id'] is None


def test_archived_identity_index_rejects_post_window_players(tmp_path):
    game,schedule,box,scroll,hi,pi=sample('20260728WOLG0')
    raw=gzip.decompress((HERE/'20260728WOLG0.schedule.raw.gz').read_bytes())
    (tmp_path/game['schedule_evidence']['sha256']).write_bytes(raw)
    def old(day,pid):
        return {'date':day,'home':schedule['HOME_NM'],'away':schedule['AWAY_NM'],
                'data':{'home':[{'name':'A','pcode':pid}],'away':[]}}
    archive={'20260726AAAA02026':old('2026-07-26','12345'),
             '20260801AAAA02026':old('2026-08-01','99999'),
             '20260000AAAA02026':old('','55555')}
    index=k.archived_pitcher_index([game],tmp_path,archive)
    assert index[(game['home_team_id'],'A')]=={'12345'}


def test_identity_header_and_totals_fail_closed():
    game,schedule,box,scroll,hi,pi=sample('20260728WOLG0')
    wrong=dict(schedule,HOME_ID='ZZ')
    with pytest.raises(ValueError,match='schedule_mismatch'):k.normalize(game,wrong,json.dumps(box).encode(),json.dumps(scroll).encode(),hi,pi)
    changed=copy.deepcopy(box);changed['tables'][1]['rows'][0]['row'][-5]['Text']='999'
    with pytest.raises(ValueError,match='hitter_total_mismatch'):k.normalize(game,schedule,json.dumps(changed).encode(),json.dumps(scroll).encode(),hi,pi)
    changed=copy.deepcopy(box);changed['tables'][3]['headers'][0]['row'][8]['Text']='타자'
    with pytest.raises(ValueError,match='pitcher_headers'):k.normalize(game,schedule,json.dumps(changed).encode(),json.dumps(scroll).encode(),hi,pi)


def test_prior_only_excludes_target_and_outcomes():
    records=[{'game_date':'20260801','completed':True,'status':'ok','statistics':{'x':1},'outcome':{'score':100}},
             {'game_date':'20260802','completed':True,'status':'ok','statistics':{'x':2}},
             {'game_date':'20260730','completed':False,'status':'ok','statistics':{'x':3}}]
    assert k.prior_only(records,'20260802')==[{'x':1}]
    with pytest.raises(ValueError,match='target_date'):k.prior_only(records,'20261345')


def test_collector_lock_excludes_competing_writer(tmp_path):
    with k.capture_lock(tmp_path):
        with pytest.raises(RuntimeError,match='collector_already_running'):
            with k.capture_lock(tmp_path):pass


def test_offline_cache_miss_never_requests_network(tmp_path,monkeypatch):
    fetcher=k.Fetcher(tmp_path,1,offline=True)
    monkeypatch.setattr(fetcher.s,'post',lambda *args,**kwargs:pytest.fail('network attempted'))
    with pytest.raises(k.Stop,match='cache_miss_offline'):
        fetcher.post(k.BOX,{'gameId':'not-cached'})


def test_pack_preserves_exact_response_bytes_and_prevents_overwrite(tmp_path,monkeypatch):
    import base64
    from scripts import pack_kbo_official_record_gap as pk
    cp=tmp_path/'checkpoint';(cp/'raw').mkdir(parents=True);(cp/'requests').mkdir()
    body=b'official response\r\n';h=k.sha(body)
    (cp/'raw'/f'{h}.gz').write_bytes(gzip.compress(body,mtime=0))
    meta={'url':k.BOX,'params':{'gameId':'g0'},'http_status':200,'received_at':'now','sha256':h,'bytes':len(body)}
    (cp/'requests'/'one.json').write_text(json.dumps(meta),encoding='utf8')
    (cp/'records.jsonl').write_text(json.dumps({'official_game_id':'g0','status':'ok'})+'\n',encoding='utf8')
    sched=tmp_path/'schedule';sched.mkdir();(sched/h).write_bytes(body)
    sm={'url':'https://official/schedule','params':{'date':'20260801'},'sha256':h,'bytes':len(body)}
    games=[{'official_game_id':f'g{i}','game_date':'20260801','schedule_evidence':sm} for i in range(256)]
    source=tmp_path/'lineups.jsonl';source.write_text(''.join(json.dumps(x)+'\n' for x in games),encoding='utf8')
    monkeypatch.setattr(pk,'SOURCE_SHA',k.sha(source.read_bytes()))
    result=pk.pack(cp,source,sched,tmp_path/'out')
    assert result['record_count']==1 and result['raw_response_entries']==2
    lines=gzip.decompress((tmp_path/'out'/'raw-responses.jsonl.gz').read_bytes()).splitlines()
    assert all(base64.b64decode(json.loads(line)['raw_base64'])==body for line in lines)
    with pytest.raises(FileExistsError):pk.pack(cp,source,sched,tmp_path/'out')
