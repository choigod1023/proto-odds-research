"""Capture a current football round and refresh one older round per local run."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import timedelta
import json
from pathlib import Path
import re
import subprocess
import sys
import traceback

import requests

if __package__:
    from .forward_research_cycle import atomic_json, fetch_public, single_writer
else:
    from forward_research_cycle import atomic_json, fetch_public, single_writer
from live_market_refresh import KST, _game_kickoff

ROUND = re.compile(r"\d{6}\Z")


def active_round(document, received):
    rounds = set()
    for game in document.get("live", []):
        if game.get("sport") != "sc" or game.get("status") != "경기전":
            continue
        year, number = game.get("year"), game.get("round")
        if type(year) is not int or type(number) is not int or not 0 < number < 10000:
            continue
        kickoff = _game_kickoff(game)
        if kickoff is not None and kickoff.replace(tzinfo=KST) > received + timedelta(minutes=30):
            rounds.add(f"{year % 100:02d}{number:04d}")
    return max(rounds, default=None)


def run(state, collector, ledger=None):
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")
    with requests.Session() as session:
        document, _, received = fetch_public(session, "/api/matches?scope=recent")
    current = active_round(document, received)
    results = state / "reports"
    results.mkdir(exist_ok=True)
    pairs = state / "paired-v1"
    previous = []
    for path in sorted(pairs.glob("*"), reverse=True):
        if not path.is_dir() or not ROUND.fullmatch(path.name) or path.name == current:
            continue
        report = results / (path.name + ".json")
        if not report.exists():
            previous.append(path.name)
            continue
        saved = json.loads(report.read_text(encoding="utf-8"))
        if any(row.get("status") == "verified" and row.get("eligible_quotes", 0) > 0
               for row in saved.get("price_receipts", [])) and any(
                   saved.get(key, {}).get("pending_products", 0) for key in
                   ("paired_vs_existing_vote", "paired_vs_frozen_operating_pick")):
            previous.append(path.name)
    cursor = state / "settlement_cursor.json"
    index = int(json.loads(cursor.read_text(encoding="utf-8")).get("index", 0)) if cursor.exists() else 0
    rounds = ([current] if current else []) + ([previous[index % len(previous)]] if previous else [])

    def command(mode, number, *args):
        finished = subprocess.run(
            [str(python), "-m", "scripts.prospective_tipster_pairs", mode,
             "--round", number, "--evidence", str(state), *args], cwd=collector,
            capture_output=True, encoding="utf-8", timeout=100,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True,
        )
        return json.loads(finished.stdout)

    summaries = {}
    for number in rounds:
        captured = command("capture", number, "--article-requests", "6" if number == current else "0")
        extra = ("--ledger", str(ledger)) if ledger is not None and ledger.is_file() else ()
        evaluated = command("evaluate", number, *extra)
        atomic_json(results / (number + ".json"), evaluated)
        summaries[number] = {"capture": captured, "report": str(results / (number + ".json"))}
    if previous:
        atomic_json(cursor, {"index": index + 1})
    result = {"active_round": current, "received_at": received.isoformat(),
              "local_operating_ledger_available": bool(ledger and ledger.is_file()), "rounds": summaries}
    atomic_json(state / "latest.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--collector", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args()
    args.state.mkdir(parents=True, exist_ok=True)
    with args.log.open("a", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
        try:
            with single_writer(args.state / "scheduler.lock") as acquired:
                if not acquired:
                    return 0
                result = run(args.state, args.collector, args.ledger)
            print(json.dumps(result, ensure_ascii=False))
        except Exception:
            traceback.print_exc()
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
