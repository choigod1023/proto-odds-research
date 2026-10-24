"""Owned scratch space with crash recovery; never adopt legacy directories."""
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile

MARKER = ".scratch-owner.json"
LEASE = ".scratch-lease"
PREFIXES = {"stage": "proodd-stage-", "publish": "collector-publish-"}


def linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


def lock(handle):
    if os.name == "nt":
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def reap(parent, kind):
    """Recover only marked, unlocked workspaces. Unknown/legacy paths stay put.

    Publish runs git children: only a different Linux boot proves none survived.
    Stage SQLite is used solely in the owning process, so its lease suffices.
    """
    parent = Path(parent).resolve(strict=True)
    removed = []
    for path in parent.glob(PREFIXES[kind] + "*"):
        lease = None
        try:
            if linked(path) or not path.is_dir() or path.resolve().parent != parent:
                continue
            if linked(path / MARKER) or linked(path / LEASE):
                continue
            meta = json.loads((path / MARKER).read_text())
            if meta != {"schema": 1, "kind": kind, "name": path.name,
                        "boot": meta.get("boot")}:
                continue
            if kind == "publish" and (not boot_id() or not meta["boot"] or meta["boot"] == boot_id()):
                continue
            lease = (path / LEASE).open("r+b")
            lock(lease)
            # Do not traverse links or unexpected data in a recovered stage.
            if any(linked(p) for p in path.rglob("*")):
                continue
            if kind == "stage" and any(p.name not in {
                MARKER, LEASE, "datasets.sqlite3", "datasets.sqlite3-journal",
                "datasets.sqlite3-wal", "datasets.sqlite3-shm"
            } or not p.is_file() for p in path.iterdir()):
                continue
            # Names are unique and never reused. On Windows release before unlink.
            if os.name == "nt":
                lease.close()
                lease = None
            shutil.rmtree(path)
            removed.append(path.name)
            logging.getLogger(__name__).info("Recovered abandoned %s workspace: %s", kind, path.name)
        except (OSError, ValueError, TypeError, AttributeError):
            continue  # Fail closed: malformed metadata, contention, permission.
        finally:
            if lease is not None:
                lease.close()
    return removed


@contextmanager
def workspace(parent, kind):
    parent = Path(parent).resolve(strict=True)
    reap(parent, kind)
    path = Path(tempfile.mkdtemp(prefix=PREFIXES[kind], dir=parent))
    lease = None
    try:
        lease = (path / LEASE).open("w+b")
        lease.write(b"0")
        lease.flush()
        lock(lease)
        # Publish marker last: incomplete allocation is never auto-deleted.
        (path / MARKER).write_text(json.dumps({"schema": 1, "kind": kind,
                                             "name": path.name, "boot": boot_id()}))
        yield path
    finally:
        if lease is not None:
            lease.close()
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            pass  # A concurrent reaper may finish after our lease is released.
