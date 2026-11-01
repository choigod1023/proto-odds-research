import copy
import json
import pytest
from src.research_kbo_pregame import audit, evidence_tier


def sample():
    doc={'data':{'gameScore':{'gameDate':20250322,'gmKey':'20250322HHKT0','homeKey':'KT','visitKey':'HH','gtime':'14:00','endFlag':'3','hscore':3,'vscore':4},'homeLineup':[{'playerName':'test','curHra':0.99}]}}
    rows=[{'year':'2025','round':'35','game_no':'119','league':'KBO','sport':'bs','market_family':'승패','market_tag':'hm','market_label':'','n_way':'2','date_text':'03.22(토) 14:00','home':'KT 3','away':'4 한화','odds':'1.72,1.80'}]
    spec={'game_id':'20250322HHKT0','home_code':'KT','away_code':'HH','home_name':'KT','away_name':'한화','round':'35','game_no':'119','kickoff':'2025-03-22T14:00:00+09:00','decision':'2025-03-22T13:30:00+09:00','received_at':'2026-11-01T00:57:42+00:00'}
    return doc,rows,spec


def run(doc,rows,spec): return audit(json.dumps(doc,ensure_ascii=False).encode(),rows,spec)


def test_postgame_not_retroactively_eligible():
    doc,rows,spec=sample(); r=run(doc,rows,spec)
    assert r['team_official_result_linked'] and not r['eligible'] and r['evidence_tier']=='postgame_only'
    assert not r['lineup_pregame_confirmed'] and not r['price_received_before']
    assert 'curHra' not in str(r) and 'test' not in str(r)


def test_time_boundaries_and_claims():
    assert evidence_tier('2025-03-22T13:30:00+09:00','2025-03-22T13:30:00+09:00','2025-03-22T14:00:00+09:00')=='prospective_received_before'
    assert evidence_tier('2026-01-01T00:00:00+00:00','2025-03-22T13:30:00+09:00','2025-03-22T14:00:00+09:00','2025-03-22T12:00:00+09:00')=='historical_claimed_before'
    assert evidence_tier('2026-01-01T00:00:00+00:00','2025-03-22T13:30:00+09:00','2025-03-22T14:00:00+09:00','2025-03-22T12:00:00+09:00','2025-03-23T12:00:00+09:00')=='postgame_only'
    with pytest.raises(ValueError): evidence_tier('2025-03-22T13:30:00','2025-03-22T13:30:00+09:00','2025-03-22T14:00:00+09:00')
    d,r,s=sample(); s['decision']='2025-03-22T13:30:01+09:00'
    with pytest.raises(ValueError): run(d,r,s)


def test_market_identity_and_incomplete_prices_fail_closed():
    d,r,s=sample()
    for field,value in [('market_family','핸디캡'),('market_label','H -1.5'),('n_way','3'),('odds','1.72'),('odds','NaN,1.8'),('date_text','03.23(일) 14:00')]:
        changed=copy.deepcopy(r); changed[0][field]=value
        with pytest.raises(ValueError): run(d,changed,s)
    with pytest.raises(ValueError): run(d,r+r,s)


def test_future_label_and_future_form_never_become_features():
    d,r,s=sample(); before=run(d,r,s)
    d['data']['gameScore']['hscore']=99; d['data']['homeLineup'][0]['curHra']=999
    after=run(d,r,s)
    assert after['team_official_result_linked'] is False
    for key in ['market','odds','eligible','lineup_pregame_confirmed','evidence_tier','price_received_before']:
        assert before[key]==after[key]
    assert 'curHra' not in str(after)


def test_identity_mismatch_fails():
    d,r,s=sample(); d['data']['gameScore']['gmKey']='20250322HHKT1'
    with pytest.raises(ValueError): run(d,r,s)
