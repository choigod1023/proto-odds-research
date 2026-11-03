import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

BASE=Path('experiments/kleague1-official-lineup-20261103')
spec=importlib.util.spec_from_file_location('kleague_lineup_collector',BASE/'collector.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)


def pair(year,gid):
    page=gzip.decompress((BASE/f'raw/match-{year}-{gid}.html.gz').read_bytes())
    info=(BASE/f'raw/info-{year}-{gid}.json').read_bytes()
    return page,info


@pytest.mark.parametrize('year,gid,score,benches',[(2023,1,(2,1),(7,7)),(2024,1,(1,0),(9,9)),(2025,96,(1,1),(9,9))])
def test_actual_official_fixtures(year,gid,score,benches):
    page,info=pair(year,gid);r=mod.parse_pair(page,info,year,gid)
    assert (r['scores']['home'],r['scores']['away'])==score
    assert [len(x['starters']) for x in r['lineups']]==[11,11]
    assert tuple(len(x['bench']) for x in r['lineups'])==benches
    assert all(x['formation'] and all(p['player_id'] and p['position'] for p in x['starters']) for x in r['lineups'])
    assert r['announced_at'] is None and r['actual_pregame_receipt'] is None and r['actual_pregame_receipt_verified'] is False
    assert r['substitutions'] and all(e['playerId'] and e['playerId2'] for e in r['substitutions'])


def test_wrong_identity_and_incomplete_lineup_rejected():
    page,info=pair(2025,96)
    with pytest.raises(ValueError,match='fixture identity'):mod.parse_pair(page,info,2025,95)
    soup=BeautifulSoup(page.decode('utf8'),'html.parser')
    soup.select_one('#tab02 .match-lineup-player table tr[onclick*=playerDetailPop]').decompose()
    with pytest.raises(ValueError,match='missing or duplicated starters'):mod.parse_pair(str(soup).encode('utf8'),info,2025,96)
    d=json.loads(info);d['data']['firstHalf'][0]['gameId']=95
    with pytest.raises(ValueError,match='event fixture mismatch'):mod.parse_pair(page,json.dumps(d).encode('utf8'),2025,96)


def test_cache_does_not_request_again(monkeypatch):
    def unexpected(*args,**kwargs):raise AssertionError('unexpected network')
    monkeypatch.setattr(mod.requests,'request',unexpected)
    mod.capture([(2023,1),(2024,1),(2025,96)],3)


def test_hashes_and_coverage():
    entries=json.loads((BASE/'requests.json').read_text(encoding='utf8'))
    assert len(entries)==6 and all(e['status']==200 and e.get('received_at') for e in entries)
    for e in entries:
        b=(BASE/e['raw_file']).read_bytes()
        if e['raw_file'].endswith('.gz'):b=gzip.decompress(b)
        assert hashlib.sha256(b).hexdigest()==e['sha256']
    m=json.loads((BASE/'manifest.json').read_text(encoding='utf8'))
    assert (m['parsed_fixtures'],m['player_starters'],m['bench_players'],m['formation_sides'],m['substitution_events'])==(3,66,50,6,27)
    assert m['historical_actual_pregame_receipt_verified']==0


def test_crosswalk_distinguishes_absent_duplicate_and_score_mismatch():
    spec=importlib.util.spec_from_file_location('lineup_crosswalk',BASE/'compare_oof.py')
    cross=importlib.util.module_from_spec(spec);spec.loader.exec_module(cross)
    f={'fixture_id':'id','day':'2025-01-01','lineups':[{'team_id':'H'},{'team_id':'A'}],'scores':{'home':1,'away':0}}
    r={'id':'id','day':'2025-01-01','home_id':'H','away_id':'A','hg':1,'ag':0,'prices':['2','3','4']}
    assert cross.compare([f],[r])[0]['identity_date_score_match'] is True
    assert cross.compare([f],[])[0]['identity_date_score_match'] is None
    assert cross.compare([f],[r,r])[0]['matching_ids']==2
    assert cross.compare([f],[{**r,'hg':2}])[0]['identity_date_score_match'] is False


def test_restricted_response_prevents_retries(tmp_path,monkeypatch):
    log=tmp_path/'requests.json';log.write_text(json.dumps([{'status':403}]),encoding='utf8')
    monkeypatch.setattr(mod,'LOG',log)
    monkeypatch.setattr(mod.requests,'request',lambda *a,**k: pytest.fail('must not retry'))
    with pytest.raises(ValueError,match='access restriction'):mod.capture([(2026,1)],1)


def test_crosswalk_rejects_incomplete_or_invalid_prices():
    spec=importlib.util.spec_from_file_location('lineup_crosswalk2',BASE/'compare_oof.py')
    cross=importlib.util.module_from_spec(spec);spec.loader.exec_module(cross)
    f={'fixture_id':'id','day':'2025-01-01','lineups':[{'team_id':'H'},{'team_id':'A'}],'scores':{'home':1,'away':0}}
    row={'id':'id','day':'2025-01-01','home_id':'H','away_id':'A','hg':1,'ag':0,'prices':['2','bad','4']}
    assert cross.compare([f],[row])[0]['complete_threeway_price'] is False
