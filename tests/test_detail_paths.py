import sys
import json
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from detail_paths import latest_detail_path  # noqa: E402


def test_latest_detail_path_rolls_forward_to_highest_end_year(tmp_path):
    older = tmp_path / "kbo_baseball_2023_2026.json"
    newest = tmp_path / "kbo_baseball_2023_2027.json"
    unrelated = tmp_path / "kbo_batters_2023_2099.json"
    for path in (older, newest, unrelated):
        path.write_text("{}", encoding="utf-8")

    assert latest_detail_path("kbo", "baseball", root=tmp_path) == newest


def test_missing_detail_path_uses_call_time_kst_year(tmp_path):
    before = latest_detail_path(
        "kbo", "baseball", root=tmp_path,
        now=datetime(2026, 12, 31, 14, 59, tzinfo=timezone.utc))
    after = latest_detail_path(
        "kbo", "baseball", root=tmp_path,
        now=datetime(2026, 12, 31, 15, 0, tzinfo=timezone.utc))

    assert before.name == "kbo_baseball_2023_2026.json"
    assert after.name == "kbo_baseball_2023_2027.json"


def _write_pitcher_detail(path, year, name):
    path.write_text(json.dumps({
        "g1": {
            "gameId": f"{year}0101",
            "date": f"{year}-01-01",
            "home": "홈", "away": "원정",
            "home_score": 2, "away_score": 1,
            "data": {
                "home": [{"name": name, "inn": "6", "er": 1, "hr": 0,
                          "bb": 1, "kk": 6, "hit": 4}],
                "away": [{"name": "상대", "inn": "6", "er": 2, "hr": 1,
                          "bb": 1, "kk": 5, "hit": 5}],
            },
        },
    }, ensure_ascii=False), encoding="utf-8")


def test_pitcher_loaders_recheck_path_without_module_reload(tmp_path, monkeypatch):
    import pitcher_er
    import pitcher_xfip

    old = tmp_path / "kbo_baseball_2023_2026.json"
    new = tmp_path / "kbo_baseball_2023_2027.json"
    _write_pitcher_detail(old, 2026, "이전선발")
    _write_pitcher_detail(new, 2027, "새해선발")
    selected = {"path": old}
    monkeypatch.setattr(pitcher_er, "detail_path", lambda: selected["path"])
    monkeypatch.setattr(pitcher_xfip, "detail_path", lambda: selected["path"])

    assert pitcher_er.load_detail().iloc[0]["home_sp"]["name"] == "이전선발"
    assert pitcher_xfip.load_full().iloc[0]["home_sp"]["name"] == "이전선발"
    selected["path"] = new
    assert pitcher_er.load_detail().iloc[0]["home_sp"]["name"] == "새해선발"
    assert pitcher_xfip.load_full().iloc[0]["home_sp"]["name"] == "새해선발"


def test_starter_proxy_audit_rechecks_default_detail_path(tmp_path, monkeypatch):
    import accuracy_pareto

    seen = []

    def current_path():
        year = 2026 + len(seen)
        path = tmp_path / f"kbo_baseball_2023_{year}.json"
        seen.append(path)
        return path

    monkeypatch.setattr(accuracy_pareto, "kbo_detail_path", current_path)
    missing_snapshots = tmp_path / "missing.csv"
    assert accuracy_pareto.audit_starter_proxy(missing_snapshots)["status"] == "unavailable"
    assert accuracy_pareto.audit_starter_proxy(missing_snapshots)["status"] == "unavailable"
    assert [path.name for path in seen] == [
        "kbo_baseball_2023_2026.json", "kbo_baseball_2023_2027.json"]
