import json
import math
import subprocess
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import side_archive as archive


REPO = Path(__file__).resolve().parents[2]


class SideArchiveTest(unittest.TestCase):
    def test_pinned_products_and_probability_contract(self):
        data = list(archive.rows(REPO))
        self.assertEqual(
            Counter((r["sport"], r["market"], len(r["odds"])) for r in data),
            {
                ("bk", "handicap", 2): 207,
                ("bk", "total", 2): 199,
                ("bs", "handicap", 2): 2592,
                ("bs", "total", 2): 2514,
                ("sc", "handicap", 3): 2928,
                ("sc", "total", 2): 2895,
            },
        )
        self.assertEqual(len({r["id"] for r in data}), len(data))
        self.assertEqual([r["day"] for r in data], sorted(r["day"] for r in data))
        self.assertEqual((data[0]["day"], data[-1]["day"]), ("2026-04-21", "2026-11-02"))
        self.assertEqual({r["provenance"] for r in data}, {archive.PROVENANCE})
        self.assertEqual(
            {r["sport"] for r in data if archive.CANDIDATE in r["prob"]}, {"sc", "bs", "bk"}
        )
        self.assertNotIn("KBL", {r["league"] for r in data})

        original = {}
        for raw in archive._read_archive(REPO).splitlines():
            record = json.loads(raw)
            original[f"betman:{record['round']}:{record['matchSeq']}"] = record
        for row in data:
            source = original[row["id"]]
            self.assertEqual(row["line"], source["line"])
            self.assertEqual(row["period"], source["period"])
            self.assertEqual(row["odds"], [price for price in source["prices"] if price > 1])
            official_codes = [0, 1, 2] if len(row["odds"]) == 3 else [0, 2]
            self.assertEqual(official_codes[row["y"]], source["result"])
            for vector in row["prob"].values():
                self.assertEqual(len(vector), len(row["odds"]))
                self.assertTrue(all(0 < p < 1 for p in vector))
                self.assertTrue(math.isclose(sum(vector), 1.0))
            for price, probability in zip(row["odds"], row["prob"]["market"]):
                self.assertAlmostEqual(
                    probability, (1 / price) / sum(1 / odd for odd in row["odds"])
                )

    def test_target_score_and_embargoed_results_do_not_change_candidate(self):
        def record(day, sequence, score, line, league="SC001"):
            margin = score[0] - score[1] + line
            return {
                "sport": "SC",
                "league": league,
                "leagueCode": league,
                "market": "handicap",
                "period": "regulation",
                "kickoff": f"{day}T18:00:00+09:00",
                "round": 260001,
                "matchSeq": sequence,
                "score": score,
                "line": line,
                "prices": [3.0, 3.0, 3.0],
                "result": 0 if margin > 0 else 2 if margin < 0 else 1,
            }

        past = [record("2026-04-01", seq, [2, 1], 0) for seq in range(20)]
        target_id = "betman:260001:22"

        def target_for(history, recent_score):
            other_cohort = record("2026-04-01", 24, [0, 1], -1, league="SC002")
            entries = history + [
                record("2026-04-02", 20, recent_score, -1),  # Exactly seven days old.
                record("2026-04-09", 21, recent_score, -1),
                record("2026-04-09", 22, recent_score, -1),
                record("2026-04-10", 23, recent_score, -1),
                other_cohort,
            ]
            return next(r for r in archive._replay(reversed(entries)) if r["id"] == target_id)

        target = target_for(past, [2, 0])
        changed = target_for(past, [0, 2])
        self.assertNotEqual(target["y"], changed["y"])
        self.assertEqual(target["prob"], changed["prob"])
        # The past home wins become draws at the target's -1 handicap.
        prior = 50 / 3
        for actual, expected in zip(
            target["prob"][archive.CANDIDATE], [prior / 70, (20 + prior) / 70, prior / 70]
        ):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(set(target_for(past[:-1], [2, 0])["prob"]), {"market"})

    def test_reference_fallback_requires_the_pinned_hash(self):
        missing = subprocess.CompletedProcess([], 128, stdout=b"", stderr=b"missing")
        wrong = subprocess.CompletedProcess([], 0, stdout=b"unrelated snapshot", stderr=b"")
        with patch.object(archive.subprocess, "run", side_effect=[missing, wrong]) as git:
            with self.assertRaisesRegex(ValueError, "Unexpected Betman archive contents"):
                archive._read_archive(REPO)
        self.assertEqual(
            [call.args[0][-1] for call in git.call_args_list],
            [
                f"{archive.REVISION}:{archive.ARCHIVE_PATH}",
                f"{archive.FALLBACK_REF}:{archive.ARCHIVE_PATH}",
            ],
        )


if __name__ == "__main__":
    unittest.main()
