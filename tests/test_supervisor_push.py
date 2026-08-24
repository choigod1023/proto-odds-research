from types import SimpleNamespace

from deploy import supervisor


def result(code=0):
    return SimpleNamespace(returncode=code, stdout="", stderr="")


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