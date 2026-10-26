"""Synthetic fixtures: no third-party article text or network requests in tests."""
import copy
import json
import unittest

from scripts.collect_pickdawgz_corpus import Excluded, join_article, parse_article, parse_pick, copy_check, shingles

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


if __name__ == "__main__":
    unittest.main()
