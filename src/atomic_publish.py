"""Fail-closed, atomic JSON publishing for generated site artifacts."""
from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any


class PublishGuardError(RuntimeError):
    """Raised when a transiently empty collection must not replace good data."""


def publish_nonempty_json(
        path: Path,
        document: Mapping[str, Any],
        *,
        rounds: Collection[Any],
        records: Collection[Any],
        artifact_name: str,
) -> Path:
    """Publish a non-empty JSON artifact using an atomic same-directory swap.

    Empty rounds or records usually mean that the live-round lookup, cache, or
    per-round fetch failed.  Reject them before touching the destination so a
    healthy previous artifact remains available.  The serialized document is
    flushed and fsynced to a temporary file in the destination directory, then
    replaced atomically so readers never observe a partially written JSON file.
    """
    if not rounds:
        raise PublishGuardError(
            f"{artifact_name}: 대상 회차가 비어 있어 기존 파일을 보존합니다")
    if not records:
        raise PublishGuardError(
            f"{artifact_name}: 생성 경기 목록이 비어 있어 기존 파일을 보존합니다")

    # Serialize first: an encoding/type failure must not create even a temp file.
    payload = json.dumps(document, ensure_ascii=False, indent=1)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
        temporary.chmod(mode)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target
