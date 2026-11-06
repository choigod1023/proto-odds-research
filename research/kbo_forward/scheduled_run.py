"""Launch the pinned KBO collector from pythonw without a console window."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def compare_when_due(args, log, env):
    output = args.state / "lineup-paired-comparison.json"
    if output.exists() and (time.time() - output.stat().st_mtime < 1800
                            or not any((args.state / "lineup-m0-captures").glob("*.jsonl"))):
        return 0
    command = [str(args.python), "-B", "-X", "utf8", "-m",
               "research.kbo_forward.paired_comparison", "--state", str(args.state)]
    if args.operating_ledger and args.operating_ledger.is_file():
        command.extend(["--operating-ledger", str(args.operating_ledger)])
    result = subprocess.run(command, cwd=args.runtime, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=log, timeout=60,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        return result.returncode
    json.loads(result.stdout)
    temp = output.with_suffix(".tmp")
    temp.write_bytes(result.stdout)
    temp.replace(output)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--m1-state", type=Path)
    parser.add_argument("--watch-lineup-revisions", action="store_true")
    parser.add_argument("--paired-comparison", action="store_true")
    parser.add_argument("--operating-ledger", type=Path)
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
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}
            code = subprocess.run(
                command,
                cwd=args.runtime,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).returncode
            if code == 0 and args.paired_comparison:
                return compare_when_due(args, log, env)
            return code
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            log.write(f"Collector launch failed: {exc}\n".encode("utf-8", errors="replace"))
            return 1


if __name__ == "__main__":
    sys.exit(main())
