import copy
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest
from fixed_tipster_hybrid import context, VERSION, load_votes
from ai_decision import build_decision_snapshot, validate_decision_snapshot

AT = "2026-10-25T22:00:00+00:00"


def game():
    return dict(league="MLB", sport="bs", home="뉴욕양키", away="보스레드",
        year=2026, date="10.26(월) 08:10", round=100,
        options=[dict(market="승패", label="", 선택=s, 배당=o, 시장확률=p, 게임번호="10")
                 for s, o, p in [("홈", 1.49, .52), ("원정", 2.1, .48)]] +
                [dict(market="언더오버", label="8.5", 선택="언더", 배당=1.8, 시장확률=.8, 게임번호="11")])


def vote(**kw):
    return dict(dict(source="tailslips_public_html", event_type="first_observed", identity_version=2,
        eligible_pre_event=True, game_started=False, result=None, is_parlay_leg=False,
        is_baseline=False, market_type="moneyline", team_a="NYY", team_b="BOS",
        slug="author", pick_id="pick1", selection="BOS ML +110", american_odds=110,
        slate_date="2026-10-25", game_header="NYY BOS 7:10 PM ET", observed_at=AT), **kw)


def decide(g=None, rows=None, at=AT, **kwargs):
    g = g or game()
    s = build_decision_snapshot(g, as_of=at, tipster_votes=[vote()] if rows is None else rows, **kwargs)
    validate_decision_snapshot(s)
    return g, s


def test_reverses_market_favorite_without_selecting_high_probability_total():
    g, s = decide()
    assert g["추천"]["선택"] == "원정"
    assert s["probability"]["final"] == .532
    assert s["model"]["operating_version"] == VERSION
    assert s["model"]["policy_authorized"] is True
    assert s["model"]["validated_edge"] is False
    assert s["model"]["promotion_gate"] == "user_authorized_unvalidated"
    assert s["tipster_hybrid"]["authors"] == 1


def test_idempotent_and_evidence_changes_revision():
    g, s = decide()
    _, repeated = decide(g)
    assert s == repeated
    _, other = decide(rows=[vote(slug="another")])
    assert s["decision_id"] != other["decision_id"]


def test_fallback_keeps_low_odds_moneyline_and_not_total():
    g, s = decide(rows=[])
    assert g["추천"]["선택"] == "홈"
    assert g["추천"]["배당"] == 1.49
    assert s["probability"]["final"] == .52
    assert s["tipster_hybrid"]["status"] == "market_fallback"


@pytest.mark.parametrize("change", [dict(result="W"), dict(game_started=True), dict(is_baseline=True),
    dict(is_parlay_leg=True), dict(identity_version=1), dict(event_type="updated"),
    dict(observed_at="2026-10-25T22:01:00Z"), dict(observed_at="2026-10-25T22:00:00"),
    dict(game_header="NYY BOS 1:10 PM ET"), dict(selection="BOS F5 ML +110"),
    dict(american_odds=120), dict(source="other"), dict(slug=""), dict(team_a="LAD"),
    dict(slate_date="2026-10-24"), dict(selection="BOS ML NaN")])
def test_invalid_inputs_cannot_change_probability(change):
    _, s = decide(rows=[vote(**change)])
    assert s["probability"]["final"] == .52
    assert not s["model"]["policy_authorized"]


def test_one_earliest_vote_per_author_and_stake_suffix():
    rows = [vote(selection="NYY ML -120 1u", american_odds=-120),
            vote(pick_id="later", observed_at="2026-10-25T22:01:00Z")]
    info = context(game(), rows, "2026-10-25T22:02:00Z", "2026-10-25T22:02:00Z")
    assert info["authors"] == 1 and info["home_vote_share"] == 1


def test_late_build_and_reconstruction_do_not_apply_new_votes():
    _, s = decide(built_at="2026-10-25T22:41:00Z")
    assert s["tipster_hybrid"]["reason"] == "after_t30"
    _, s = decide(reconstructed_at=AT)
    assert s.get("tipster_hybrid") is None


@pytest.mark.parametrize("sport,league", [("bs", "KBO"), ("bk", "KBL"), ("vl", "KOVO"), ("sc", "EPL")])
def test_other_leagues_use_explicit_market_fallback(sport, league):
    g = game(); g["league"] = "KBO"
    g.update(sport=sport, league=league)
    _, s = decide(g)
    assert s["tipster_hybrid"]["reason"] == "no_eligible_pregame_votes"
    assert s["model"]["selection_policy"] == VERSION
    assert s["probability"]["final"] == .52
    assert g["추천"]["market"] == "승패"


def test_three_way_draw_can_win_and_missing_side_is_withheld():
    g = game()
    g.update(sport="sc", league="EPL")
    g["options"] = [dict(market="승무패", label="", 선택=s, 배당=3.0, 시장확률=p)
                    for s, p in [("홈", .30), ("무", .40), ("원정", .30)]]
    _, s = decide(g)
    assert g["추천"]["선택"] == "무"
    assert s["probability"]["final"] == .4
    g["options"].pop()
    _, s = decide(g)
    assert s["action"] == "withhold"


def test_invalid_or_duplicate_moneyline_withheld():
    g = game(); g["options"].append(copy.deepcopy(g["options"][0]))
    _, s = decide(g)
    assert s["action"] == "withhold"


def test_loader_uses_time_bounds_and_fails_closed(monkeypatch):
    import runtime_db
    monkeypatch.setattr(runtime_db, "database_enabled", lambda: True)
    calls = []
    def iterator(self, stream, **kwargs):
        calls.append((stream, kwargs))
        if stream == "pickster_pick_events":
            yield vote()
    monkeypatch.setattr(runtime_db.RuntimeDatabase, "iter_events", iterator)
    assert load_votes(AT) == [vote()]
    assert calls == [(stream, {"since": "2026-10-22", "through": "2026-10-26"})
                     for stream in ("pickster_pick_events", "public_tipster_votes")]


def test_new_votes_refresh_unchanged_odds_but_never_frozen_pick(tmp_path, monkeypatch):
    from datetime import datetime
    import fixed_tipster_hybrid
    import src.live_market_refresh as refresh
    import kbo_pregame
    monkeypatch.setenv("PROODD_DB_PATH", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setattr(kbo_pregame, "current_document", lambda: {"games": []})
    rows = []
    calls = []
    def load(at):
        calls.append(at)
        return rows[:]
    monkeypatch.setattr(fixed_tipster_hybrid, "load_votes", load)
    g = game()
    feed = dict(generated_at=AT, markets={"100": {"10": dict(
        game_no="10", date=g["date"], sport="bs", league="MLB", home=g["home"], away=g["away"],
        market="승패", label="", n_way=2, odds=[1.8, 1.9], result="경기전")}})
    doc, n = refresh.refresh_document({"live": [], "past": []}, feed, now=datetime.fromisoformat(AT))
    assert n == 1 and doc["live"][0]["추천"]["선택"] == "홈"
    rows.append(vote())
    doc, n = refresh.refresh_document(doc, feed, now=datetime.fromisoformat("2026-10-25T22:01:00+00:00"))
    current = doc["live"][0]
    assert n == 1 and current["추천"]["선택"] == "원정"
    assert current["decision_snapshot"]["model"]["validated_edge"] is False
    pinned = copy.deepcopy(current["decision_snapshot"])
    previous_calls = len(calls)
    rows.clear()
    doc, n = refresh.refresh_document(doc, feed, now=datetime.fromisoformat("2026-10-25T22:41:00+00:00"))
    assert doc["live"][0]["decision_snapshot"] == pinned
    assert len(calls) == previous_calls


def test_sql_since_bounds(tmp_path):
    from runtime_db import RuntimeDatabase
    db = RuntimeDatabase(tmp_path / "test.sqlite3")
    db.append_events("test", [dict(observed_at=f"2026-10-{day}T00:00:00Z", id=day) for day in (20, 23, 26)])
    assert [r["id"] for r in db.iter_events("test", since="2026-10-22", through="2026-10-25")] == [23]


def test_today_metadata_does_not_turn_authorization_into_validated_profit():
    from today_combo import _apply_decision_pipeline
    g, snapshot = decide()
    g["decision_snapshot"] = snapshot
    option = g["추천"]
    candidate = dict(market="승패", market_label="", sel="원정", odds=option["배당"],
                     market_prob=option["시장확률"])
    _apply_decision_pipeline(candidate, g)
    assert candidate["predicted_hit_prob"] == .532
    assert candidate["decision_pipeline_applied"] is True
    assert candidate["policy_authorized"] is True
    assert candidate["has_validated_edge"] is False
    assert candidate["validated_uncertainty_available"] is False
