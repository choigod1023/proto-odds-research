from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import archive


def fixture(ident, day, teams, completed=True):
    return {
        "id": ident,
        "season": ident[:3],
        "kickoff": datetime.fromisoformat(day + "T19:00:00+09:00"),
        "teams": teams,
        "result": {} if completed else None,
    }


class ArchiveTest(unittest.TestCase):
    def test_rest_uses_prior_completed_fixtures_in_same_season(self):
        games = [
            fixture("S47G01N3", "2025-10-07", ("06", "60")),
            fixture("S45G04N1", "2025-05-02", ("06", "60")),
            fixture("S47G01N2", "2025-10-06", ("06", "70"), completed=False),
            fixture("S47G01N1", "2025-10-04", ("06", "70")),
        ]
        rest = archive._rest(games)
        self.assertEqual(rest["S47G01N1"], [(None, None), (None, None)])
        self.assertEqual(rest["S47G01N3"], [(2, "S47G01N1"), (None, None)])

    def test_earliest_round_and_number_are_selected_before_grading(self):
        game = fixture("S45G01N195", "2025-02-28", ("06", "60"))
        game.update(phase="regular", score=[81, 80])
        raw = {"gameReject": "0", "protoStatus": "4", "gameResult": "0", "mchScore": "82.5:80"}
        conflict = dict(raw, mchScore="0.5:0")
        terms = (1.5, [1.7, 1.8])
        candidates = [
            (game, ("250027", 1), "G101:250027:1", raw, terms, {}),
            (game, ("250026", 3), "G101:250026:3", raw, terms, {}),
            (game, ("250026", 2), "G101:250026:2", conflict, terms, {}),
        ]
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            manifest = repo / "manifest.json"
            manifest.write_bytes(b"{}")
            with (
                patch.object(archive, "_read_kbl", return_value=({game["id"]: game}, {})),
                patch.object(archive, "_offer_candidates", return_value=iter(candidates)),
            ):
                rows, audit = archive.load(repo, manifest_path=manifest)
        self.assertEqual(rows, [])
        self.assertEqual(audit["duplicate_offers"], 2)
        self.assertEqual(audit["settlement_conflict"], 1)


if __name__ == "__main__":
    unittest.main()
