import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import temporary_workspace as scratch


def crashed(parent, kind="stage"):
    code = """
import os, sys
from temporary_workspace import workspace
with workspace(sys.argv[1], sys.argv[2]) as path:
    (path / 'datasets.sqlite3').write_bytes(b'x' * 1048576)
    print(path, flush=True)
    os._exit(9)
"""
    env = dict(os.environ, PYTHONPATH=str(Path(scratch.__file__).parent))
    result = subprocess.run([sys.executable, "-c", code, str(parent), kind],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 9
    return Path(result.stdout.strip())


def test_real_crash_residue_is_recovered_before_next_stage(tmp_path):
    residue = crashed(tmp_path)
    assert (residue / "datasets.sqlite3").stat().st_size == 1048576
    with scratch.workspace(tmp_path, "stage") as current:
        assert not residue.exists()
        assert current.exists()
    assert not current.exists()


def test_active_stage_and_live_db_are_preserved(tmp_path):
    database = tmp_path / "runtime.sqlite3"
    database.write_bytes(b"ledger")
    with scratch.workspace(tmp_path, "stage") as active:
        assert scratch.reap(tmp_path, "stage") == []
        assert active.exists()
    assert database.read_bytes() == b"ledger"


def test_exception_cleans_own_workspace(tmp_path):
    with pytest.raises(RuntimeError):
        with scratch.workspace(tmp_path, "stage") as path:
            raise RuntimeError("publish failed")
    assert not path.exists()


def test_legacy_malformed_and_unexpected_data_fail_closed(tmp_path):
    legacy = tmp_path / "proodd-stage-legacy"
    legacy.mkdir()
    malformed = crashed(tmp_path)
    (malformed / scratch.MARKER).write_text("[]")
    protected = crashed(tmp_path)
    (protected / "prediction-ledger.sqlite3").write_bytes(b"keep")
    assert scratch.reap(tmp_path, "stage") == []
    assert legacy.exists() and malformed.exists() and protected.exists()


def test_publish_requires_different_known_boot(tmp_path, monkeypatch):
    residue = crashed(tmp_path, "publish")
    meta = json.loads((residue / scratch.MARKER).read_text())
    meta["boot"] = "previous"
    (residue / scratch.MARKER).write_text(json.dumps(meta))
    monkeypatch.setattr(scratch, "boot_id", lambda: "previous")
    assert scratch.reap(tmp_path, "publish") == []
    monkeypatch.setattr(scratch, "boot_id", lambda: None)
    assert scratch.reap(tmp_path, "publish") == []
    monkeypatch.setattr(scratch, "boot_id", lambda: "current")
    assert scratch.reap(tmp_path, "publish") == [residue.name]


def test_symlink_never_followed(tmp_path):
    target = tmp_path / "real-db"
    target.mkdir()
    link = tmp_path / "proodd-stage-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    assert scratch.reap(tmp_path, "stage") == []
    assert target.exists()


def test_publish_lock_wins_even_if_boot_marker_is_old(tmp_path, monkeypatch):
    monkeypatch.setattr(scratch, "boot_id", lambda: "first")
    with scratch.workspace(tmp_path, "publish") as active:
        monkeypatch.setattr(scratch, "boot_id", lambda: "second")
        assert scratch.reap(tmp_path, "publish") == []
        assert active.exists()


def test_partial_git_worktree_creation_is_cleaned(tmp_path, monkeypatch):
    from deploy import supervisor
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(supervisor, "REPO", repo)
    monkeypatch.setattr(supervisor, "_snapshot_delta_paths", lambda *a:
                        ([], subprocess.CompletedProcess([], 0, "", "")))
    targets = []
    def failed_git(args, **kwargs):
        assert args[:3] == ["git", "worktree", "add"]
        target = Path(args[4])
        targets.append(target)
        target.mkdir()
        (target / "partial").write_text("incomplete checkout")
        return subprocess.CompletedProcess(args, 1, "", "disk full")
    monkeypatch.setattr(supervisor, "sh", failed_git)
    assert supervisor._publish_snapshot_on_remote("snapshot").returncode == 1
    assert targets and not targets[0].parent.exists()
    assert repo.exists()
