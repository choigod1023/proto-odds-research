import copy
from contextlib import closing
from datetime import date
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

SPEC = importlib.util.spec_from_file_location("gate", Path(__file__).resolve().parents[1] / "src/soccer_process_gate.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def fixture(day, shots=10):
    return {"date": day, "home": "A", "away": "B", "data": {
        "home": {"shots": shots, "sog": 4}, "away": {"shots": 8, "sog": 3}}}


class ProcessGateTests(unittest.TestCase):
    def test_missing_target_is_not_zero(self):
        item = fixture("2024-01-01")
        del item["data"]["home"]["shots"]
        self.assertEqual(gate.load_games({"1": item}), ([], 1))

    def test_invalid_count_rejected(self):
        for count in (True, float("nan"), float("inf"), -1, 2.5):
            self.assertEqual(gate.load_games({"1": fixture("2024-01-01", count)})[1], 1)

    def test_duplicate_fixture_fails_closed(self):
        with self.assertRaises(ValueError):
            gate.load_games({"1": fixture("2024-01-01"), "2": fixture("2024-01-01")})

    def test_two_day_lag_and_current_outcome_invariance(self):
        raw = {str(i): fixture(f"2024-01-{i:02d}") for i in range(1, 10)}
        games, _ = gate.load_games(raw)
        before = gate.examples(games)
        changed = copy.deepcopy(games)
        changed[-1]["counts"] = [[100, 99], [100, 99]]
        after = gate.examples(changed)
        np.testing.assert_array_equal(before[-1]["x"], after[-1]["x"])
        self.assertEqual(before[0]["day"], date(2024, 1, 7))
        changed[-2]["counts"] = [[200, 199], [200, 199]]
        np.testing.assert_array_equal(before[-1]["x"], gate.examples(changed)[-1]["x"])

    def test_empty_sample_is_not_success(self):
        result = gate.evaluate([])
        self.assertEqual(result["status"], "insufficient_sample")
        self.assertFalse(result["production_allowed"])

    def test_day_bootstrap_constant_gain(self):
        np.testing.assert_allclose(gate.interval_by_day([2, 2, 2], ["a", "a", "b"]), [2, 2])

    def test_playoff_removed_before_history(self):
        raw = {next(iter(gate.PLAYOFF_IDS)): fixture("2024-01-01")}
        self.assertEqual(gate.load_games(raw), ([], 0))

    def test_config_controls_history(self):
        raw = {str(i): fixture(f"2024-01-{i:02d}") for i in range(1, 10)}
        with patch.dict(gate.CONFIG, {"history_lag_days": 4, "minimum_history": 2, "window": 2}):
            rows = gate.examples(gate.load_games(raw)[0])
        self.assertEqual(rows[0]["day"], date(2024, 1, 6))

    def test_real_snapshot_pipeline_and_duplicate_guard(self):
        source = Path(__file__).resolve().parents[1] / "data/raw/detail/kleague_shots_2023_2026.json"
        with tempfile.TemporaryDirectory() as directory, patch.dict(gate.CONFIG, {"bootstrap": 100}):
            root = Path(directory)
            db = root / "research.sqlite3"
            result = gate.run(source, db, root / "report.md")
            self.assertEqual(result["source_games"], 804)
            self.assertEqual(result["excluded_playoffs"], 12)
            self.assertFalse(result["production_allowed"])
            with closing(sqlite3.connect(db)) as connection:
                status, saved = connection.execute("SELECT status, result FROM runs").fetchone()
                self.assertEqual(status, "complete")
                self.assertEqual(len(json.loads(saved)["predictions"]), 2 * result["test_games"])
            with self.assertRaises(sqlite3.IntegrityError):
                gate.run(source, db, root / "duplicate.md")
            self.assertFalse((root / "duplicate.md").exists())


if __name__ == "__main__":
    unittest.main()
