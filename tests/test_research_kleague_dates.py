from src.research_kleague_dates import compare, parse_raw


def row(**overrides):
    x={'year':'2025','sport':'sc','league':'K리그1','market_family':'승무패','market_label':'','n_way':'3',
       'home':'광주FC 1','away':'1 울산HDFC','date_text':'05.28(수) 19:30',
       'round':'64','game_no':'073','is_void':'False','odds':'2.95,3.20,2.06'}
    return {**x,**overrides}


def fixture(**overrides):
    x={'fixture_id':'official-96','day':'2025-05-28','kickoff':'2025-05-28T19:30:00',
       'scores':{'home':1,'away':1},'domestic_link':{'round':'64','game_no':'073','home':'광주FC','away':'울산HDFC','normalized_id':'n1'}}
    return {**x,**overrides}


def norm(**overrides):
    return {**{'id':'n1','home':'광주FC','away':'울산HDFC','day':'2025-05-28','kickoff':'2025-05-28T19:30:00','y':1},**overrides}


def test_complete_and_mismatched_fields():
    r=compare(fixture(),[parse_raw(row())],[norm()])
    assert r['date_raw'] and r['clock_raw'] and r['score_raw'] and r['outcome_normalized']
    r=compare(fixture(day='2025-05-29',scores={'home':2,'away':1}),[parse_raw(row())],[norm()])
    assert r['date_raw'] is False and r['score_raw'] is False and r['outcome_normalized'] is False
    assert r['utc_instant'] is None and r['same_market_price'] is None


def test_ambiguous_and_missing_never_match():
    raw=parse_raw(row())
    assert compare(fixture(),[raw,raw],[norm()])['status']=='ambiguous_or_missing'
    assert compare(fixture(),[raw],[])['status']=='ambiguous_or_missing'
    assert compare(fixture(),[raw],[norm(home='다른팀')])['status']=='identity_conflict'


def test_market_and_league_do_not_bleed():
    assert parse_raw(row(market_family='핸디캡',market_label='H -1.0')) is None
    assert parse_raw(row(league='K리그2')) is None
    assert parse_raw(row(home='광주FC',away='울산HDFC')) is None


def test_score_absence_and_timezone_stay_unknown():
    r=compare(fixture(scores=None),[parse_raw(row())],[norm()])
    assert r['score_raw'] is None and r['outcome_normalized'] is None
    assert r['utc_instant'] is None and r['cancel_postpone'] is None


def test_preserved_official_bytes_support_selected_rows():
    import json
    import re
    from html import unescape
    from pathlib import Path
    base=Path('experiments/verified-cohort-20261101/kleague1')
    fixtures=[json.loads(x) for x in (base/'fixtures.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    for f in fixtures:
        page=(base / f['source_evidence_id'][0]).read_text(encoding='utf-8')
        assert f['day'].replace('-','/') in page
        game_section=page.split('<div class="game-info">',1)[1].split('<div class="match-contents',1)[0]
        assert game_section.index(f['home']) < game_section.index(f['away'])
        assert f['kickoff'][11:16] in page
    news=(base/'news-93724.html').read_text(encoding='utf-8')
    text=re.sub(r'\s+',' ',unescape(re.sub(r'<[^>]+>',' ',news)))
    assert '하나은행 K리그1 2025' in text and '5월 28일 광주 원정(1대1 무)' in text
