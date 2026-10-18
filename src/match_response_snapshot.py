"""Atomic, non-executable restart cache of compressed HTTP responses.

This is disposable serving state, never a prediction/ledger source. Bump VERSION
when the response contract changes. Original bodies and revisions stay untouched.
"""
import json
import os
from pathlib import Path
import tempfile
import zipfile

VERSION = 1
MAX_BYTES = 128 * 1024 * 1024


def save(path, ready, revisions, revision, games):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
                metadata = {'version': VERSION, 'revision': revision,
                            'revisions': revisions, 'days': {k: v[0] for k, v in ready.items()},
                            'keys': list(games)}
                archive.writestr('metadata', json.dumps(metadata))
                for scope, (_, body) in ready.items():
                    archive.writestr('list/' + scope, body)
                for key, body in games.items():
                    archive.writestr('detail/' + key, body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load(path, day):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('restart cache too large')
    with zipfile.ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_BYTES:
            raise ValueError('restart cache expands beyond limit')
        meta = json.loads(archive.read('metadata'))
        if meta['version'] != VERSION or set(meta['days']) - {'recent', 'all', 'performance'}:
            raise ValueError('incompatible restart cache')
        from datetime import date
        if not meta['days'] or any(not 0 <= (date.fromisoformat(day) - date.fromisoformat(d)).days <= 1
                                   for d in meta['days'].values()):
            raise ValueError('restart cache is outside the last-good day window')
        if set(meta['revisions']) != set(meta['days']) or set(meta['revisions'].values()) != {meta['revision']}:
            raise ValueError('mixed restart revisions')
        ready = {k: (d, archive.read('list/' + k)) for k, d in meta['days'].items()}
        games = {k: archive.read('detail/' + k) for k in meta['keys']}
    return ready, meta['revisions'], meta['revision'], games
