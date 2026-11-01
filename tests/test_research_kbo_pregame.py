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


def test_historical_article_files_are_consistent_and_not_promoted():
    from pathlib import Path
    import hashlib
    import gzip
    root=Path(__file__).resolve().parents[1]/'experiments/verified-cohort-20261101/kbo'
    evidence=[json.loads(s) for s in (root/'evidence.jsonl').read_text(encoding='utf8').splitlines()]
    fixtures=[json.loads(s) for s in (root/'fixtures.jsonl').read_text(encoding='utf8').splitlines()]
    log=json.loads((root/'article-get-log.json').read_text(encoding='utf8'))
    assert len(log)==6 and all(x['status']==200 for x in log)
    for item in log:
        name=('kbo-photo-'+item['id'] if item['id'].isdigit() else item['id'])+'.html'
        assert hashlib.sha256(gzip.decompress((root/'raw'/(name+'.gz')).read_bytes())).hexdigest()==item['sha256']
    claims=[x for x in evidence if x.get('classification')=='historical_claimed_before']
    assert len(claims)==2 and len(fixtures)==4 and not any(x['eligible'] for x in fixtures)
    assert sum(len(x['lineup_claims']) for x in claims)==18
    assert all(not player['official_identity_verified'] for x in claims for player in x['lineup_claims'])
    assert all(not x['prospective_received_before'] and x['independent_archive'] is None for x in claims)
    unknown=[x for x in evidence if x.get('classification')=='pregame_article_claim_time_unverified']
    assert len(unknown)==1 and unknown[0]['published_claim'] is None


def test_claimed_publications_before_recorded_kickoffs_and_sources_contain_lineups():
    from pathlib import Path
    from datetime import datetime
    import gzip
    root=Path(__file__).resolve().parents[1]/'experiments/verified-cohort-20261101/kbo'
    evidence=[json.loads(s) for s in (root/'evidence.jsonl').read_text(encoding='utf8').splitlines()]
    fixtures=[json.loads(s) for s in (root/'fixtures.jsonl').read_text(encoding='utf8').splitlines()]
    for e in evidence[1:3]:
        f=next(x for x in fixtures if x.get('game_id_postgame_source')==e['game_id_postgame_source'])
        kickoff=datetime.fromisoformat(f['date']+'T'+f['scheduled_time_claim_kst']+':00+09:00')
        assert datetime.fromisoformat(e['published_claim'])<kickoff
        assert not f['official_result_independently_verified'] and not f['price_receipt_before']
        article_id=e['article_kbo_url'].split('=')[-1]
        article=gzip.decompress((root/'raw'/f'kbo-photo-{article_id}.html.gz').read_bytes()).decode('utf8')
        assert all(x['name_claim'] in article for x in e['lineup_claims'])
    assert evidence[-1]['classification']=='pregame_article_claim_time_unverified'
