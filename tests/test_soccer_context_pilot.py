"""Offline temporal and identity regression tests; fixtures are synthetic."""
import csv
from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from soccer_context_pilot import build_rows, fixture, fixed_probe


class SoccerContextPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.game = dict(year="2026", round="90", game_no="1", sport="sc", league="K리그1",
                         market_family="승무패", n_way="3", home="FC서울 9", away="0 전북현대",
                         date_text="08.01(토) 19:30", result="무승부", is_void="False", odds="2,3,4")
        self.xg = dict(league="kleague1", home_team="FC Seoul", away_team="Jeonbuk Motors",
                       snapshot_at="2026-07-31T10:00:00Z",
                       home=dict(xg_home=1.5, xga_home=1.0), away=dict(xg_away=1.1, xga_away=1.2))
        self.odds = dict(self.game, home="FC서울", away="전북현대", result="경기전",
                         ts="2026-08-01T10:00:00Z")

    def run_adapter(self, games=None, snapshots=None, odds=None):
        gp, xp, op = [self.root / n for n in ("games.csv", "xg.jsonl", "odds.csv")]
        for path, values in ((gp, games if games is not None else [self.game]),
                             (op, odds if odds is not None else [self.odds])):
            with path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(values[0] if values else self.odds))
                writer.writeheader()
                writer.writerows(values)
        xp.write_text("\n".join(json.dumps(x) if isinstance(x, dict) else x
                                for x in (snapshots if snapshots is not None else [self.xg])),
                      encoding="utf-8")
        before = [p.read_bytes() for p in (gp, xp, op)]
        result = build_rows(gp, xp, [op], since=date(2026, 8, 1), until=date(2026, 8, 2))
        self.assertEqual(before, [p.read_bytes() for p in (gp, xp, op)])
        return result

    def test_boundary_draw_and_schema_without_score_inference(self):
        rows, audit = self.run_adapter()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["target"], 1)  # Display says 9-0; explicit label owns target.
        self.assertEqual(set(rows[0]), {"event_id", "league", "kickoff", "feature_as_of",
                                        "features", "target", "odds", "odds_as_of",
                                        'feature_availability_verified', 'feature_provenance'})
        self.assertFalse(rows[0]['feature_availability_verified'])
        self.assertEqual(rows[0]["feature_as_of"], "2026-08-01T10:00:00+00:00")
        self.assertTrue(audit["insufficient_sample"])
        self.assertFalse(audit["model_fitted"])

    def test_snapshot_one_second_too_recent(self):
        rows, audit = self.run_adapter(snapshots=[dict(self.xg, snapshot_at="2026-07-31T10:00:01Z")])
        self.assertFalse(rows)
        self.assertEqual(audit["counts"]["missing_xg_with_24h_lag_by_t30"], 1)

    def test_odds_one_second_after_t30_no_archive_fallback(self):
        rows, audit = self.run_adapter(odds=[dict(self.odds, ts="2026-08-01T10:00:01Z")])
        self.assertFalse(rows)
        self.assertEqual(audit["counts"]["missing_timestamped_pregame_odds_by_t30"], 1)

    def test_unknown_mapping_missing_metric_and_bad_json(self):
        rows, audit = self.run_adapter(snapshots=["broken", dict(self.xg, home_team="Unknown"),
                                                  dict(self.xg, home={"xg_home": None})])
        self.assertFalse(rows)
        self.assertEqual(audit["malformed_xg_lines"], [1])
        self.assertEqual(audit["unknown_maps"], {"Unknown": 1})
        self.assertEqual(audit["counts"]["invalid_snapshot_fields"], 1)

    def test_reissues_deduplicate_and_conflicting_targets_exclude(self):
        rows, _ = self.run_adapter(games=[self.game, dict(self.game, round="91")])
        self.assertEqual(len(rows), 1)
        rows, audit = self.run_adapter(games=[self.game, dict(self.game, result="홈승")])
        self.assertFalse(rows)
        self.assertEqual(audit["counts"]["conflicting_result_fixtures"], 1)

    def test_latest_conflicting_odds_rejected(self):
        rows, audit = self.run_adapter(odds=[self.odds, dict(self.odds, odds="3,3,3")])
        self.assertFalse(rows)
        self.assertEqual(audit["counts"]["conflicting_latest_odds"], 1)

    def test_stale_odds_rejected(self):
        rows, audit = self.run_adapter(odds=[dict(self.odds, ts='2026-08-01T09:24:59Z')])
        self.assertFalse(rows)
        self.assertEqual(audit['counts']['stale_odds_before_t30'],1)

    def test_latest_cancellation_does_not_revive_old_price(self):
        rows, audit = self.run_adapter(odds=[dict(self.odds,ts='2026-08-01T09:59:00Z'),dict(self.odds,result='취소')])
        self.assertFalse(rows)
        self.assertEqual(audit['counts']['latest_market_closed'],1)

    def test_bad_identity_and_truncated_source_do_not_crash(self):
        rows, audit = self.run_adapter(snapshots=[dict(self.xg,home_team=None),dict(self.xg,home_team='Unknown')])
        self.assertFalse(rows)
        self.assertEqual(audit['counts']['invalid_snapshot_fields'],1)
        rows, audit = self.run_adapter(games=[dict(self.game,is_void=None)])
        self.assertFalse(rows)

    def test_numerical_overflow_is_a_status_not_nan(self):
        result=fixed_probe([dict(features=[1e308]*4,target=0,odds=[2,3,4])])
        self.assertEqual(result['status'],'poisson_numerical_range_exceeded')
        json.dumps(result,allow_nan=False)

    def test_no_opponent_reversal_or_same_year_pair_join(self):
        rows, _ = self.run_adapter(snapshots=[dict(self.xg, home_team="Jeonbuk Motors", away_team="FC Seoul")])
        self.assertFalse(rows)
        rows, _ = self.run_adapter(snapshots=[dict(self.xg, snapshot_at="2026-07-01T10:00:00Z")])
        self.assertFalse(rows)

    def test_naive_timestamp_and_final_quote_rejected(self):
        rows, _ = self.run_adapter(odds=[dict(self.odds, ts="2026-08-01T10:00:00")])
        self.assertFalse(rows)
        rows, _ = self.run_adapter(odds=[dict(self.odds, result="홈승")])
        self.assertFalse(rows)

    def test_missing_real_files_are_audited(self):
        rows, audit = build_rows(self.root / "missing.csv", self.root / "missing.jsonl", [],
                                 since=date(2026, 8, 1), until=date(2026, 8, 2))
        self.assertFalse(rows)
        self.assertEqual(audit["status"], "missing_real_data")
        self.assertEqual(len(audit["missing_real_data"]), 3)

    def test_new_year_release_date(self):
        key = fixture(dict(self.game, year="2026", round="1", date_text="12.31(수) 23:30"))
        self.assertEqual(key[0].isoformat(), "2025-12-31T14:30:00+00:00")


if __name__ == "__main__":
    unittest.main()
