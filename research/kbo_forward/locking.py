"""Process-scoped OS lock; crash releases the lock without deleting evidence."""
from contextlib import contextmanager
from pathlib import Path
import os

@contextmanager
def single_writer(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell()==0:stream.write(b"0");stream.flush()
        stream.seek(0)
        try:
            if os.name=="nt":
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except (OSError,BlockingIOError):
            yield False
            return
        try:yield True
        finally:
            stream.seek(0)
            if os.name=="nt":msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
