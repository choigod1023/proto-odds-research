import subprocess
from types import SimpleNamespace

from deploy import supervisor


def result(code=0, stdout=""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr="")


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


def test_existing_repo_aborts_failed_startup_rebase(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    calls = []
    results = iter([
        result(stdout="HEAD\n"),  # rev-parse
        result(),                  # checkout -B main
        result(1),                 # pull --rebase
        result(),                  # rebase --abort
    ])

    def fake_sh(cmd, cwd=None, **kwargs):
        calls.append(cmd)
        return next(results)

    monkeypatch.setattr(supervisor, "REPO", repo)
    monkeypatch.setattr(supervisor, "_configure", lambda _: None)
    monkeypatch.setattr(supervisor, "sh", fake_sh)

    assert supervisor.ensure_repo() is True
    assert calls[-1] == ["git", "rebase", "--abort"]


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
