import sys
from datetime import datetime, timezone
from types import SimpleNamespace

from deploy import supervisor


def result(code=0):
    return SimpleNamespace(returncode=code, stdout="", stderr="")


def test_kbo_detail_refresh_preserves_player_info_cache_contract():
    name, cmd, critical, timeout = supervisor.KBO_DETAIL_REFRESH

    assert name == "KBO 완료경기 상세"
    assert cmd == [
        sys.executable,
        "-u",
        "src/game_detail.py",
        "baseball",
        "kbo",
        "2023",
        str(supervisor.CURRENT_KST_YEAR),
    ]
    assert f"kbo_baseball_{cmd[-2]}_{cmd[-1]}.json" == (
        f"kbo_baseball_2023_{supervisor.CURRENT_KST_YEAR}.json"
    )
    assert critical is False
    assert timeout == 1800
    assert supervisor.PUBLISH[0] is supervisor.KBO_DETAIL_REFRESH
    collect_cmd = supervisor.PUBLISH[1][1]
    assert collect_cmd[-1] == str(supervisor.CURRENT_KST_YEAR)
    assert collect_cmd[3:] == [
        str(year) for year in range(2023, supervisor.CURRENT_KST_YEAR + 1)]


def test_heavy_steps_roll_to_new_kst_year_without_process_restart():
    # These UTC instants are one minute apart but straddle midnight in Korea.
    before = supervisor.build_publish_steps(
        datetime(2026, 12, 31, 14, 59, tzinfo=timezone.utc))
    after = supervisor.build_publish_steps(
        datetime(2026, 12, 31, 15, 0, tzinfo=timezone.utc))

    assert before[0][1][-1] == "2026"
    assert after[0][1][-1] == "2027"
    assert before[1][1][3:] == ["2023", "2024", "2025", "2026"]
    assert after[1][1][3:] == ["2023", "2024", "2025", "2026", "2027"]
    assert f"kbo_baseball_2023_{after[0][1][-1]}.json" == (
        "kbo_baseball_2023_2027.json")


def test_push_remote_main_rebases_explicit_remote_branch(monkeypatch):
    calls = []
    results = iter([result(1), result(), result(), result()])

    def fake_sh(cmd, cwd=None, **kwargs):
        calls.append((cmd, cwd))
        return next(results)

    monkeypatch.setattr(supervisor, "sh", fake_sh)
    monkeypatch.setattr(supervisor, "REPO", "/repo")

    assert supervisor._push_remote_main().returncode == 0
    assert calls == [
        (["git", "push", "origin", "HEAD:main"], "/repo"),
        (["git", "fetch", "origin", "main"], "/repo"),
        (["git", "rebase", "--autostash", "origin/main"], "/repo"),
        (["git", "push", "origin", "HEAD:main"], "/repo"),
    ]


def test_push_remote_main_aborts_failed_rebase(monkeypatch):
    calls = []
    results = iter([result(1), result(), result(1), result()])

    def fake_sh(cmd, cwd=None, **kwargs):
        calls.append(cmd)
        return next(results)

    monkeypatch.setattr(supervisor, "sh", fake_sh)

    assert supervisor._push_remote_main().returncode == 1
    assert calls[-1] == ["git", "rebase", "--abort"]
    assert calls.count(["git", "push", "origin", "HEAD:main"]) == 1
