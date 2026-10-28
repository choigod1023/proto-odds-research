"""Synthetic minimal markup mirrors inspected public DOM; no article bodies."""
import copy
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest
from public_tipster_votes import parse_page, match_vote, fixture_key, read_fixtures, fetch
from fixed_tipster_hybrid import context, apply

AT = "2026-10-26T04:00:00Z"
URL = "https://pick79.com/posts/detail/test-kbl/"


def game(sport="bk", league="KBL", home="고양소노", away="창원LG"):
    return dict(sport=sport, league=league, home=home, away=away, year=2026,
                date="10.26(월) 19:00", probability_pipeline={},
                options=[dict(market="승패", label="", 선택=s, 배당=2.0, 시장확률=p)
                         for s, p in [("홈", .55), ("원정", .45)]])


def markup(sport="Basketball", league="KBL", home="고양 소노", away="LG", choice="고양 소노", result="승"):
    data = {"@graph": [{"@type": "Article", "author": {"name": "픽천국"},
                       "datePublished": "2026-10-26T10:00:00Z"},
                      {"@type": "SportsEvent", "homeTeam": {"name": home},
                       "awayTeam": {"name": away}, "superEvent": {"name": league},
                       "sport": sport, "startDate": "2026-10-26T10:00:00Z",
                       "eventStatus": "https://schema.org/EventScheduled"}]}
    return '<script type="application/ld+json">'+json.dumps(data)+'</script>'+f'''
    <div class="section-widget"><h2>추천 픽</h2><div><div>
    <div><div>승무패</div><div>{choice}</div></div><div>{result}</div>
    </div></div></div>'''


def event(g=None, html=None, at=AT):
    return match_vote(parse_page(URL, html or markup(), at), [g or game()])


@pytest.mark.parametrize("sport,league,home,away,source_sport", [
    ("bk", "KBL", "고양소노", "창원LG", "Basketball"),
    ("bs", "KBO", "삼성", "KIA", "Baseball"),
    ("sc", "EPL", "첼시", "토트넘", "Soccer"),
    ("vl", "KOVO컵남", "대한항공", "현대캐피", "Volleyball"),
])
def test_all_sports_actual_vote_vector(sport, league, home, away, source_sport):
    g = game(sport, league, home, away)
    e = event(g, markup(source_sport, league, home, away, away))
    info = context(g, [e], AT, AT)
    assert info["status"] == "applied"
    picked = apply(g, info)
    assert picked["선택"] == "원정" and picked["최종확률"] == .505
    assert sum(o["최종확률"] for o in g["options"]) == 1


def test_three_way_draw_is_independent_share():
    g = game("sc", "EPL", "첼시", "토트넘")
    g["options"] = [dict(market="승무패", label="", 선택=s, 배당=3., 시장확률=p)
                    for s, p in [("홈", .35), ("무", .3), ("원정", .35)]]
    e = event(g, markup("Soccer", "EPL", "첼시", "토트넘", "무승부", ""))
    info = context(g, [e], AT, AT)
    assert apply(g, info)["선택"] == "무"
    assert [o["최종확률"] for o in g["options"]] == [.315, .37, .315]


def test_claimed_publication_is_not_fabricated_provenance():
    e = event()
    assert e["published_at"] is None
    assert e["reported_published_at"] == "2026-10-26T10:00:00Z"
    assert e["observed_at"] == "2026-10-26T04:00:00+00:00"
    assert e["provenance"] == "live_http_observation"


@pytest.mark.parametrize("at", ["2026-10-26T09:30:01Z", "2026-10-27T04:00:00Z", "2026-10-22T04:00:00Z"])
def test_reject_late_or_old_observations(at):
    with pytest.raises(ValueError, match="outside_pregame"):
        event(at=at)


def test_earliest_vote_per_publisher_and_no_future_evidence():
    g = game(); e = event()
    later = dict(e, side="원정", pick_id="second", observed_at="2026-10-26T04:01:00Z")
    info = context(g, [later, e, e], "2026-10-26T04:02:00Z", "2026-10-26T04:02:00Z")
    assert info["authors"] == 1 and info["home_vote_share"] == 1
    assert context(g, [later], AT, AT)["status"] == "market_fallback"
    assert context(g, [e], AT, "2026-10-26T09:31:00Z")["reason"] == "after_t30"


@pytest.mark.parametrize("change", [dict(fixture=[]), dict(market_type="first_half"),
    dict(source_url="https://evil.example/pick"), dict(provenance="historical_download"),
    dict(eligible_pre_event=False), dict(event_type="changed"), dict(side="오버")])
def test_invalid_event_cannot_affect_probability(change):
    assert context(game(), [dict(event(), **change)], AT, AT)["status"] == "market_fallback"


def test_fixture_mismatch_and_doubleheader_are_rejected():
    row = parse_page(URL, markup(), AT)
    with pytest.raises(ValueError, match="ambiguous"):
        match_vote(row, [game(), game()])
    with pytest.raises(ValueError, match="unmatched"):
        match_vote(row, [game(league="WKBL")])


def test_reverse_source_home_away_does_not_reverse_selection():
    row = parse_page(URL, markup(home="LG", away="고양 소노", choice="LG"), AT)
    assert match_vote(row, [game()])["side"] == "원정"
    with pytest.raises(ValueError, match="stale_publication"):
        match_vote(dict(row, published_at="2025-10-26T10:00:00+09:00"), [game()])


def test_kbl_db_alias_matches_actual_scheduled_fixture():
    # Observed on pick79's 2026-10-28 KBL page; only identity and pick markup.
    g = game(home="한국가스", away="원주DB")
    e = event(g, markup(home="한국가스공사", away="DB", choice="DB"))
    assert e["side"] == "원정"
    assert e["fixture"] == fixture_key(g)
    info = context(g, [e], AT, AT)
    assert info["status"] == "applied"
    assert apply(g, info)["최종확률"] == .505
    with pytest.raises(ValueError, match="ambiguous"):
        match_vote(parse_page(URL, markup(home="한국가스공사", away="DB", choice="DB"), AT), [g, g])
    with pytest.raises(ValueError, match="unmatched"):
        match_vote(parse_page(URL, markup(home="한국가스공사", away="DB", choice="DB"), AT), [dict(g, league="WKBL")])


def test_first_observation_is_immutable_and_fixture_projection(tmp_path):
    from runtime_db import RuntimeDatabase
    db = RuntimeDatabase(tmp_path / "votes.sqlite3")
    e = event()
    assert db.append_events("pickster_pick_events", [e], identity_keys=("source", "pick_id")) == 1
    assert db.append_events("pickster_pick_events", [dict(e, side="원정")], identity_keys=("source", "pick_id")) == 0
    assert db.events("pickster_pick_events")[0]["side"] == "홈"
    db.store_artifact("picks_v2", {"live": [game()]})
    assert fixture_key(read_fixtures(db)[0]) == fixture_key(game())
    assert "options" not in read_fixtures(db)[0]


def test_goli_only_fullgame_section():
    html = '''<h1 id="page-heading">고양소노 대 창원LG KBL 10월26일 맞대결</h1>
    <div class="writer_box"><a class="_userView">writer</a></div>
    <ul class="view_stat"><li>2026-10-26 10:00</li></ul>
    <div id="partner_content"><p>베팅 요약 및 추천</p><p>[승/무/패] 고양소노 패</p>
    <p>전반전 예측</p><p>[승/무/패] 고양소노 승</p></div>'''
    row = parse_page("https://www.goli.tv/board/analysis/123", html, AT)
    assert match_vote(row, [game()])["side"] == "원정"
    with pytest.raises(ValueError):
        parse_page("https://www.goli.tv/board/analysis/123", html.replace("베팅 요약 및 추천", "전반전"), AT)


def test_http_redirect_and_oversized_body_fail_closed():
    class Response:
        status_code = 302
        headers = {"Content-Type": "text/html"}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield b"x" * 6_000_001
    class Session:
        def get(self, url, **kw):
            assert kw["allow_redirects"] is False
            return Response()
    with pytest.raises(ValueError, match="not_html"):
        fetch(Session(), URL)
    Response.status_code = 200
    with pytest.raises(ValueError, match="budget"):
        fetch(Session(), URL)


def test_collector_runs_bounded_and_retains_original_observation(tmp_path, monkeypatch):
    import public_tipster_votes as mod
    from runtime_db import RuntimeDatabase
    db = RuntimeDatabase(tmp_path / "collect.sqlite3")
    class Clock:
        @staticmethod
        def now(tz): return mod.stamp(AT)
    monkeypatch.setattr(mod, "datetime", Clock)
    # Preserve actual timestamp parser while fixing only collector wall clock.
    Clock.fromisoformat = __import__("datetime").datetime.fromisoformat
    monkeypatch.setattr(mod, "kickoff", lambda g: mod.stamp("2026-10-26T10:00:00Z"))
    monkeypatch.setattr(mod.time, "sleep", lambda n: None)
    db.store_artifact("picks_v2", {"live": [game()]})
    calls = []
    def http(session, url):
        calls.append(url)
        if "/posts/detail/" in url: return markup()
        if "goli" in url: raise ValueError("not_html_200")
        return '<a href="/posts/detail/test-kbl/">test</a>'
    monkeypatch.setattr(mod, "fetch", http)
    result = mod.collect(db, object())
    assert result["written"] == 1 and result["sports"] == {"bk": 1}
    assert result["errors"] == {"goli:ValueError": 1}
    assert len(calls) == 6
    assert mod.collect(db, object())["written"] == 0
    assert len(db.events("public_tipster_votes")) == 1


def test_non_mlb_votes_refresh_unchanged_prices_and_freeze_at_t30(tmp_path, monkeypatch):
    from datetime import datetime
    import fixed_tipster_hybrid
    import src.live_market_refresh as refresh
    import kbo_pregame
    monkeypatch.setenv("PROODD_DB_PATH", str(tmp_path / "refresh.sqlite3"))
    monkeypatch.setattr(kbo_pregame, "current_document", lambda: {"games": []})
    votes, calls = [], []
    def load(at):
        calls.append(at)
        return votes[:]
    monkeypatch.setattr(fixed_tipster_hybrid, "load_votes", load)
    g = game()
    feed = dict(generated_at=AT, markets={"127": {"10": dict(game_no="10", date=g["date"],
        sport=g["sport"], league=g["league"], home=g["home"], away=g["away"],
        market="승패", label="", n_way=2, odds=[1.8, 1.9], result="경기전")}})
    doc, _ = refresh.refresh_document({"live": [], "past": []}, feed, now=datetime.fromisoformat(AT))
    assert doc["live"][0]["추천"]["선택"] == "홈"
    votes.append(event(html=markup(choice="LG")))
    doc, n = refresh.refresh_document(doc, feed, now=datetime.fromisoformat("2026-10-26T04:01:00Z"))
    assert n == 1 and doc["live"][0]["추천"]["선택"] == "원정"
    snapshot = copy.deepcopy(doc["live"][0]["decision_snapshot"])
    count = len(calls)
    votes.clear()
    doc, _ = refresh.refresh_document(doc, feed, now=datetime.fromisoformat("2026-10-26T09:31:00Z"))
    assert doc["live"][0]["decision_snapshot"] == snapshot
    assert len(calls) == count


def test_latest_article_per_listing_is_checked_while_history_rotates(tmp_path, monkeypatch):
    import public_tipster_votes as mod
    from runtime_db import RuntimeDatabase
    db = RuntimeDatabase(tmp_path / "rotation.sqlite3")
    db.store_artifact("picks_v2", {"live": []})
    db.put_document("public_tipster_intake", {"cursor": 35})
    monkeypatch.setattr(mod.time, "sleep", lambda n: None)
    categories = ("soccer", "baseball", "basketball", "volleyball")
    listings = {f"https://pick79.com/posts/{c}/":
                [f"https://pick79.com/posts/detail/{c}-{i}/" for i in range(12)]
                for c in categories}
    listings["https://www.goli.tv/board/analysis"] = [f"https://www.goli.tv/board/analysis/{i}" for i in range(12)]
    articles = []

    def http(session, url):
        if url in listings:
            return ''.join(f'<a href="{u}">article</a>' for u in listings[url])
        articles.append(url)
        raise ValueError("missing_explicit_pick")

    monkeypatch.setattr(mod, "fetch", http)
    latest = {links[0] for links in listings.values()}
    seen = set()
    for _ in range(6):
        articles.clear()
        summary = mod.collect(db, object())
        assert latest <= set(articles)
        assert len(articles) == len(set(articles)) <= 16
        assert summary["candidates"] == len(articles)
        seen.update(articles)
    assert seen == {u for links in listings.values() for u in links}
