"""Offline regression/integrity checks; writes a machine-readable test receipt."""
import copy
import gzip
import hashlib
import html
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location("collector", ROOT / "scripts/research_collect_kbl_tipsters.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def jsonl(name):
    return [json.loads(line) for line in (HERE / name).read_text(encoding="utf-8").splitlines()]


class CaptureVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kbl = jsonl("kbl-candidates.jsonl")
        cls.nba = jsonl("nba-candidates.jsonl")
        cls.observations = jsonl("source-observations.jsonl")
        cls.official = jsonl("official-kbl-results.jsonl")
        cls.summary = json.loads((HERE / "summary.json").read_text(encoding="utf-8"))
        cls.ledger = json.loads((HERE / "requests.json").read_text(encoding="utf-8"))

    def test_every_matching_source_row_retained(self):
        self.assertEqual(len(self.observations), 142)
        self.assertEqual(sum(p["raw_rows"] for p in self.summary["page_audits"]), 476)
        self.assertEqual(sum(p["matching_rows"] for p in self.summary["page_audits"]), 142)
        for page in self.summary["page_audits"]:
            self.assertEqual(page["raw_rows"], page["matching_rows"] + sum(page["exclusions"].values()))
            self.assertFalse(any(label in {"other_league:USA · NBA", "other_league:South Korea · KBL"}
                                 for label in page["exclusions"]))

    def test_every_kbl_row_has_both_league_and_day_evidence(self):
        self.assertEqual(len(self.kbl), 19)
        for row in self.kbl:
            urls = [e["source_url"] for e in row["evidence"]]
            self.assertEqual(len(urls), 2)
            self.assertTrue(any("/league/" in u for u in urls))
            self.assertIn(row["source_url"], urls)

    def test_losses_and_pending_preserved(self):
        self.assertEqual(c.Counter(r["source_reported_result"] for r in self.kbl),
                         {"Won": 12, "Lost": 6, "Pending": 1})
        self.assertEqual(len({r["candidate_id"] for r in self.kbl + self.nba}), 120)

    def test_game_time_does_not_prove_publication(self):
        for row in self.kbl + self.nba:
            self.assertIsNone(row["source_reported_published_at"])
            self.assertIs(row["cutoff_proven"], False)
            self.assertEqual(row["timestamp_role"], "game_time_not_publication_time")
            self.assertIn(row["match_date"], row["source_url"])
            self.assertIsNotNone(row["source_reported_game_timestamp"])

    def test_official_full_response_is_retained(self):
        self.assertEqual(len(self.official), 138)
        self.assertEqual(len({r["official_id"] for r in self.official}), 138)
        self.assertTrue(all("2026-03-05" <= r["match_date"] <= "2026-10-28" for r in self.official))

    def test_exact_joins_keep_score_disagreement(self):
        settled = [r for r in self.kbl if r["official_result"] is not None]
        self.assertEqual(len(settled), 18)
        self.assertEqual(sum(r["publisher_official_outcome_matches"] for r in settled), 18)
        self.assertEqual(sum(r["publisher_official_score_matches"] for r in settled), 17)
        mismatch = next(r for r in settled if not r["publisher_official_score_matches"])
        self.assertEqual(mismatch["match_date"], "2026-04-26")
        self.assertEqual(mismatch["source_reported_score"], {"home": 84, "away": 67})
        self.assertEqual(mismatch["official_result"]["home_score"], 91)
        self.assertEqual(mismatch["official_result"]["away_score"], 83)

    def test_joins_do_not_use_scores_or_results(self):
        row = copy.deepcopy(self.kbl[0])
        row["source_reported_score"] = {"home": 999, "away": 0}
        row["source_reported_result"] = "Lost"
        c.attach_official([row], self.official)
        self.assertEqual(row["official_result_provenance"]["official_id"], self.kbl[0]["official_result_provenance"]["official_id"])
        self.assertFalse(row["publisher_official_score_matches"])

    def test_unknown_alias_does_not_fuzzy_join(self):
        row = copy.deepcopy(self.kbl[0])
        row["teams"]["home"] += "?"
        row["official_result"] = None
        row["official_result_provenance"] = None
        c.attach_official([row], self.official)
        self.assertEqual(row["official_validation_status"], "unknown_team_alias")
        self.assertIsNone(row["official_result"])

    def test_unended_match_stays_null(self):
        row = next(r for r in self.kbl if r["source_reported_result"] == "Pending")
        self.assertIsNone(row["source_reported_profit"])
        self.assertIsNone(row["official_result"])
        self.assertEqual(row["official_validation_status"], "official_not_ended")

    def test_other_markets_are_not_moneyline_settled(self):
        row = copy.deepcopy(self.kbl[0])
        row["market"] = "AH -5.5"
        row["official_result"] = None
        c.attach_official([row], self.official)
        self.assertEqual(row["official_validation_status"], "unsupported_market_or_side")
        self.assertIsNone(row["official_result"])

    def test_conflicting_observations_are_not_silently_merged(self):
        first = copy.deepcopy(self.observations[0])
        second = copy.deepcopy(first)
        second["source_reported_result"] = "Lost"
        rows, conflicts = c.deduplicate([first, second])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(rows[0]["source_observation_conflict"])

    def test_missing_odds_fails_instead_of_skipping_row(self):
        meta = next(m for m in self.ledger if m["url"] == c.BASE + c.LEAGUES["KBL"])
        # Reconstruct a minimal HTML fixture from facts; no copyrighted body needed.
        row = self.kbl[0]
        fixture = html.escape(row["raw_fixture_text"])
        body = ('<table><tbody><tr><td class="when"><a href="/tips/' + row["match_date"] + '/">date</a></td>'
                '<td class="game">' + fixture + '<a class="league" href="/league/south-korea-kbl/">South Korea · KBL</a></td>'
                '<td class="market">H/A, OT</td><td class="pick">Home</td>'
                '<td class="missing">2.15</td></tr></tbody></table>')
        with self.assertRaisesRegex(ValueError, "Missing column"):
            c.parse_page(body, meta)

    def test_raw_file_hashes_and_request_cap(self):
        self.assertEqual(len(self.ledger) + 2, 27)
        self.assertLessEqual(len(self.ledger) + 2, 30)
        for meta in self.ledger:
            if "raw_path" not in meta:
                self.assertIn("error", meta)
                continue
            if not (ROOT / meta["raw_path"]).exists():
                self.assertIs(meta.get("git_retained"), False)
                continue
            body = (ROOT / meta["raw_path"]).read_bytes()
            if meta.get("raw_encoding", "").startswith("gzip"):
                body = gzip.decompress(body)
            self.assertEqual(hashlib.sha256(body).hexdigest(), meta["sha256"])
        self.assertTrue(all(m["automatic_retries"] == 0 for m in self.ledger))

    def test_publisher_arithmetic_label_and_prices(self):
        stats = c.publisher_stats(self.kbl)
        self.assertEqual(stats["source_reported_profit_sum_units"], 10.29)
        self.assertEqual(stats["internal_profit_arithmetic_errors"], [])
        self.assertIn("unverified", stats["label"])
        for row in self.kbl + self.nba:
            self.assertEqual(float(row["reported_decimal_odds_raw"]), row["reported_decimal_odds"])
            if row["market"] == "H/A, OT":
                self.assertIsNone(row["line"])
            else:
                self.assertTrue(row["market"].startswith("O/U "))
                self.assertTrue(195.5 <= row["line"] <= 222.5)
                self.assertIn(row["side"], {"over", "under"})
            self.assertEqual(row["price_evidence_tier"], "retrospective_listed_price_claim")

    def test_nba_is_separate_and_officially_unverified(self):
        self.assertEqual(len(self.nba), 101)
        self.assertTrue(all(r["official_result"] is None for r in self.nba))
        self.assertEqual(sum(r["line"] is not None for r in self.nba), 92)
        self.assertEqual(sum(r["market"] == "H/A, OT" for r in self.nba), 9)
        self.assertEqual(sum(any("nba-league.html" in e["raw_path"] for e in r["evidence"])
                             for r in self.nba), 100)

    def test_offline_factual_replay_reproduces_all_candidates(self):
        names = ["source-observations.jsonl", "kbl-candidates.jsonl", "nba-candidates.jsonl", "official-kbl-results.jsonl"]
        before = {name: (HERE / name).read_bytes() for name in names}
        exists = Path.exists
        with patch.object(Path, "exists", lambda path: False if path.suffix == ".html" else exists(path)):
            with redirect_stdout(io.StringIO()):
                c.build()
        self.assertEqual(before, {name: (HERE / name).read_bytes() for name in names})


if __name__ == "__main__":
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CaptureVerification))
    receipt = {"executed_at": c.now(), "tests_run": result.testsRun, "failures": len(result.failures),
               "errors": len(result.errors), "success": result.wasSuccessful(), "offline_only": True,
               "log": log.getvalue()}
    c.dump(HERE / "parser-verification.json", receipt)
    print(log.getvalue())
    raise SystemExit(0 if result.wasSuccessful() else 1)
