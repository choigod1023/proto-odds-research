import gzip, importlib.util, json
from datetime import date
from pathlib import Path
import pytest
from bs4 import BeautifulSoup

ROOT=Path('experiments/npb-official-information-20261103')
spec=importlib.util.spec_from_file_location('npb_official_capture',ROOT/'collect.py')
c=importlib.util.module_from_spec(spec); spec.loader.exec_module(c)

def test_baseball_innings_are_outs_not_decimals():
    assert c.innings_outs('5 .2')==17
    assert c.innings_outs('5 +')==15
    assert c.innings_outs('0 +')==0
    with pytest.raises(ValueError): c.innings_outs('5.3')

def test_substitutes_are_not_mistaken_for_starting_nine():
    rows=[]
    for i in range(1,10):
        rows.append(f'<tr><td>{i}</td><td>(中)</td><td><a href="/bis/players/{100+i}.html">p{i}</a></td></tr>')
        if i==1: rows.append('<tr><td></td><td>走中</td><td><a href="/bis/players/999.html">sub</a></td></tr>')
    html='<h3>読売ジャイアンツ</h3><table id="tablefix_b_b"><tr><th></th><th>守備</th><th>選手</th></tr>'+''.join(rows)+'</table>'
    starters,sub=c.batting(BeautifulSoup(html,'html.parser'),'b','g')
    assert [x['order'] for x in starters]==list(range(1,10))
    assert all(x['official_player_id']!='999' for x in starters)
    assert sub[0]['official_player_id']=='999'
    with pytest.raises(ValueError): c.batting(BeautifulSoup(html.replace('(中)','中',1),'html.parser'),'b','g')

def test_source_identity_and_strict_availability():
    with pytest.raises(ValueError): c.parse_game('2026年3月27日'.encode(), 'https://npb.jp/scores/2026/0328/g-c-01/box.html', date(2026,3,27))
    assert sum(1 for _ in c.dates())==40

def test_all_saved_response_bytes_and_parsed_records():
    manifest=ROOT/'manifest.json'
    if not manifest.exists(): pytest.skip('real collection has not completed')
    metas=list((ROOT/'raw').glob('*.meta.json'))
    for m in metas:
        meta=json.loads(m.read_text(encoding='utf8')); body=gzip.decompress(m.with_name(m.name.replace('.meta.json','.body.gz')).read_bytes())
        assert c.sha(body)==meta['body_sha256']
    records=[json.loads(x) for x in (ROOT/'postgame-records.jsonl').read_text(encoding='utf8').splitlines()]
    done=[x for x in records if x['state']=='completed']
    for game in done:
        assert game['strict_pit_available'] is False and game['availability']=='retrospective_postgame_only'
        assert game['received_at'][:10] > game['date']
        assert set(game['postgame']['official_final_score'])=={'home','away'}
        for side in ('home','away'):
            data=game['postgame'][side]
            assert len(data['actual_starting_lineup'])==9 and data['actual_pitchers'][0]['role']=='starter'
            assert all(x['official_player_id'].isdigit() for x in data['actual_starting_lineup'])
            assert all(p['innings_outs']>=0 for p in data['actual_pitchers'])
    assert len(done)==json.loads(manifest.read_text(encoding='utf8'))['completed']
    assert sum(g['completion_type']=='called_game' for g in done)==2

def test_actual_census_selection_and_request_pacing():
    from collections import defaultdict
    from datetime import datetime
    if not (ROOT/'manifest.json').exists(): pytest.skip('real collection has not completed')
    census=[json.loads(x) for x in (ROOT/'fixture-census.jsonl').read_text(encoding='utf8').splitlines()]
    records=[json.loads(x) for x in (ROOT/'postgame-records.jsonl').read_text(encoding='utf8').splitlines()]
    months=defaultdict(list)
    for day in census:
        assert day['status']=='ok'
        months[day['date'][:7]].extend((day['date'],p) for p in day['fixture_paths'])
    expected=[p for month in sorted(months) for _,p in sorted(set(months[month]))[:20]]
    assert [g['official_game_id'] for g in records]==expected
    log=[json.loads(x) for x in (ROOT/'attempts.jsonl').read_text(encoding='utf8').splitlines()]
    starts=[datetime.fromisoformat(x['started_at']) for x in log if x['kind']=='request_started']
    assert len(starts)<=500 and all((b-a).total_seconds()>=1 for a,b in zip(starts,starts[1:]))
    assert all(x.get('http_status')==200 for x in log if x['kind']=='request_completed')

def test_actual_price_provenance_and_no_pit_claim():
    if not (ROOT/'price-manifest.json').exists(): pytest.skip('price audit has not completed')
    quotes=[json.loads(x) for x in (ROOT/'retrospective-price-links.jsonl').read_text(encoding='utf8').splitlines()]
    for q in quotes:
        source=ROOT/'price-raw'/(q['source_name']+'.body.gz')
        assert c.sha(gzip.decompress(source.read_bytes()))==q['source_sha256']
        assert q['strict_pit_verified'] is False and q['settlement_rules_verified'] is False
        assert q['product']['period']=='unverified_from_response'

def test_source_manifest_and_handoff_hashes():
    if not (ROOT/'source-manifest.json').exists(): pytest.skip('source manifest has not been generated')
    manifest=json.loads((ROOT/'source-manifest.json').read_text(encoding='utf8'))
    assert len(manifest['source_documents'])==manifest['source_count']
    for source in manifest['source_documents']:
        compressed=(ROOT/source['body_file']).read_bytes()
        assert c.sha(compressed)==source['compressed_sha256']
        assert c.sha(gzip.decompress(compressed))==source['body_sha256']
        assert c.sha((ROOT/source['metadata_file']).read_bytes())==source['metadata_sha256']
    for name,expected in manifest['derived_sha256'].items():
        assert c.sha((ROOT/name).read_bytes())==expected
    handoff=json.loads((ROOT/'handoff.json').read_text(encoding='utf8'))
    assert c.sha((ROOT/'source-manifest.json').read_bytes())==handoff['source_manifest_sha256']

def test_real_source_rows_match_saved_starters_pitchers_and_scores():
    import re
    if not (ROOT/'manifest.json').exists(): pytest.skip('real collection has not completed')
    records=[json.loads(x) for x in (ROOT/'postgame-records.jsonl').read_text(encoding='utf8').splitlines()]
    for g in records:
        if g['state']!='completed':continue
        raw,meta=c.load(g['source_url']); soup=BeautifulSoup(raw,'html.parser')
        assert meta['body_sha256']==g['source_sha256']
        for side,prefix in (('away','t'),('home','b')):
            table=soup.select_one('#tablefix_'+prefix+'_b'); raw_starters={}
            for tr in table.find_all('tr')[1:]:
                cells=tr.find_all(['th','td'],recursive=False)
                if len(cells)<3:continue
                order=cells[0].get_text(strip=True)
                if re.fullmatch('[1-9]',order):
                    a=cells[2].find('a',href=True); assert a is not None
                    raw_starters[int(order)]=re.fullmatch(r'/bis/players/(\d+)\.html',a['href'])[1]
                    assert re.match(r'^[（(][^）)]+[）)]',cells[1].get_text(' ',strip=True))
            saved=g['postgame'][side]['actual_starting_lineup']
            assert {x['order']:x['official_player_id'] for x in saved}==raw_starters and len(raw_starters)==9
            pitchers=soup.select_one('#tablefix_'+prefix+'_p'); header=[x.get_text(' ',strip=True) for x in pitchers.find('tr').find_all(['th','td'],recursive=False)];ix=header.index('投手')
            first=next((cells[ix] for tr in pitchers.find_all('tr')[1:] if len(cells:=tr.find_all(['th','td'],recursive=False))==len(header) and cells[ix].get_text(strip=True)),None)
            assert first is not None and first.find('a',href=True)['href'].endswith('/'+g['postgame'][side]['actual_pitchers'][0]['official_player_id']+'.html')
        table=soup.select_one('#tablefix_ls'); rows=[[x.get_text(' ',strip=True) for x in tr.find_all(['th','td'],recursive=False)] for tr in table.find_all('tr')]; col=rows[0].index('計')
        assert g['postgame']['official_final_score']=={'away':int(rows[1][col]),'home':int(rows[2][col])}

def test_price_join_requires_correct_market_fixture_and_complete_prices():
    import hashlib
    pspec=importlib.util.spec_from_file_location('npb_price_join',ROOT/'price_join.py')
    pj=importlib.util.module_from_spec(pspec);pspec.loader.exec_module(pj)
    from datetime import datetime,timezone,timedelta
    event={'state':'completed','date':'2026-03-27','home_team_id':'g','away_team_id':'c','official_game_id':'/scores/2026/0327/g-c-01/','actual_start_jst':'2026-03-27T18:00:00+09:00','postgame':{'official_final_score':{'home':3,'away':2}}}
    census=[{'date':'2026-03-27','fixture_paths':[event['official_game_id']]}]
    stamp=int(datetime(2026,3,27,18,tzinfo=timezone(timedelta(hours=9))).timestamp()*1000)
    raw={'leagueName':'NPB','betId':'2','betTypId':'2','betNm':'야구 승패','betTypNm':'일반 승패','gameDate':stamp,'homeId':'C6','awayId':'C2','winAllot':1.7,'loseAllot':2.1,'drawAllot':0,'winTxt':'승','loseTxt':'패','mchScore':'3:2','matchSeq':1,'gameReject':'0','buyReject':'0','live':'N'}
    def inputs(row):
        body=json.dumps({'compSchedules':{'keys':list(row),'datas':[list(row.values())]}},ensure_ascii=False).encode()
        return [('f',body,{'url':'https://www.betman.co.kr/','received_at':'2026-11-03T01:00:00+00:00','body_sha256':hashlib.sha256(body).hexdigest()})]
    matched,_,_=pj.match_price([event],census,inputs(raw))
    assert len(matched)==1 and matched[0]['result_score_agrees'] is True
    assert matched[0]['strict_pit_verified'] is False and matched[0]['product']['period']=='unverified_from_response'
    wrong={**raw,'betNm':'야구 핸디캡'}
    assert not pj.match_price([event],census,inputs(wrong))[0]
    bad={**raw,'loseAllot':0}
    assert not pj.match_price([event],census,inputs(bad))[0]
    assert not pj.match_price([event],census,inputs({**raw,'buyReject':'1'}))[0]
    assert not pj.match_price([event],census,inputs({**raw,'mchScore':'2:3'}))[0]
    double=[{'date':'2026-03-27','fixture_paths':[event['official_game_id'],'/scores/2026/0327/g-c-02/']}]
    assert not pj.match_price([event],double,inputs(raw))[0]
