"""Minimal source structure; original articles are not distributed."""
from datetime import datetime
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from public_tipster_votes import parse_page, match_vote, safe_url, discover_links
from fixed_tipster_hybrid import context, apply

AT = '2026-10-28T14:00:00Z'
URL = 'https://golfmasterj.tistory.com/entry/fixture-test'


def game():
    return dict(year=2026, sport='sc', league='세리에A', home='파르마', away='우디네세',
                date='10.29(목) 02:30', probability_pipeline={}, options=[
                    dict(market='승무패', label='', 선택=s, 배당=3., 시장확률=p)
                    for s, p in [('홈', .35), ('무', .3), ('원정', .35)]])


def markup(pick='무승부', heading='정규시간 90분 기본 방향'):
    meta = {'@type': 'BlogPosting', 'headline': '[세리에 A 프리뷰]',
            'url': URL, 'author': {'name': 'writer'}, 'datePublished': '2026-10-28T10:00:00+09:00'}
    return '<script type="application/ld+json">' + json.dumps(meta) + '</script>' + f'''
    <div class="contents_style"><p>일시 : 2026년 10월 29일(목) 02: 30 (한국 시간 KST)</p>
    <h3>파르마 칼초 1913 (Home)</h3><p>전력 설명.</p><h3>우디네세 칼초 (Away)</h3>
    <h2>5. 최종 종합 결론 및 추천 가이드</h2><ul><li>{heading} : <b>{pick}</b></li></ul>
    <h3>자주 묻는 질문</h3><p>파르마 승리</p></div>'''


def test_explicit_regulation_pick_matches_fixture_and_changes_vote_without_probability_inflation():
    vote = match_vote(parse_page(URL, markup(), AT), [game()])
    assert vote['side'] == '무'
    info = context(game(), [vote], AT, AT)
    picked = apply(game(), info)
    assert picked['선택'] == '무' and picked['최종확률'] == .3
    assert info['authors'] == 1


def test_pick_field_does_not_consume_next_score_or_handicap_field():
    html = markup('우디네세 칼초 승리').replace('</li></ul>',
        '</li><li>예상 스코어 : 1 - 2</li><li>핸디캡 : 파르마 +1.0</li></ul>')
    assert match_vote(parse_page(URL, html, AT), [game()])['side'] == '원정'


@pytest.mark.parametrize('pick,heading', [('우디네세 약우세', '전망'),
    ('우디네세 승리', '진출 팀'), ('파르마 +1.0', '정규시간 90분 기본 방향'),
    ('무/패', '정규시간 90분 기본 방향'), ('무승부 또는 파르마 승', '정규시간 90분 기본 방향')])
def test_narrative_handicap_double_chance_and_qualification_are_not_moneyline(pick, heading):
    with pytest.raises(ValueError):
        parse_page(URL, markup(pick, heading), AT)


def test_late_observation_and_mismatched_fixture_cannot_enter_model():
    row = parse_page(URL, markup(), AT)
    with pytest.raises(ValueError, match='outside_pregame'):
        match_vote(dict(row, observed_at='2026-10-28T17:00:01Z'), [game()])
    with pytest.raises(ValueError, match='ambiguous'):
        match_vote(row, [game(), game()])
    with pytest.raises(ValueError, match='unmatched'):
        match_vote(row, [dict(game(), league='잉리그컵')])


@pytest.mark.parametrize('url', ['http://golfmasterj.tistory.com/entry/a',
    'https://golfmasterj.tistory.com/manage/entry/post',
    'https://golfmasterj.tistory.com/entry/a%2fb',
    'https://golfmasterj.tistory.com/entry/a?redirect=elsewhere',
    'https://golfmasterj.tistory.com.evil.test/entry/a'])
def test_new_source_allowlist_is_exact_and_read_only_article_paths(url):
    assert not safe_url(url)


def test_current_links_win_over_thousands_of_old_archive_links():
    old = ''.join(f'<a href="/posts/detail/2026-06-02-old-{n}/">old</a>' for n in range(30))
    html = old + '<a href="/posts/detail/2026-10-29-new/">upcoming</a>'
    html += '<a href="/posts/detail/2026-10-28-today/">today</a>'
    found = discover_links('https://pick79.com/posts/baseball/', html, AT)
    assert found == ['https://pick79.com/posts/detail/2026-10-28-today/',
                     'https://pick79.com/posts/detail/2026-10-29-new/']


def test_exact_copies_are_one_vote_but_agreeing_distinct_analysis_remains_two():
    row = match_vote(parse_page(URL, markup(), AT), [game()])
    row.update(analysis_hash='a'*64, analysis_characters=400)
    other = dict(row, pick_id='other', voter='www.goli.tv:other',
                 source_url='https://www.goli.tv/board/analysis/123')
    info = context(game(), [other, row], AT, AT)
    assert info['authors'] == 1 and info['raw_authors'] == 2
    assert info['duplicate_analysis_count'] == 1
    other['analysis_hash'] = 'b'*64
    assert context(game(), [other, row], AT, AT)['authors'] == 2
    other.update(analysis_hash='a'*64, side='홈')
    info = context(game(), [other, row], AT, AT)
    assert info['status'] == 'withheld' and info['conflicting_analysis_count'] == 2


def test_collector_has_global_deadline_before_starting_more_requests(tmp_path, monkeypatch):
    import public_tipster_votes as mod
    from runtime_db import RuntimeDatabase
    db = RuntimeDatabase(tmp_path/'deadline.db')
    db.store_artifact('picks_v2', {'live': []})
    clock = [0.]
    calls = []
    monkeypatch.setattr(mod.time, 'monotonic', lambda: clock[0])
    def fetch(*args):
        calls.append(args[1])
        clock[0] += 95
        return '<a href="https://pick79.com/posts/detail/test/">article</a>'
    monkeypatch.setattr(mod, 'fetch', fetch)
    result = mod.collect(db, object())
    assert len(calls) == 1 and result['errors']['collection_deadline'] >= 1
    assert result['candidates'] == 0 and result['deferred'] == 1


@pytest.mark.parametrize('league,source,canonical', [
    ('K리그1', '울산 HD', '울산HDFC'), ('K리그1', '포항 스틸러스', '포항스틸'),
    ('K리그2', '서울 이랜드 FC', '서울이랜'), ('NPB', '니혼햄 파이터스', '닛폰햄'),
    ('NPB', '요코하마 DeNA 베이스타스', '요코베이'),
    ('NBA', '포틀랜드 트레일블레이저스', '포틀트레'),
    ('NBA', '샌안토니오 스퍼스', '샌안스퍼'),
])
def test_verified_league_scoped_team_aliases(league, source, canonical):
    from public_tipster_votes import team
    assert team(source, league) == team(canonical, league)
    assert team(source, 'unrelated') != team(canonical, 'unrelated')
