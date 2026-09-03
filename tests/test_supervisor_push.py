import subprocess
from types import SimpleNamespace

from deploy import supervisor


def result(code=0, stdout=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr="")


def test_push_data_exports_db_artifacts_to_site_before_commit(monkeypatch):
    """DB 정본을 docs/data 로 내보낸 뒤에야 git status 를 확인해야 사이트가 갱신된다."""
    order = []
    monkeypatch.setattr(supervisor, "_export_site_artifacts",
                        lambda: order.append("export"))

    def fake_sh(args, cwd=None, check=False):
        order.append(" ".join(args[:2]))
        return result(0, "")            # git status --porcelain: 변경 없음 → 조기 종료

    monkeypatch.setattr(supervisor, "sh", fake_sh)
    monkeypatch.setattr(supervisor, "REPO", "/repo")

    supervisor.push_data()

    assert order[0] == "export"
    assert order[1].startswith("git status")


def test_push_remote_main_rebases_explicit_remote_branch(monkeypatch):
    calls = []
    results = iter([result(1), result(stdout="abc123\n"), result(), result(), result()])

    def fake_sh(cmd, cwd=None, **kwargs):
        calls.append((cmd, cwd))
        return next(results)

    monkeypatch.setattr(supervisor, "sh", fake_sh)
    monkeypatch.setattr(supervisor, "REPO", "/repo")

    assert supervisor._push_remote_main().returncode == 0
    assert calls == [
        (["git", "push", "origin", "HEAD:main"], "/repo"),
        (["git", "rev-parse", "HEAD"], "/repo"),
        (["git", "fetch", "origin", "main"], "/repo"),
        (["git", "rebase", "--autostash", "origin/main"], "/repo"),
        (["git", "push", "origin", "HEAD:main"], "/repo"),
    ]


def test_push_remote_main_uses_snapshot_after_failed_rebase(monkeypatch):
    calls = []
    results = iter([result(1), result(stdout="snapshot123\n"), result(), result(1), result()])
    recovered = []

    def fake_sh(cmd, cwd=None, **kwargs):
        calls.append(cmd)
        return next(results)

    monkeypatch.setattr(supervisor, "sh", fake_sh)
    monkeypatch.setattr(supervisor, "_publish_snapshot_on_remote",
                        lambda snapshot: recovered.append(snapshot) or result())

    assert supervisor._push_remote_main().returncode == 0
    assert calls[-1] == ["git", "rebase", "--abort"]
    assert calls.count(["git", "push", "origin", "HEAD:main"]) == 1
    assert recovered == ["snapshot123"]


def test_existing_repo_uses_startup_synchronizer(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    synchronized = []

    monkeypatch.setattr(supervisor, "REPO", repo)
    monkeypatch.setattr(supervisor, "_configure", lambda _: None)
    monkeypatch.setattr(supervisor, "_sync_existing_repo",
                        lambda: synchronized.append(True) or True)

    assert supervisor.ensure_repo() is True
    assert synchronized == [True]


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_conflicting_collector_data_is_rebuilt_on_latest_remote(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    collector = tmp_path / "collector"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", "-b", "main", str(seed))
    for repo in (seed,):
        _git(repo, "config", "user.name", "test")
        _git(repo, "config", "user.email", "test@example.com")
    _write(seed / "source.txt", "base\n")
    _write(seed / "data" / "raw.txt", "base data\n")
    _write(seed / "docs" / "data" / "picks_v2.json", '{"generated_at":"base"}\n')
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "base")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "main")
    _git(tmp_path, "clone", "-b", "main", str(remote), str(collector))
    _git(collector, "config", "user.name", "collector")
    _git(collector, "config", "user.email", "collector@example.com")

    _write(collector / "data" / "raw.txt", "collector data\n")
    _write(collector / "docs" / "data" / "picks_v2.json",
           '{"generated_at":"fresh"}\n')
    _git(collector, "add", "data", "docs/data")
    _git(collector, "commit", "-m", "collector snapshot")

    _write(seed / "source.txt", "new source\n")
    _write(seed / "data" / "raw.txt", "remote data\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "remote change")
    _git(seed, "push", "origin", "main")

    monkeypatch.setattr(supervisor, "REPO", collector)
    pushed = supervisor._push_remote_main()
    assert pushed.returncode == 0, pushed.stderr

    _git(seed, "pull", "--ff-only")
    assert (seed / "source.txt").read_text(encoding="utf-8") == "new source\n"
    assert (seed / "data" / "raw.txt").read_text(encoding="utf-8") == "collector data\n"
    assert '"fresh"' in (seed / "docs" / "data" / "picks_v2.json").read_text(encoding="utf-8")


def test_startup_sync_keeps_volume_data_and_updates_source(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    collector = tmp_path / "collector"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "init", "-b", "main", str(seed))
    _git(seed, "config", "user.name", "test")
    _git(seed, "config", "user.email", "test@example.com")
    _write(seed / "source.txt", "old source\n")
    _write(seed / "data" / "raw.txt", "old data\n")
    _write(seed / "docs" / "data" / "picks_v2.json", '{"generated_at":"old"}\n')
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "base")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-u", "origin", "main")
    _git(tmp_path, "clone", "-b", "main", str(remote), str(collector))
    _git(collector, "config", "user.name", "collector")
    _git(collector, "config", "user.email", "collector@example.com")

    _write(collector / "data" / "raw.txt", "fresh volume data\n")
    _write(collector / "docs" / "data" / "picks_v2.json",
           '{"generated_at":"fresh-volume"}\n')
    _write(collector / "data" / "cache.tmp", "untracked cache\n")
    _write(seed / "source.txt", "new remote source\n")
    _write(seed / "data" / "raw.txt", "remote data\n")
    # 수집 스냅샷에는 없고 새 main에만 생긴 정적 설정이다. 재시작 동기화가
    # data/ 전체를 옛 snapshot으로 바꾸면 이 파일이 사라진다.
    _write(seed / "data" / "static" / "venues.csv", "new remote config\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "remote source update")
    _git(seed, "push", "origin", "main")

    monkeypatch.setattr(supervisor, "REPO", collector)
    monkeypatch.setattr(supervisor, "_configure", lambda _: None)
    assert supervisor.ensure_repo() is True

    assert _git(collector, "branch", "--show-current").stdout.strip() == "main"
    assert (collector / "source.txt").read_text(encoding="utf-8") == "new remote source\n"
    assert (collector / "data" / "raw.txt").read_text(encoding="utf-8") == "fresh volume data\n"
    assert (collector / "data" / "static" / "venues.csv").read_text(
        encoding="utf-8") == "new remote config\n"
    assert (collector / "data" / "cache.tmp").read_text(encoding="utf-8") == "untracked cache\n"
    _git(seed, "pull", "--ff-only")
    assert (seed / "data" / "raw.txt").read_text(encoding="utf-8") == "fresh volume data\n"


def test_restart_refreshes_site_before_heavy_pipeline(tmp_path, monkeypatch):
    (tmp_path / "data" / "processed").mkdir(parents=True)
    (tmp_path / "data" / "processed" / "games.csv").write_text("ok\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(supervisor, "REPO", tmp_path)
    monkeypatch.setattr(supervisor, "PUBLISH_LIGHT", ["light"])
    monkeypatch.setattr(supervisor, "PUBLISH_ENRICH", ["enrich"])
    monkeypatch.setattr(supervisor, "PUBLISH", ["heavy"])
    monkeypatch.setattr(supervisor, "_run_steps", lambda steps: calls.append(steps[0]))
    monkeypatch.setattr(supervisor, "push_data", lambda: calls.append("push"))

    supervisor._run_publish_cycle(0)

    assert calls == ["light", "push", "heavy", "light", "push", "enrich", "push"]


def test_restart_clears_nested_prediction_ledger_lock(tmp_path, monkeypatch):
    nested = tmp_path / "data" / "raw" / "prediction_ledger" / "pregame.jsonl.lock"
    nested.parent.mkdir(parents=True)
    nested.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(supervisor, "REPO", tmp_path)

    supervisor._clear_stale_locks()

    assert not nested.exists()


def test_heavy_collectors_are_staggered_after_restart():
    names = {name for name, _, _ in supervisor.LOOPERS}
    assert set(supervisor.LOOPER_START_DELAYS) <= names
    assert supervisor.LOOPER_START_DELAYS["선수·팀 정보"] >= 180
    assert supervisor.LOOPER_START_DELAYS["공개 픽스터"] > supervisor.LOOPER_START_DELAYS["선수·팀 정보"]
    assert supervisor.LOOPER_START_DELAYS["무료 날씨"] > supervisor.LOOPER_START_DELAYS["공개 픽스터"]


def test_daily_and_publish_pipeline_share_one_memory_gate():
    assert supervisor._pipeline_lock is not supervisor._anonymous_bets_lock
    assert hasattr(supervisor._pipeline_lock, "acquire")


def test_loopers_are_one_shot_so_child_memory_is_reclaimed():
    for _name, command, interval in supervisor.LOOPERS:
        assert "--loop" not in command
        assert interval >= 60


def test_large_collectors_share_publish_memory_gate():
    assert {"선수·팀 정보", "공개 픽스터", "무료 날씨"} <= supervisor.MEMORY_HEAVY_LOOPERS
    assert hasattr(supervisor._memory_heavy_lock, "acquire")


def test_live_server_cors_accepts_frontend_cache_control_header():
    allowed = {value.strip().lower() for value in supervisor.CORS_REQUEST_HEADERS.split(",")}
    assert "cache-control" in allowed
