"""Launch the pinned KBO collector from pythonw without a console window."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--m1-state", type=Path)
    parser.add_argument("--watch-lineup-revisions", action="store_true")
    args = parser.parse_args()
    args.state.mkdir(parents=True, exist_ok=True)
    runner = args.runtime / "research/kbo_forward/runner.py"
    command = [str(args.python), "-B", "-X", "utf8", str(runner), "--state", str(args.state)]
    if args.m1_state is not None:
        command += ["--m1-state", str(args.m1_state)]

    if args.watch_lineup_revisions:
        command.append("--watch-lineup-revisions")

    with (args.state / "last-process.log").open("wb") as log:
        if not runner.is_file():
            log.write(b"Pinned collector runtime is missing\n")
            return 1
        try:
            return subprocess.run(
                command,
                cwd=args.runtime,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).returncode
        except OSError as exc:
            log.write(f"Collector launch failed: {exc}\n".encode("utf-8", errors="replace"))
            return 1


if __name__ == "__main__":
    sys.exit(main())
