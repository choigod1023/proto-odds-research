from src import odds_live


def test_collect_uses_persisted_rounds_when_file_cache_is_empty(monkeypatch):
    monkeypatch.setattr(odds_live, "_session", lambda: object())
    monkeypatch.setattr(odds_live, "_start_hint", lambda season: 1)
    seen = {}

    def find(_session, year, hint):
        seen.update(year=year, hint=hint)
        return []

    monkeypatch.setattr(odds_live, "find_live_rounds", find)
    result = odds_live.collect({"rounds": [101, 102, 104]})

    assert seen["hint"] == 101
    assert result["rounds"] == []


def test_collect_does_not_let_stale_file_cache_override_database_rounds(monkeypatch):
    monkeypatch.setattr(odds_live, "_session", lambda: object())
    monkeypatch.setattr(odds_live, "_start_hint", lambda season: 70)
    seen = {}
    monkeypatch.setattr(
        odds_live, "find_live_rounds",
        lambda _session, _year, hint: seen.setdefault("hint", hint) and [],
    )

    odds_live.collect({"rounds": [101, 102, 104]})

    assert seen["hint"] == 101


def test_main_refreshes_picks_immediately_after_persist(monkeypatch):
    collected = {"generated_at": "2026-08-31T00:00:00+00:00", "n": 1,
                 "rounds": [104], "markets": {}, "odds": {}}
    calls = []
    monkeypatch.setattr(odds_live, "collect", lambda _picks=None, _odds=None: collected)
    monkeypatch.setattr(odds_live, "load_artifact", lambda name, path: {})
    monkeypatch.setattr(odds_live, "merge_market_history",
                        lambda current, previous, picks: current)
    monkeypatch.setattr(odds_live, "persist_artifact",
                        lambda *args, **kwargs: calls.append(("persist", args[1])))
    monkeypatch.setattr(odds_live, "refresh_once",
                        lambda data: calls.append(("refresh", data)) or 0)
    monkeypatch.setattr(odds_live, "export_site_artifacts",
                        lambda: calls.append(("export", None)) or [])

    assert odds_live.main(["odds_live.py"]) == 0
    assert calls == [("persist", collected), ("refresh", collected),
                     ("export", None)]


def test_main_keeps_last_prices_and_still_refreshes_on_empty_poll(monkeypatch):
    """빈 폴링이 와도 raise 로 멈추지 않고 직전 배당을 유지하며 하위 갱신을 돌린다."""
    empty = {"generated_at": "2026-09-03T02:30:00+00:00", "n": 0,
             "rounds": [], "markets": {}, "odds": {}, "history": {}}
    previous_odds = {"generated_at": "2026-09-03T02:00:00+00:00", "n": 1,
                     "rounds": [104],
                     "odds": {"104": {"9001": [1.8, 2.0], "9002": [1.5, 2.6]}},
                     "markets": {"104": {"9001": {"market": "승패"}}}}
    calls = []
    monkeypatch.setattr(odds_live, "collect", lambda _picks=None, _odds=None: empty)
    monkeypatch.setattr(odds_live, "load_artifact",
                        lambda name, path: previous_odds if name == "live_odds" else {})
    monkeypatch.setattr(odds_live, "merge_market_history",
                        lambda current, previous, picks: current)
    monkeypatch.setattr(odds_live, "persist_artifact",
                        lambda *args, **kwargs: calls.append(("persist", args[1])))
    monkeypatch.setattr(odds_live, "refresh_once",
                        lambda data: calls.append(("refresh", data)) or 0)
    monkeypatch.setattr(odds_live, "export_site_artifacts",
                        lambda: calls.append(("export", None)) or [])

    assert odds_live.main(["odds_live.py"]) == 0
    assert [name for name, _ in calls] == ["persist", "refresh", "export"]
    persisted = calls[0][1]
    assert persisted["odds"] == {"104": {"9001": [1.8, 2.0], "9002": [1.5, 2.6]}}
    assert persisted["rounds"] == [104] and persisted["n"] == 2  # 실경기 2건으로 재계산
    # 생성시각은 이번 폴링의 새 값이어야 화면 신선도가 진행된다.
    assert persisted["generated_at"] == "2026-09-03T02:30:00+00:00"


def test_market_history_records_price_and_line_changes_with_probabilities():
    previous = {
        "history": {"103": {"8071": [{
            "observed_at": "2026-08-31T03:24:13+00:00", "market": "핸디캡",
            "label": "H -29.5", "line": -29.5, "odds": [1.74, 1.78],
            "probabilities": [0.5065, 0.4935],
        }]}}
    }
    current = {
        "generated_at": "2026-08-31T04:55:25+00:00",
        "markets": {"103": {"8071": {
            "market": "핸디캡", "label": "H -31.5", "odds": [1.74, 1.78],
        }}},
    }

    merged = odds_live.merge_market_history(current, previous)
    entries = merged["history"]["103"]["8071"]

    assert [entry["line"] for entry in entries] == [-29.5, -31.5]
    assert entries[-1]["probabilities"] == [0.5065, 0.4935]


def test_market_history_seeds_old_line_from_picks_before_first_live_revision():
    current = {
        "generated_at": "2026-08-31T04:55:25+00:00",
        "markets": {"103": {"8071": {
            "market": "핸디캡", "label": "H -31.5", "odds": [1.74, 1.78],
        }}},
    }
    picks = {"generated_at": "2026-08-31T04:22:45+00:00", "live": [{
        "round": 103, "options": [
            {"게임번호": "8071", "market": "핸디캡", "label": "H -29.5",
             "배당": 1.74},
            {"게임번호": "8071", "market": "핸디캡", "label": "H -29.5",
             "배당": 1.78},
        ],
    }]}

    merged = odds_live.merge_market_history(current, {}, picks)

    assert [entry["line"] for entry in merged["history"]["103"]["8071"]] == [-29.5, -31.5]


def test_collect_skips_full_scan_when_previous_rounds_are_fresh(monkeypatch):
    """60초마다 12개 회차를 훑지 않도록, 직전 산출물이 신선하면 스캔을 건너뛴다."""
    from datetime import datetime, timezone
    monkeypatch.setattr(odds_live, "_session", lambda: object())
    monkeypatch.setattr(odds_live, "_start_hint", lambda season: 1)
    scanned = []
    monkeypatch.setattr(odds_live, "find_live_rounds",
                        lambda *_a: scanned.append(True) or [999])
    monkeypatch.setattr(odds_live, "_fetch",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no net")))

    fresh = {"generated_at": datetime.now(timezone.utc).isoformat(),
             "rounds": [104, 105]}
    result = odds_live.collect({"rounds": [104]}, fresh)

    assert scanned == []                      # 전체 스캔 안 함
    assert set(result["rounds"]) >= {104, 105}


def test_collect_still_scans_when_previous_rounds_are_stale(monkeypatch):
    monkeypatch.setattr(odds_live, "_session", lambda: object())
    monkeypatch.setattr(odds_live, "_start_hint", lambda season: 1)
    scanned = []
    monkeypatch.setattr(odds_live, "find_live_rounds",
                        lambda *_a: scanned.append(True) or [])
    monkeypatch.setattr(odds_live, "_fetch",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no net")))

    stale = {"generated_at": "2026-09-04T00:00:00+00:00", "rounds": [104]}
    odds_live.collect({"rounds": [104]}, stale)

    assert scanned == [True]
