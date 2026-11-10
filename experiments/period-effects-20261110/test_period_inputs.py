"""Regression checks against the pinned and working-tree input ledgers."""

from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import period_inputs as adapter


REPO = Path(__file__).resolve().parents[2]
KEYS = {"id", "day", "sport", "league", "market", "period", "line", "odds", "y", "base", "source"}


def read(blob):
    return [json.loads(line) for line in gzip.decompress(blob).splitlines()]


class PeriodInputTests(unittest.TestCase):
    def test_archive_projection_and_exact_history_match(self):
        actual = list(adapter.rows(REPO))
        self.assertEqual(len(actual), len({row["id"] for row in actual}))
        self.assertTrue(all(row.keys() == KEYS for row in actual))
        keys = [(row["day"], row["id"]) for row in actual]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(Counter(row["day"][:4] for row in actual),
                         {"2023": 11443, "2024": 12853, "2025": 13428, "2026": 19658})

        spec = f"{adapter.ROUTING_COMMIT}:{adapter.ROUTING_PATH.as_posix()}"
        pinned = read(subprocess.check_output(["git", "show", spec], cwd=REPO))
        evaluation = [row for row in actual if row["day"] >= "2025-01-01"]
        expected = [
            {**{key: raw[key] for key in KEYS - {"base", "source"}},
             "base": raw["policies"]["reference"]["prob"], "source": raw["provenance"]}
            for raw in pinned
        ]
        self.assertEqual(len(evaluation), 33086)
        self.assertEqual(evaluation, sorted(expected, key=lambda row: (row["day"], row["id"])))
        self.assertEqual(sum(row["reference"] != "market" for row in pinned), 95)

        groups = {tuple(raw["group"]) for raw in pinned}
        expected_history = {}
        for sport, market, size in (("sc", "1x2", 3), ("bs", "win_loss", 2),
                                    ("bk", "win_loss", 2), ("vl", "win_loss", 2)):
            path = adapter.PRIMARY_ROOT / sport / "domestic_archive/ledger.jsonl.gz"
            for raw in read((REPO / path).read_bytes()):
                group = (sport, raw["league"], market, "full_game", size)
                if ("2023-01-01" <= raw["day"] < "2025-01-01" and group in groups
                        and raw["league"] != "KBL"):
                    expected_history[raw["id"]] = {
                        **{key: raw[key] for key in ("id", "day", "sport", "league", "odds", "y")},
                        "market": market, "period": "full_game", "line": None,
                        "base": raw["market_p"], "source": f"{path.as_posix()}:market_p",
                    }
        history = {row["id"]: row for row in actual if row["day"] < "2025-01-01"}
        self.assertEqual(history, expected_history)

    def test_manifest_and_hash_rejection(self):
        sources = adapter.manifest(REPO)["sources"]
        self.assertEqual(len(sources), 5)
        for path, source in sources.items():
            if "git_commit" in source:
                blob = subprocess.check_output(
                    ["git", "show", f"{source['git_commit']}:{path}"], cwd=REPO
                )
                self.assertEqual(hashlib.sha256(gzip.decompress(blob)).hexdigest(),
                                 source["sha256_uncompressed"])
            else:
                blob = (REPO / path).read_bytes()
            self.assertEqual(hashlib.sha256(blob).hexdigest(), source["sha256"])

        with tempfile.TemporaryDirectory() as directory:
            fresh = Path(directory)
            path = fresh / adapter._primary_path("sc")
            path.parent.mkdir(parents=True)
            path.write_bytes(b"wrong archive")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                adapter._primary_blob(fresh, "sc")

    def test_rejects_bad_pick_and_settlement(self):
        row = {"id": "offer", "odds": [2.0, 2.0], "y": 0}
        with self.assertRaisesRegex(ValueError, "Invalid archive pick"):
            adapter._check_pick(row, [0.5, 0.5], {"pick": 1, "hit": 0, "net": -1})
        with self.assertRaisesRegex(ValueError, "settlement disagree"):
            adapter._check_pick(row, [0.5, 0.5], {"pick": 0, "hit": 0, "net": -1})

    def test_rejects_duplicate_before_yielding(self):
        row = {"id": "offer", "day": "2025-01-01", "sport": "bk", "league": "NBA",
               "market": "win_loss", "period": "full_game", "odds": [2.0, 2.0]}
        with patch.object(adapter, "_routing_rows", return_value=iter([row, row])), \
                patch.object(adapter, "_primary_rows", return_value=iter(())):
            with self.assertRaisesRegex(ValueError, "Duplicate offer identity"):
                next(adapter.rows(REPO))


if __name__ == "__main__":
    unittest.main()
