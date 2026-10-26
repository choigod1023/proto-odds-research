"""Synthetic fixtures: no third-party article text or network requests in tests."""
import copy
import json
import unittest
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.collect_pickdawgz_corpus import (
    Excluded, join_article, parse_article, parse_pick, copy_check, shingles,
    parse_windows, in_windows, url_game_date, run, BASE, recover_existing, load_odds, sha,
)

URL = "https://pickdawgz.com/mlb-picks/synthetic-2025/"
TEAMS = ["Oakland Athletics", "Cleveland Guardians"]


def fixture(pick="Test Writer's Pick: Athletics +120", modified="2025-07-18T10:33:19-04:00"):
    graph = {"@graph": [{"@type": "WebPage", "url": URL,
                        "datePublished": "2025-07-18T05:29:23-04:00", "dateModified": modified}]}
    return f'''<html><script type="application/ld+json">{json.dumps(graph)}</script>
    <article><header><h1>Original native title</h1>
    <p class="wp-block-post-author__name">Test Writer</p>
    <time datetime="2025-07-18T05:29:23-04:00">July 18, 2025 at 05:29 AM EDT</time></header>
    <div class="entry-content"><table class="free-pick-table">
    <thead><tr><th>MATCHUP</th><th>Oakland Athletics</th><th>Cleveland Guardians</th></tr></thead>
    <tbody><tr><th>When</th><td>Friday, July 18, 2025</td></tr>
    <tr><th>Time</th><td>07:10 PM EDT</td></tr></tbody></table>
    <h2>Why Cleveland Guardians will win</h2><p>Not a recommendation.</p>
    <h3>{pick}</h3></div></article></html>'''.encode()


def official():
    return {"league": "MLB", "date": "2025-07-19", "kickoff": "2025-07-19T08:10:00+09:00",
            "home": "클리가디", "away": "애슬레틱", "raw_team_names": {
                "home": "Cleveland Guardians", "away": "Athletics"},
            "official_id": "mlb:123", "source_url": "https://statsapi.mlb.com/test",
            "game_number": 1, "home_score": 2, "away_score": 3}


def odds():
    return {("2025-07-19", "클리가디", "애슬레틱"): [{
        "kickoff": "2025-07-19T08:10:00+09:00", "odds": [1.6, 2.2]}]}


class PickDawgzTest(unittest.TestCase):
    def test_moneyline_and_bare_team(self):
        self.assertEqual(parse_pick("A's Pick: Athletics +120", TEAMS), ("A", "Oakland Athletics", 120))
        self.assertEqual(parse_pick("A's Pick: Cleveland Guardians", TEAMS), ("A", "Cleveland Guardians", None))

    def test_reject_other_markets(self):
        for pick in ["Athletics +1.5", "Athletics -1", "Over 8.5", "Under 8", "Athletics F5 +120", "Athletics ML or Over", "Athletics and Guardians"]:
            with self.subTest(pick=pick), self.assertRaises(Excluded):
                parse_pick("A's Pick: " + pick, TEAMS)

    def test_ambiguous_city(self):
        with self.assertRaisesRegex(Excluded, "ambiguous"):
            parse_pick("A's Pick: New York +120", ["New York Mets", "New York Yankees"])

    def test_pick_heading_only_not_win_trend(self):
        row = parse_article(fixture(), URL)
        self.assertEqual(row["selected_native_team"], "Oakland Athletics")
        self.assertEqual(row["native_pick"], "Test Writer's Pick: Athletics +120")
        self.assertEqual(row["native_title"], "Original native title")
        self.assertEqual(row["displayed_published"], "2025-07-18 18:29")

    def test_postgame_modified_excluded(self):
        with self.assertRaisesRegex(Excluded, "after_source"):
            parse_article(fixture(modified="2025-07-19T01:00:00-04:00"), URL)

    def test_wrong_author_excluded(self):
        with self.assertRaisesRegex(Excluded, "author_mismatch"):
            parse_article(fixture(pick="Other Author's Pick: Athletics +120"), URL)

    def test_missing_pick_not_inferred_from_body(self):
        with self.assertRaisesRegex(Excluded, "pick_heading"):
            parse_article(fixture().replace(b"Test Writer's Pick:", b"Why"), URL)

    def test_join_uses_official_home_away_and_oakland_alias(self):
        row = join_article(parse_article(fixture(), URL), [official()], odds(), 2025)
        self.assertEqual(row["title"], "[MLB] 2025년 7월 19일 클리가디 vs 애슬레틱")
        self.assertEqual(row["pick"], "애슬레틱 승")
        self.assertEqual(row["selection"], "away")
        self.assertEqual(row["official_id"], "mlb:123")

    def test_doubleheader_same_time_ambiguous(self):
        first, second = official(), official()
        second.update(official_id="mlb:456", game_number=2)
        with self.assertRaisesRegex(Excluded, "ambiguous"):
            join_article(parse_article(fixture(), URL), [first, second], odds(), 2025)

    def test_tbd_kickoff_excluded(self):
        game = official()
        game["kickoff_is_tbd"] = True
        with self.assertRaises(Excluded):
            join_article(parse_article(fixture(), URL), [game], odds(), 2025)

    def test_odds_must_match_exact_time(self):
        index = odds()
        next(iter(index.values()))[0]["kickoff"] = "2025-07-19T08:15:00+09:00"
        with self.assertRaisesRegex(Excluded, "exact_proto_odds"):
            join_article(parse_article(fixture(), URL), [official()], index, 2025)

    def test_multiple_odds_rows_not_chosen_by_price(self):
        index = odds()
        values = next(iter(index.values()))
        values.append(copy.deepcopy(values[0]))
        with self.assertRaises(Excluded):
            join_article(parse_article(fixture(), URL), [official()], index, 2025)

    def test_copy_detection(self):
        row = {"body": "This is a synthetic copied article with a sufficiently long phrase."}
        with self.assertRaisesRegex(Excluded, "possible_wstv_copy"):
            copy_check(row, [({"url": "https://example.test"}, shingles(row["body"]))])

    def test_fixed_windows_and_url_padding(self):
        windows = parse_windows(["2025-08-01:2025-08-14", "2025-09-01:2025-09-14"])
        self.assertTrue(in_windows(date(2025, 8, 1), windows))
        self.assertTrue(in_windows(date(2025, 9, 14), windows))
        self.assertFalse(in_windows(date(2025, 8, 15), windows))
        self.assertFalse(in_windows(date(2025, 7, 31), windows))
        self.assertTrue(in_windows(date(2025, 7, 31), windows, padding=1))
        self.assertEqual(url_game_date(BASE + "/mlb-picks/a-prediction-8-1-2025-picks/"), date(2025, 8, 1))
        self.assertIsNone(url_game_date(BASE + "/mlb-picks/undated/"))

    def test_request_cap_counts_excluded_articles(self):
        namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
        sitemap = BASE + "/free-pick-sitemap1.xml"
        urls = [BASE + f"/mlb-picks/team{i}-prediction-9-5-2025-picks/" for i in range(3)]
        invalid_url = BASE + "/mlb-picks/0-invalid-prediction-9-31-2025-picks/"
        calls = []

        class FakeFetcher:
            def __init__(self, *args):
                self.sources = {}

            def get(self, url):
                calls.append(url)
                if url.endswith("robots.txt"):
                    return b"", {}
                if url.endswith("sitemap_index.xml"):
                    return f'<sitemapindex xmlns="{namespace}"><sitemap><loc>{sitemap}</loc></sitemap></sitemapindex>'.encode(), {}
                if url == sitemap:
                    items = "".join(f"<url><loc>{u}</loc></url>" for u in [invalid_url] + urls)
                    return f'<urlset xmlns="{namespace}">{items}</urlset>'.encode(), {}
                return b"synthetic", {"sha256": "hash"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "official.json").write_text("[]")
            (root / "reference.json").write_text("[]")
            (root / "games.csv").write_text("year,league,market_family,n_way\n")
            args = SimpleNamespace(out=root / "out", official=root / "official.json",
                                   comparison=root / "reference.json", games_csv=root / "games.csv",
                                   year=2025, windows=["2025-09-01:2025-09-14"], target=0,
                                   start_sitemap=1, end_sitemap=1, max_articles=2, offline=True)
            with patch("scripts.collect_pickdawgz_corpus.Fetcher", FakeFetcher), \
                 patch("scripts.collect_pickdawgz_corpus.parse_article", side_effect=Excluded("test_rejection")), \
                 patch("builtins.print"):
                run(args)
            summary = json.loads((args.out / "summary.json").read_bytes())
            self.assertEqual(summary["accepted"], 0)
            self.assertEqual(summary["discovered"], 2)
            self.assertEqual(summary["excluded"], {"test_rejection": 2})
            self.assertTrue(summary["cap_reached"])
            self.assertEqual(summary["next_unprocessed_url"], urls[2])
            self.assertNotIn(urls[2], calls)
            self.assertNotIn(invalid_url, calls)
            self.assertEqual(summary["listing_excluded"], {"invalid_url_calendar_date": 1})

    def test_offline_recovery_preserves_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "cache"
            cache.mkdir()
            raw = fixture()
            (cache / "sample.html").write_bytes(raw)
            (cache / "sample.json").write_text(json.dumps({
                "cache_file": "sample.html", "url": URL, "sha256": sha(raw),
                "observed_at": "2026-10-26T00:00:00+00:00"}))
            (root / "official.json").write_text(json.dumps([official()]))
            (root / "reference.json").write_text("[]")
            csv_path = root / "games.csv"
            csv_path.write_text('year,league,market_family,n_way,is_void,date_text,home,away,odds,round,game_no,result\n'
                                '2025,MLB,승패,2,False,07.19(토) 08:10,클리가디,애슬레틱,"1.6,2.2",1,1,홈패\n', encoding="utf-8")
            row = join_article(parse_article(raw, URL), [official()], load_odds(csv_path, 2025), 2025)
            original = (json.dumps([row], ensure_ascii=False) + "\n\n").encode()
            (root / "articles.json").write_bytes(original)
            args = SimpleNamespace(out=root, official=root / "official.json", comparison=root / "reference.json",
                                   games_csv=csv_path, year=2025)
            with patch("urllib.request.urlopen", side_effect=AssertionError("No network allowed")), patch("builtins.print"):
                recover_existing(args)
            self.assertEqual((root / "articles.json").read_bytes(), original)
            summary = json.loads((root / "recovered-metadata" / "summary.json").read_bytes())
            self.assertEqual(summary["original_rows_revalidated"], 1)
            self.assertEqual(summary["original_articles_sha256"], sha(original))
            self.assertFalse(summary["original_file_written"])
            self.assertEqual(summary["network_requests"], 0)


if __name__ == "__main__":
    unittest.main()
