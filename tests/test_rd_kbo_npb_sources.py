"""Offline real-cache and adversarial identity/clock regressions."""
import copy
import base64
from datetime import date
import gzip
import json

import pytest
from bs4 import BeautifulSoup

from scripts.rd_kbo_npb_sources import (OUT, Fetch, aggregate, dt, npb_links,
    parse_kbo, parse_npb, target_match, team_code)


def cached(fragment, data_date=None):
    for p in (OUT/'raw').glob('*.meta.json'):
        m=json.loads(p.read_text(encoding='utf8'))
        if fragment in m['url'] and (data_date is None or (m.get('data') or {}).get('gamedate')==data_date):
            return gzip.decompress((OUT/m['raw_path']).read_bytes()).decode(m['encoding']),m
    raise AssertionError('required real public sample missing')


def test_real_kbo_summary_pitch_count_is_not_batters_faced():
    html,_=cached('/Game/SummaryView.aspx','20260328')
    games=parse_kbo(html,date(2026,3,28))
    assert len(games)==5
    game=next(g for g in games if g['game_id']=='20260328KTLG0')
    assert game['home_id']=='LG' and game['away_id']=='KT'
    starter=game['pitchers'][0]
    assert starter['pitch_count']==88 and starter['batters_faced']==24
    relievers=[p for p in game['pitchers'] if p['role']=='reliever']
    assert relievers and all(p['pitch_count'] is None for p in relievers)
    assert not game['lineups'] and game['end_time'] is None


def test_real_kbo_wrong_date_rejected():
    html,_=cached('/Game/SummaryView.aspx','20260328')
    with pytest.raises(ValueError,match='date_mismatch'):
        parse_kbo(html,date(2026,3,29))


def test_kbo_ambiguous_doubleheader_does_not_guess_board_order():
    import re
    html,_=cached('/Game/SummaryView.aspx','20260328')
    soup=BeautifulSoup(html,'lxml')
    node=soup.select_one('#__VIEWSTATE')
    raw=base64.b64decode(node['value'])
    pairs=[seg for seg in re.findall(rb'<Table1\s[^>]*>.*?</Table1>',raw,re.S)
           if b'<G_DT>' in seg and b'<G_ID>20260328KTLG0</G_ID>' in seg]
    assert len(pairs)==2
    raw+=b''.join(p.replace(b'20260328KTLG0',b'20260328KTLG1') for p in pairs)
    node['value']=base64.b64encode(raw).decode()
    with pytest.raises(ValueError,match='doubleheader_identity_ambiguous'):
        parse_kbo(str(soup),date(2026,3,28))


def test_real_npb_nested_innings_cells_do_not_shift_pitch_column():
    html,m=cached('/0329/c-d-03/box.html')
    game=parse_npb(html,m['url'],date(2026,3,29))
    assert [(p['pitch_count'],p['batters_faced']) for p in game['pitchers']]==[(104,31),(95,28)]
    assert game['end_time']=='2026-03-29T15:41:00+09:00'
    assert all(len(x)==9 for x in game['lineups'].values())
    assert sum(p['role']=='reliever' for p in game['pitchers'])==0


def test_real_npb_date_and_team_path_mismatch_rejected():
    html,m=cached('/0329/c-d-03/box.html')
    with pytest.raises(ValueError,match='date_mismatch'):
        parse_npb(html,m['url'],date(2026,3,30))
    with pytest.raises(ValueError,match='team_heading_mismatch'):
        parse_npb(html,m['url'].replace('c-d-03','d-c-03'),date(2026,3,29))


def test_daily_cardinality_only_exact_day_first_team_and_preserves_doubleheaders():
    h=''.join(f'<a href="{p}"></a>' for p in ('/scores/2026/0329/c-d-01/','/scores/2026/0329/c-d-02/',
        '/scores/2026/0328/c-d-01/','/farm/2026/0329/c-d-01/','https://evil.example/scores/2026/0329/c-d-01/'))
    links=npb_links(h,date(2026,3,29))
    assert len(links)==2 and links[0].endswith('c-d-01/box.html') and links[1].endswith('c-d-02/box.html')


def make_game(gid='old1',day='2026-03-28',end=None):
    return dict(league='KBO',game_id=gid,fact_date=day,home_id='LG',away_id='KT',end_time=end,
        source_sha256='a'*64,observed_at='2026-10-30T00:00:00+00:00',pitchers=[
            dict(team_id='LG',player_id='starter',role='starter',pitch_count=88),
            dict(team_id='LG',player_id='relief',role='reliever',pitch_count=None)])


def make_row():
    return dict(cohort='retrospective_year_strict__claimed_time_only',event_id='target',league='KBO',home='LG 트윈스',away='KT 위즈',
        kickoff='2026-03-31T18:30:00+09:00',decision_at='2026-03-31T18:00:00+09:00')


def test_target_and_future_stats_excluded_and_same_game_deduplicated():
    row=make_row()
    old=make_game()
    values,evidence,_=aggregate(row,[old,copy.deepcopy(old),make_game('target'),make_game('future','2026-04-01')])
    assert values['home_observed_starter_pitch_count_prior3_source_dates']==88
    assert values['home_observed_relief_appearances_prior3_source_dates']==1
    assert all(e['source_game_ids']==['old1'] for e in evidence.values())


def test_doubleheader_is_two_appearances_same_player_not_deduplicated_by_date():
    values,_,_=aggregate(make_row(),[make_game('dh1'),make_game('dh2')])
    assert values['home_observed_relief_appearances_prior3_source_dates']==2
    assert values['home_observed_relievers_ge2_appearances_prior3_source_dates']==1
    assert values['home_observed_starter_pitch_count_prior3_source_dates']==176


def test_npb_actual_completion_clock_and_japan_timezone():
    row=make_row();row.update(league='NPB',home='주니치',away='요미우리',kickoff='2026-03-31T18:30:00+09:00')
    game=make_game(end='2026-03-28T20:00:00+09:00');game.update(league='NPB',home_id='d',away_id='g')
    for p in game['pitchers']:
        p.update(team_id='d',pitch_count=21)
    v,e,_=aggregate(row,[game])
    assert v['home_observed_relief_pitch_count_prior3_source_dates']==21
    assert all(x['max_fact_time']==game['end_time'] and x['fact_date_timezone']=='Asia/Tokyo' for x in e.values())
    game['end_time']='2026-03-31T18:01:00+09:00'
    assert not aggregate(row,[game])[0]


def test_missing_history_stays_missing_no_zero_fill():
    values,_,audit=aggregate(make_row(),[])
    assert values=={} and len(audit)==2


def test_calendar_end_plus24h_boundary_is_not_fabricated_completion():
    row=make_row()
    game=make_game(day='2026-03-29')
    row['decision_at']='2026-03-30T23:59:59+09:00'
    assert not aggregate(row,[game])[0]
    row['decision_at']='2026-03-31T00:00:01+09:00'
    values,evidence,_=aggregate(row,[game])
    assert values and all(e['max_fact_time'] is None and e['max_fact_date']=='2026-03-29' for e in evidence.values())


@pytest.mark.parametrize('field,value,reason',[
    ('fact_date','2026-04-01','date_mismatch'),('home_id','SS','home_away_team_mismatch'),
    ('game_id','other_doubleheader','exact_official_fixture_id_mismatch'),
    ('start_time','2026-03-31T17:00:00+09:00','kickoff_difference_over30m')])
def test_target_match_identity_adversarial(field,value,reason):
    row=make_row()
    game=make_game('target','2026-03-31');game['start_time']=row['kickoff'];game[field]=value
    assert target_match(row,game)==(False,reason)


def test_archive_ambiguous_names_and_naive_clock_fail_closed():
    assert team_code('NPB','지바 롯데 마린스')=='m'
    assert team_code('KBO','KIA 타이거즈')=='HT'
    with pytest.raises(ValueError):team_code('NPB','unknown')
    with pytest.raises(ValueError):dt('2026-03-31T00:00:00')


def test_request_budget_failures_durable_and_official_only(tmp_path):
    fetch=Fetch(tmp_path,limit=1)
    class Broken:
        def request(self,*args,**kwargs):
            import requests
            raise requests.Timeout('do not emit request exception text')
    fetch.session=Broken()
    url='https://npb.jp/bis/2026/games/gm20260329.html'
    html,meta=fetch.get(url)
    assert not html and meta['error']=='Timeout' and len(fetch.attempts)==1
    assert Fetch(tmp_path,1).get(url)[1]['error']=='Timeout'
    with pytest.raises(RuntimeError,match='budget_exhausted'):
        Fetch(tmp_path,1).get(url+'?different')
    with pytest.raises(ValueError,match='nonofficial_url'):
        fetch.get('https://untrusted.example/')
