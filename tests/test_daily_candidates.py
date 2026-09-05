from datetime import datetime
from zoneinfo import ZoneInfo

from src import today_combo


NOW = datetime(2026, 9, 5, 10, tzinfo=ZoneInfo("Asia/Seoul"))


def feed(**changes):
    row = {"home": "LG", "away": "삼성", "date": "09.05(토) 18:00",
           "league": "KBO", "sport": "bs", "market": "승패",
           "label": "", "odds": [1.55, 2.3], "result": "경기전", **changes}
    return {"generated_at": "2026-09-05T09:55:00+09:00",
            "markets": {"105": {"10": row}},
            # Intentionally inconsistent legacy price map: metadata is canonical.
            "odds": {"105": {"10": [2.3, 1.55]}}}


def loader(monkeypatch, live, picks=None):
    values = {"live_odds": live, "picks_v2": picks or {},
              "loss_grades": {"odds_bins": []},
              "today": {"generated_at": "2026-09-02T01:00:00Z", "rounds": []}}
    calls = []

    def load(name, path):
        calls.append(name)
        return values.get(name)

    monkeypatch.setattr(today_combo, "load_runtime_artifact", load)
    return calls


def test_new_round_candidates_do_not_depend_on_stale_today(monkeypatch):
    calls = loader(monkeypatch, feed())
    source = today_combo._candidate_source()
    candidates = today_combo.legs_today(NOW, source=source)
    assert len(candidates) == 1
    assert candidates[0]["round"] == 105
    assert candidates[0]["sel"] == "홈"
    assert candidates[0]["odds"] == 1.55
    assert candidates[0]["price_source"] == "live_odds"
    assert source["generated_at"] == "2026-09-05T09:55:00+09:00"
    assert "today" not in calls


def test_changed_line_and_prices_are_taken_from_one_snapshot(monkeypatch):
    loader(monkeypatch, feed(market="언더오버", label="U 9.5", odds=[1.6, 2.2]))
    candidates = today_combo.legs_today(NOW)
    assert candidates[0]["market_label"] == "U 9.5"
    assert candidates[0]["sel"] == "언더"
    assert candidates[0]["odds"] == 1.6


def test_terminal_and_empty_live_markets_do_not_resurrect_old_candidates(monkeypatch):
    for result in ("홈승", "취소", "연기", "중단"):
        loader(monkeypatch, feed(result=result))
        assert today_combo.legs_today(NOW) == []
    loader(monkeypatch, {"markets": {}}, {"live": [{"status": "경기전"}]})
    assert today_combo._candidate_source()["rounds"] == []


def test_picks_fallback_preserves_selection_order_and_excludes_finished(monkeypatch):
    game = {"home": "LG", "away": "삼성", "date": "09.05(토) 18:00",
            "league": "KBO", "sport": "bs", "round": 105, "status": "경기전",
            "options": [{"게임번호": "10", "market": "승패", "선택": "원정", "배당": 2.3},
                        {"게임번호": "10", "market": "승패", "선택": "홈", "배당": 1.55}]}
    loader(monkeypatch, {}, {"generated_at": "2026-09-05T09:55:00+09:00", "live": [game]})
    source = today_combo._candidate_source()
    assert source["candidate_source"] == "picks_v2"
    assert today_combo.legs_today(NOW, source=source)[0]["sel"] == "홈"
    game["status"] = "정산"
    assert today_combo.legs_today(NOW) == []


def test_build_reports_actual_source_time(monkeypatch):
    calls = loader(monkeypatch, feed())
    monkeypatch.setattr(today_combo, "live_snapshot", lambda *args: {})
    monkeypatch.setattr(today_combo, "load_artifact", lambda *args: None)
    payload = today_combo.build()
    assert payload["source_generated_at"] == "2026-09-05T09:55:00+09:00"
    assert payload["candidate_source"] == "live_odds"
    assert "today" not in calls
