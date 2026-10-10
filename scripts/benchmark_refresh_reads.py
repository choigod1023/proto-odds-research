"""Local synthetic before/after comparison; never connect to production.

Run: python scripts/benchmark_refresh_reads.py
Reports Python traced allocation (not total RSS) and elapsed wall time.
"""
import gc
import json
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from runtime_db import RuntimeDatabase


def measure(function):
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    result = function()
    elapsed = time.perf_counter() - started
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return result, dict(seconds=round(elapsed, 4), python_peak_mib=round(peak / 1048576, 3))


def main():
    with tempfile.TemporaryDirectory(prefix="refresh-read-bench-") as temp:
        db = RuntimeDatabase(Path(temp) / "synthetic.db")
        games = [dict(league="NBA", home_team=f"H{i}", away_team="A",
                      game_datetime="2026-10-10T09:00:00Z", payload="x" * 10000)
                 for i in range(200)]
        db.put_document("player_info", {"games": games})
        requests = [("NBA", f"H{i}", "A", "2026-10-10T09:00:00Z") for i in range(40)]
        old, old_metric = measure(lambda: [g for args in requests
            for g in db.player_fixture_document(*args)["games"]])
        new, new_metric = measure(lambda: db.player_fixtures_document(requests)["games"])
        assert sorted(old, key=lambda r: r["home_team"]) == sorted(new, key=lambda r: r["home_team"])
        print(json.dumps(dict(player_fixture_count=200, requested=40, scans_before=40,
                              scans_after=1, before=old_metric, after=new_metric)))
        with db.transaction() as c:
            c.executemany("INSERT INTO prediction_records VALUES (?,?,?,?,?,?,?,?)",
                ((i, "prediction", str(i), str(i), str(i), "2026-10-10", str(i),
                  json.dumps(dict(sequence=i, payload="x" * 7000))) for i in range(1, 5001)))
        def old_read():
            with db.connect() as c:
                rows = c.execute("SELECT record_json FROM prediction_records ORDER BY ledger_sequence").fetchall()
            return [json.loads(row[0]) for row in rows]
        old, old_metric = measure(old_read)
        new, new_metric = measure(db.prediction_records)
        assert old == new
        print(json.dumps(dict(ledger_rows=5000, before=old_metric, after=new_metric)))


if __name__ == "__main__":
    main()
