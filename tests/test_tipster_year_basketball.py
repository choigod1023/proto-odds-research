"""Offline regression checks for public archive parsing, provenance and joins."""
import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location("year_basketball", Path(__file__).resolve().parents[1] / "scripts/research_collect_year_basketball.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
META = dict(url=m.MIKY + "/results/2026-03/", observed_at="2026-10-28T00:00:00+00:00", raw_path="sample.gz", sha256="abc")


def miky_row(day="2026-03-05", side="Home", market="H/A, OT", result="Lost 75:73"):
    return f'''<table><tbody><tr><td class="when"><a href="/tips/{day}/">date</a></td>
    <td class="game">🏀 Goyang Sono - Anyang JungKwanJang<a class="league" href="/league/south-korea-kbl/">KBL</a></td>
    <td class="market">{market}</td><td class="pick">{side}</td><td class="odds">2.15</td>
    <td class="book">bet365</td><td class="res"><span class="badge">{result.split()[0]}</span> {result}</td>
    <td class="profit">-1.00</td></tr></tbody></table>'''


class BasketballArchiveTests(unittest.TestCase):
    def test_loss_kept_date_and_publication_not_confused(self):
        rows, _ = m.parse_miky(miky_row(), META)
        self.assertEqual(rows[0]["source_reported_result"], "Lost")
        self.assertIsNone(rows[0]["published_at_claim"])
        self.assertFalse(rows[0]["cutoff_proven"])
        self.assertEqual(rows[0]["match_date"], "2026-03-05")

    def test_requested_bounds_exclude_next_day(self):
        rows, excluded = m.parse_miky(miky_row(day="2026-10-28"), META)
        self.assertEqual(rows, [])
        self.assertEqual(excluded["outside_date_window"], 1)
        self.assertEqual(len(m.parse_miky(miky_row(day=m.START), META)[0]), 1)
        self.assertEqual(len(m.parse_miky(miky_row(day=m.END), META)[0]), 1)

    def test_markets_do_not_promote_props_or_periods(self):
        for raw in ("Over (Christian Braun over 4.5 rebounds)", "(Victor Wembanyama Under 19.5 Points)"):
            self.assertEqual(m.normalize_market(raw, "A", "B")[0], "unparsed")
        self.assertEqual(m.normalize_market("(1st half -12.5 Denver Nuggets)", "A", "B")[0], "period_spread")
        self.assertEqual(m.normalize_market("Under (1 rungt. pusė - Total under 121.5)", "A", "B")[:3], ("period_total", "under", 121.5))
        self.assertEqual(m.normalize_market("Over (243.5)", "A", "B")[:3], ("total", "over", 243.5))
        self.assertEqual(m.normalize_market("O/U 165.25, OT", "A", "B")[2], 165.25)

    def test_official_score_requires_exact_teams_and_date(self):
        row = m.parse_miky(miky_row(), META)[0][0]
        game = dict(match_date="2026-03-05", home_id="66", away_id="70", official_id="game-1", is_ended=True,
                    home_score=75, away_score=73, source_url="https://api.kbl.or.kr/match/list", observed_at=META["observed_at"], evidence={})
        m.attach_official([row], [game])
        self.assertEqual(row["official_result"]["pick_result"], "Won")
        self.assertEqual(row["source_reported_result"], "Lost")  # never overwrite publisher disagreement
        wrong = copy.deepcopy(row)
        wrong["match_date"] = "2026-03-06"
        m.attach_official([wrong], [game])
        self.assertIsNone(wrong["official_result"])
        ambiguous = copy.deepcopy(row)
        m.attach_official([ambiguous], [game, game])
        self.assertEqual(ambiguous["official_validation_status"], "ambiguous_official_match")

    def test_pending_official_and_quarter_total_never_settled(self):
        row = m.parse_miky(miky_row(market="O/U 148.25, OT", side="Over"), META)[0][0]
        game = dict(match_date="2026-03-05", home_id="66", away_id="70", official_id="g", is_ended=True,
                    home_score=75, away_score=73, source_url="official", observed_at="now", evidence={})
        m.attach_official([row], [game])
        self.assertIsNone(row["official_result"]["pick_result"])
        game["is_ended"] = False
        m.attach_official([row], [game])
        self.assertIsNone(row["official_result"])

    def test_multiple_authors_and_explicit_aliases_one_fixture(self):
        a = dict(league="NBA", match_date="2026-03-05", teams={"home": "Toronto Raptors", "away": "Brooklyn Nets"})
        b = dict(a, teams={"home": "TOR Raptors", "away": "BKN Nets"}, author="other")
        self.assertEqual(m.fixture_key(a), m.fixture_key(b))

    def test_spreads_team_totals_and_alias_moneyline(self):
        home, away = "Minnesota Timberwolves", "Los Angeles Lakers"
        self.assertEqual(m.normalize_market("(-7 MIN Timberwolves)", home, away)[:3], ("spread", "home", -7.0))
        self.assertEqual(m.normalize_market("Los Angeles Lakers (+8.5)", home, away)[:3], ("spread", "away", 8.5))
        self.assertEqual(m.normalize_market("(LA Lakers)", home, away)[:3], ("moneyline", "away", None))
        self.assertEqual(m.normalize_market("Over (Minnesota Timberwolves 126.5)", home, away)[:3], ("team_total", "home_over", 126.5))
        self.assertEqual(m.normalize_market("(Under 116.5 Los Angeles Lakers)", home, away)[:3], ("team_total", "away_under", 116.5))
        self.assertEqual(m.normalize_market("Minnesota Timberwolves (Serijos winner)", home, away)[0], "series_winner")

    def test_snapshot_hashes_and_real_artifact_invariants(self):
        entries = m.ledger()
        self.assertEqual(len({e["url"] for e in entries}), len(entries))
        for entry in entries:
            if entry.get("raw_path"):
                self.assertEqual(len(entry["sha256"]), 64)
                # Complete publisher pages are a local cache, not redistributed.
                if (m.ROOT / entry["raw_path"]).exists():
                    self.assertEqual(hashlib.sha256(m.body(entry)).hexdigest(), entry["sha256"])
        rows = [json.loads(line) for line in (m.OUT / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertGreaterEqual(len(rows), 500)
        self.assertEqual(len(rows), len({r["candidate_id"] for r in rows}))
        self.assertGreaterEqual(len({r["match_date"][:7] for r in rows}), 6)
        self.assertGreaterEqual(len({r["author_id"] for r in rows}), 10)
        self.assertTrue(all(m.START <= r["match_date"] <= m.END for r in rows))
        self.assertTrue(all(not r["cutoff_proven"] for r in rows))
        self.assertTrue(all(r["official_result"] is None for r in rows if r["league"] == "NBA"))
        self.assertTrue(any(r["source_reported_result"] == "Lost" for r in rows))

    def test_captured_betblog_metadata_and_full_dataset(self):
        entries = {meta["url"]: meta for meta in m.ledger()
                   if meta.get("status") == 200 and "betblog.com/tipster/kentukyo/basketball/nba/" in meta["url"]}
        candidates = [json.loads(line) for line in (m.OUT / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
        rows = [row for row in candidates if row["source"] == "Betblog"
                and any(e["url"] in entries for e in row["evidence"])]
        # Persisted facts must always be checked, even on clean CI checkouts.
        self.assertTrue(entries)
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["author"].lower(), "kentukyo")
            self.assertEqual(row["league"], "NBA")
            self.assertTrue(row["published_at_claim"])
            self.assertEqual(row["source_reported_game_time_utc"][-6:], "+00:00")
            self.assertFalse(row["cutoff_proven"])
            self.assertTrue(row["source_url"].startswith(m.BETBLOG + "/picks/"))
            self.assertIsNone(row["source_reported_score"])
            for evidence in row["evidence"]:
                if evidence["url"] in entries:
                    meta = entries[evidence["url"]]
                    for field in ("sha256", "raw_path", "observed_at"):
                        self.assertEqual(evidence[field], meta[field])
        # Complete publisher bodies stay local; when present, verify them too.
        for meta in entries.values():
            if (m.ROOT / meta["raw_path"]).is_file():
                data = m.body(meta)
                self.assertEqual(hashlib.sha256(data).hexdigest(), meta["sha256"])
                parsed, _ = m.parse_betblog(data, meta)
                by_id = {row["candidate_id"]: row for row in parsed}
                captured = [row for row in rows if any(e["url"] == meta["url"] for e in row["evidence"])]
                for row in captured:
                    self.assertIn(row["candidate_id"], by_id)
                    for field in ("published_at_claim", "source_reported_game_time_utc", "source_url",
                                  "source_reported_score", "cutoff_proven", "market", "side", "reported_decimal_odds"):
                        self.assertEqual(by_id[row["candidate_id"]][field], row[field])

    def test_minimal_betblog_dom_parses_metadata_without_publisher_cache(self):
        # Synthetic DOM: only parser inputs, no copied publisher page or prose.
        data = b'''<ul><li itemtype="http://schema.org/SportsEvent">
          <meta itemprop="name" content="Boston Celtics - New York Knicks">
          <meta itemprop="startDate" content="2026-03-05GMT00:30:00+00:00">
          <meta itemprop="validFrom" content="2026-03-04GMT18:00:00+00:00">
          <div itemprop="location"><meta itemprop="url" content="https://www.betblog.com/basketball/nba/"></div>
          <div itemprop="performer"><meta itemprop="name" content="synthetic-author">
            <meta itemprop="url" content="https://www.betblog.com/tipster/synthetic-author"></div>
          <div itemprop="offers"><meta itemprop="url" content="https://example.test/bookmaker"></div>
          <div class="match"><a href="/picks/123456-synthetic-fixture">fixture</a></div>
          <div class="hover"><span>Pick @ 1.85 Pick: (Boston Celtics) Units: 1</span></div>
          <span class="units">-1.00</span>
        </li></ul>'''
        rows, excluded = m.parse_betblog(data, META)
        self.assertEqual(excluded, {})
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["candidate_id"], "betblog-123456")
        self.assertEqual(row["author"], "synthetic-author")
        self.assertEqual(row["league"], "NBA")
        self.assertEqual(row["teams"], {"home": "Boston Celtics", "away": "New York Knicks"})
        self.assertEqual(row["published_at_claim"], "2026-03-04T18:00:00+00:00")
        self.assertEqual(row["source_reported_game_time_utc"], "2026-03-05T00:30:00+00:00")
        self.assertTrue(row["published_before_game_claim"])
        self.assertFalse(row["cutoff_proven"])
        self.assertEqual((row["market"], row["side"], row["reported_decimal_odds"]), ("moneyline", "home", 1.85))
        self.assertEqual(row["source_url"], m.BETBLOG + "/picks/123456-synthetic-fixture")
        self.assertEqual(row["source_reported_result"], "Lost")
        self.assertIsNone(row["source_reported_score"])
        self.assertEqual(row["evidence"][0]["sha256"], META["sha256"])

    def test_metadata_checks_without_local_raw_cache(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(m, "ROOT", Path(root)), \
                mock.patch.object(m, "body", side_effect=AssertionError("Local raw cache must be optional")):
            self.test_snapshot_hashes_and_real_artifact_invariants()
            self.test_captured_betblog_metadata_and_full_dataset()


if __name__ == "__main__":
    unittest.main()
