"""Compare match-view builds locally using an existing serving snapshot ZIP.

The collector is never contacted. Each variant runs in a fresh process against
the same temporary SQLite database. Prediction and settlement records are copied
verbatim from compressed match details.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from runtime_db import RuntimeDatabase
from measure_market_refresh_memory import memory


def from_snapshot(snapshot):
    with zipfile.ZipFile(snapshot) as archive:
        metadata = json.loads(archive.read("metadata"))
        if "all" not in metadata["days"]:
            raise ValueError("Snapshot needs the all scope to preserve section membership")
        cards = json.loads(gzip.decompress(archive.read("list/all")))
        performance = json.loads(gzip.decompress(archive.read("list/performance")))
        document = {key: value for key, value in cards.items()
                    if key not in ("live", "past", "result_games", "view")}
        document["prediction_performance"] = performance.get("prediction_performance")
        for section in ("live", "past"):
            document[section] = [json.loads(zlib.decompress(
                archive.read("detail/" + card["_detail_key"]))) for card in cards[section]]
    return document


def measure(database, baseline):
    if baseline:
        code = subprocess.check_output(["git", "show", baseline + ":src/match_api.py"], cwd=ROOT)
        module = types.ModuleType("baseline_match_api")
        exec(compile(code, "baseline_match_api.py", "exec"), module.__dict__)
    else:
        import match_api as module
    prepared = module.PreparedMatchResponses(module.MatchViews(RuntimeDatabase(database)))
    prepared.requested = {"recent", "all", "performance"}
    started, cpu = time.perf_counter(), time.process_time()
    prepared.refresh()
    elapsed, cpu_elapsed = time.perf_counter() - started, time.process_time() - cpu
    peak_bytes = memory()["peak_kib"] * 1024
    digest = hashlib.sha256()
    for scope in sorted(prepared.requested):
        value = json.loads(prepared.get_bytes(scope, compressed=False))
        digest.update(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())
    for key, value in sorted(prepared.views.games.items()):
        digest.update(key.encode())
        digest.update(json.dumps(json.loads(zlib.decompress(value)), sort_keys=True, ensure_ascii=False).encode())
    wire_bytes = {scope: len(prepared.get_bytes(scope)) for scope in sorted(prepared.requested)}
    return {"variant": baseline or "chunked_gzip", "wall_seconds": elapsed,
            "cpu_seconds": cpu_elapsed, "peak_rss_bytes": peak_bytes,
            "games": len(prepared.views.games), "wire_bytes": wire_bytes,
            "semantic_sha256": digest.hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--baseline", default="origin/main")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--variant")
    args = parser.parse_args()
    if args.database:
        print(json.dumps(measure(args.database, None if args.variant == "chunked_gzip" else args.variant)))
        return
    if args.snapshot is None:
        parser.error("--snapshot is required")
    with tempfile.TemporaryDirectory(prefix="match-view-benchmark-") as directory:
        document = from_snapshot(args.snapshot)
        database = RuntimeDatabase(Path(directory) / "local.sqlite3")
        database.store_artifact("picks_v2", document)
        del document
        rows = []
        for variant in (args.baseline, "chunked_gzip"):
            result = subprocess.check_output([
                sys.executable, __file__, "--database", str(database.path), "--variant", variant
            ], text=True)
            row = json.loads(result)
            rows.append(row)
            print(json.dumps(row), flush=True)
        if rows[0]["semantic_sha256"] != rows[1]["semantic_sha256"]:
            raise RuntimeError("Published lists or fixture details differ")


if __name__ == "__main__":
    main()
