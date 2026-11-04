import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from public_market_observations import parse_reference_events, safe_reference_url

URL = "https://www.goli.tv/board/free/298798"
NOW = "2026-11-04T01:07:01.872241+00:00"
HTML = (Path(__file__).parent / "fixtures_public_market/goli-free-reference-minimal.html").read_text(encoding="utf8")

def game(sport, league, home, away, date="11.04(수) 19:00"):
    return dict(year=2026, date=date, sport=sport, league=league, home=home, away=away)

GAMES = [game("bk", "KBL", "서울SK", "한국가스"), game("bk", "WKBL", "신한은행", "우리은행"),
         game("vl", "KOVO남", "KB손보", "OK저축"), game("vl", "KOVO여", "흥국생명", "대전정관")]

def parse(html=HTML, games=GAMES, now=NOW):
    return parse_reference_events(URL, html, now, games)

def test_real_source_rows_with_api_team_names():
    events = parse()
    assert [(e["fixture"][1], e["selected_team"], e["team_line"], e["side"]) for e in events if e["family"] == "handicap"] == [("KBL", "서울SK", -6, "home"), ("WKBL", "우리은행", -7, "away")]
    assert [(e["fixture"][1], e["side"], e["line"]) for e in events if e["family"] == "total"] == [("KOVO남", "over", 182.5), ("KOVO여", "over", 179.5)]
    for e in events:
        assert e["reference_only"] and e["is_parlay_leg"] and e["eligible_pre_event"]
        assert e["period"] == e["settlement"] == "unknown"
        assert not e["price_eligible"] and not e["affects_model"] and not e["affects_probability"] and not e["affects_recommendation"]
        assert e["source_odds_text"] == "1.85배"
        assert e["source_url"] == e["url"] == URL

def test_exact_volleyball_team_pairs_extract_totals_without_alias_guessing():
    games = [*GAMES[:2], game("vl", "KOVO남", "KB손해보험", "OK저축은행"), game("vl", "KOVO여", "흥국생명", "정관장")]
    events = parse(games=games)
    assert [(x["side"], x["line"]) for x in events if x["family"] == "total"] == [("over", 182.5), ("over", 179.5)]

def test_identity_stable_when_line_changes_and_home_away_preserved():
    changed = parse(HTML.replace("-6점", "-6.5점"))
    assert changed[0]["pick_id"] == parse()[0]["pick_id"]
    swapped = [game("bk", "KBL", "한국가스", "서울SK")]
    event, = parse(games=swapped)
    assert event["fixture"][2:4] == ["한국가스", "서울SK"]
    assert event["side"] == "away" and event["team_line"] == -6

@pytest.mark.parametrize("url", ["http://www.goli.tv/board/free/298798", "https://goli.tv/board/free/298798", "https://www.goli.tv/board/analysis/298798", "https://www.goli.tv/board/free/298798?x=1", "https://www.goli.tv@evil/board/free/298798"])
def test_restrict_url(url):
    assert not safe_reference_url(url)
    assert parse_reference_events(url, HTML, NOW, GAMES) == []

def test_timing_duplicate_matching_and_future_publication():
    assert parse(now="2026-11-04T09:30:00+00:00") == []
    assert parse(now="2026-11-03T22:00:00+00:00") == []
    assert parse(games=[GAMES[0], GAMES[0]]) == []
    assert parse(games=[dict(GAMES[0], date="11.04(수) 19:11")]) == []
    assert len(parse(games=[dict(GAMES[0], date="11.04(수) 19:10")])) == 1

@pytest.mark.parametrize("html", [HTML.replace("11월4일", "11월5일", 1), HTML.replace("6폴더", "단일"), HTML.replace("마핸승", "마핸승 취소", 1), HTML.replace("2026-11-04 07:37", "2026-11-04 11:00")])
def test_ambiguous_or_future_source_rejected(html):
    assert parse(html) == []

def test_duplicate_same_family_withheld():
    fragment = "<p>11월4일 19:00분!!!</p><p>한국남자농구!!!</p><p>3.서울SK:한국가스공사</p><p>서울SK -6점 마핸승!!! 1.85배!!!</p>"
    assert all(e["fixture"][1] != "KBL" for e in parse(HTML.replace("<p>6폴더", fragment + "<p>6폴더")))


def test_reference_aliases_are_league_scoped_and_do_not_modify_winner_aliases():
    from public_market_observations import reference_team
    from public_tipster_votes import team
    assert reference_team("OK저축은행", "KOVO남") == "OK저축"
    assert reference_team("정관장", "KOVO여") == "대전정관"
    assert reference_team("정관장", "KBL") != "대전정관"
    assert team("OK저축은행", "KOVO남") != "OK저축"


def test_normal_collector_storage_loader_and_handoff_keep_references_out_of_votes(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    import public_tipster_votes as collector
    from runtime_db import RuntimeDatabase
    from fixed_tipster_hybrid import load_votes,context
    from market_diversity import build_market_candidates
    from deploy import supervisor
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime.fromisoformat(NOW).astimezone(tz or timezone.utc)
    db_path=tmp_path/'references.sqlite3';monkeypatch.setenv('PROODD_DB_PATH',str(db_path))
    db=RuntimeDatabase(db_path);db.store_artifact('picks_v2',{'live':GAMES})
    monkeypatch.setattr(collector,'datetime',Clock);monkeypatch.setattr(collector.time,'sleep',lambda _:None)
    calls=[]
    def fetch(session,url):
        calls.append(url)
        if url==URL:return HTML
        if url=='https://www.goli.tv/board/analysis':return '<a href="'+URL+'">11월4일 오늘 픽</a>'
        return '<html></html>'
    monkeypatch.setattr(collector,'fetch',fetch)
    before=supervisor._tipster_revision('다종목 픽스터')
    summary=collector.collect(db,object())
    assert summary['reference_observations_written']==4 and summary['written']==0
    assert summary['status']=='references_only'
    assert calls.count(URL)==1 and len(calls)==6 # five listings, one body
    assert len(list(db.iter_events('public_tipster_votes')))==0
    assert len(list(db.iter_events('public_tipster_market_observations')))==4
    assert supervisor._tipster_revision('다종목 픽스터')!=before
    rows=load_votes(NOW)
    assert len(rows)==4
    for fixture in GAMES:
        display=build_market_candidates(fixture,rows,NOW,NOW)
        assert len(display['references'])==1 and not display['offers']
        assert context(fixture,rows,NOW,NOW)['authors']==0
    # Reobserving a source creates no extra votes or publication revisions.
    before=supervisor._tipster_revision('다종목 픽스터')
    assert collector.collect(db,object())['reference_observations_written']==0
    assert supervisor._tipster_revision('다종목 픽스터')==before


def test_reference_lines_do_not_change_operating_selection_or_probability():
    import copy
    from ai_decision import build_decision_snapshot
    fixture=copy.deepcopy(GAMES[0]);fixture['round']=131
    fixture['options']=[dict(market='승패',label='',n_way=2,선택=side,배당=price,시장확률=p,게임번호='1')
        for side,price,p in [('홈',1.8,.55),('원정',2.,.45)]]
    baseline=build_decision_snapshot(copy.deepcopy(fixture),as_of=NOW,built_at=NOW,tipster_votes=[])
    actual=build_decision_snapshot(fixture,as_of=NOW,built_at=NOW,tipster_votes=parse())
    assert len(fixture['market_candidates']['references'])==1
    for key in ('selection_id','offer_id','input_revision_hash','probability'):
        assert baseline[key]==actual[key]


def test_reference_discovery_survives_analysis_outage_and_unsupported_newest(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    import requests
    import public_tipster_votes as collector
    from runtime_db import RuntimeDatabase
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime.fromisoformat(NOW).astimezone(tz or timezone.utc)
    db=RuntimeDatabase(tmp_path/'fallback.sqlite3');db.store_artifact('picks_v2',{'live':GAMES})
    monkeypatch.setattr(collector,'datetime',Clock);monkeypatch.setattr(collector.time,'sleep',lambda _:None)
    calls=[];unsupported='https://www.goli.tv/board/free/298799'
    def fetch(session,url):
        calls.append(url)
        if url=='https://www.goli.tv/board/analysis':raise requests.ConnectionError('analysis unavailable')
        if url=='https://www.goli.tv/board/free':return ''.join('<a href="'+u+'">11월4일 오늘 픽</a>' for u in (unsupported,URL))
        if url==URL:return HTML
        return '<html></html>'
    monkeypatch.setattr(collector,'fetch',fetch)
    summary=collector.collect(db,object())
    assert summary['reference_observations_written']==4
    assert summary['errors']['goli:ConnectionError']==1
    assert unsupported in calls and URL in calls
    assert summary['candidates']<=16 and len(calls)<=22
