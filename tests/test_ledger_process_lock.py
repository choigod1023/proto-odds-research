"""Run on Linux CI: a killed writer must not block the next scheduled refresh."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import prediction_ledger as module


@pytest.mark.skipif(module.fcntl is None, reason="production Linux kernel lock")
def test_killed_writer_releases_lock_but_live_old_writer_keeps_it(tmp_path):
    path = tmp_path / "pregame.jsonl"
    source = str(Path(module.__file__).parent)
    code = (
        "import sys,time;sys.path.insert(0,sys.argv[1]);"
        "from prediction_ledger import PredictionLedger;"
        "ledger=PredictionLedger(sys.argv[2]);"
        "lock=ledger._lock();lock.__enter__();"
        "print('locked',flush=True);time.sleep(60)"
    )
    child = subprocess.Popen([sys.executable, "-c", code, source, str(path)],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "locked"
        guard = path.with_name(path.name + ".lock.guard")
        os.utime(guard, (1, 1))
        ledger = module.PredictionLedger(path, lock_timeout=0.05, stale_lock_after=0.01)
        with pytest.raises(module.LedgerLockTimeout):
            ledger.records()
        child.kill()
        child.wait(timeout=5)
        # No stale-file deletion, sleep or production history reset required.
        assert ledger.records() == []
        assert guard.exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()


def test_portable_fallback_still_releases_on_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "fcntl", None)
    ledger = module.PredictionLedger(tmp_path / "pregame.jsonl")
    with pytest.raises(RuntimeError):
        with ledger._lock():
            raise RuntimeError("test failure")
    assert not ledger.lock_path.exists()
    assert ledger.records() == []
