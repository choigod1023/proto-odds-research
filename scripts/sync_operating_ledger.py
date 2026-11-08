"""Read a stable operating ledger prefix over Fly SSH and publish a verified local export."""
from pathlib import Path
import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from threading import Timer
import time
import zlib

REMOTE = """
import base64, hashlib, os, sqlite3, time, zlib
try:
    os.nice(10)
except OSError:
    pass
with sqlite3.connect('file:/data/proodd.sqlite3?mode=ro', uri=True, timeout=5) as conn:
    conn.execute('PRAGMA query_only=ON')
    conn.execute('PRAGMA cache_size=-1024')
    conn.execute('PRAGMA mmap_size=0')
    stop = conn.execute('SELECT max(ledger_sequence) FROM prediction_records').fetchone()[0] or 0
    if AFTER > stop:
        raise ValueError('remote sequence went backwards')
    if AFTER:
        previous = conn.execute('SELECT record_hash FROM prediction_records WHERE ledger_sequence=?', (AFTER,)).fetchone()
        if previous != (AFTER_HASH,):
            raise ValueError('remote chain no longer extends local verified export')
    if stop - AFTER > 50_000:
        raise ValueError('export exceeds row limit')
    print('ledger-start', AFTER, stop, flush=True)
    stream = zlib.compressobj(level=1, wbits=31)
    digest = hashlib.sha256()
    sequence = AFTER
    def emit(data):
        for offset in range(0, len(data), 24 * 1024):
            print('ledger-data', base64.b64encode(data[offset:offset + 24 * 1024]).decode('ascii'), flush=True)
    while sequence < stop:
        rows = conn.execute('SELECT ledger_sequence,record_json FROM prediction_records WHERE ledger_sequence>? AND ledger_sequence<=? ORDER BY ledger_sequence LIMIT 32', (sequence,stop)).fetchall()
        if not rows:
            raise ValueError('ledger sequence disappeared')
        for current, raw in rows:
            if current != sequence + 1 or '\\n' in raw or '\\r' in raw:
                raise ValueError('invalid ledger framing')
            data = raw.encode('utf-8') + b'\\n'
            digest.update(data)
            emit(stream.compress(data))
            sequence = current
        time.sleep(0.01)
    emit(stream.flush())
    print('ledger-end', sequence, digest.hexdigest(), flush=True)
"""


def verify(path, src):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    if os.environ.get("PROODD_DB_PATH"):
        raise ValueError("refusing to verify with PROODD_DB_PATH set")
    from prediction_ledger import PredictionLedger
    records = PredictionLedger(path)._read_verified_storage()
    return len(records), records[-1]["record_hash"] if records else None


def run(output, src):
    marker = output.with_suffix(".receipt.json")
    if output.exists():
        after, tail = verify(output, src)
    else:
        after, tail = 0, None
    program = f"AFTER={after!r}\nAFTER_HASH={tail!r}\n" + REMOTE
    code = base64.b64encode(program.encode("utf-8")).decode("ascii")
    command = f'python -c "exec(__import__(\'base64\').b64decode(\'{code}\'))"'
    import shutil
    fly = shutil.which("flyctl") or str(Path(os.environ["USERPROFILE"]) / ".fly/bin/flyctl.exe")
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".operating-ledger-", delete=False) as pending:
        target = Path(pending.name)
        try:
            if output.exists():
                with output.open("rb") as old:
                    shutil.copyfileobj(old, pending, length=1024 * 1024)
            with tempfile.TemporaryFile() as diagnostics:
                with subprocess.Popen([fly, "ssh", "console", "-a", "proto-odds-collector",
                                       "-C", command], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=diagnostics,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
                    deadline = Timer(180, child.kill)
                    deadline.daemon = True
                    deadline.start()
                    try:
                        announced, added_bytes = receive(child.stdout, pending, after)
                        child.wait(timeout=10)
                    except BaseException:
                        child.kill()
                        raise
                    finally:
                        deadline.cancel()
            pending.flush()
            os.fsync(pending.fileno())
            pending.close()
            count, tail = verify(target, src) if added_bytes else (after, tail)
            if count != announced:
                raise ValueError("verified record count differs from captured high-water")
            if added_bytes or not output.exists():
                target.replace(output)
            receipt = {"verified_rows": count, "last_hash": tail, "added_rows": count - after,
                       "added_bytes": added_bytes, "verified_at": int(time.time())}
            temporary = marker.with_suffix(".tmp")
            temporary.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
            temporary.replace(marker)
            return receipt
        finally:
            if not pending.closed:
                pending.close()
            target.unlink(missing_ok=True)


def receive(stream, pending, after):
    inflater = zlib.decompressobj(wbits=31)
    received = hashlib.sha256()
    announced = None
    added_bytes = 0
    for line in stream:
        parts = line.split()
        if not parts:
            continue
        tag = parts[0]
        if tag == b"ledger-start" and announced is None:
            begin, announced = map(int, parts[1:])
            if begin != after or announced < after:
                raise ValueError("unexpected remote ledger range")
        elif tag == b"ledger-data" and announced is not None:
            raw = inflater.decompress(base64.b64decode(parts[1], validate=True), 24 * 1024 * 1024)
            added_bytes += len(raw)
            if inflater.unconsumed_tail or added_bytes > 512 * 1024 * 1024:
                raise ValueError("oversize ledger export")
            received.update(raw)
            pending.write(raw)
        elif tag == b"ledger-end" and announced is not None:
            if (int(parts[1]) == announced and received.hexdigest() == parts[2].decode()
                    and inflater.eof and not inflater.unused_data):
                return announced, added_bytes
            break
    raise ValueError("incomplete or corrupted SSH export")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--src", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.src)))
