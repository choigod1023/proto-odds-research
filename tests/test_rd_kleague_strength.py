"""Offline regressions including actual public-source field projections."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("rd_kleague_strength", ROOT / "scripts/rd_kleague_strength.py")
rd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rd)
from rd_asof_evidence import feature_gate


def game(day, ids=range(11), *, team="A", goal=None, assists=0, source=None):
    return {"date": day, "team": team, "home": team, "away": "B", "league": "K리그1",
            "source_id": source or day + team, "source_kind": "test_actual_stat_schema",
            "source_sha256": "a" * 64, "source_hashes": ["a" * 64],
            "source_url": "https://www.kleague.com/match.do", "observed_at": "2026-10-30T01:00:00Z",
            "field_paths": {"goal": "data.listH[].goal", "assists": "data.listH[].assist"},
            "players": [{"player_id": f"{i+20000000}", "goal": (goal or {}).get(i, 0),
                         "assists": assists, "position": "FW", "starter": True} for i in ids]}


def case():
    return {"cohort": "fixed", "event_id": "kleague:2026:1:88", "league": "K리그1",
            "home": "A", "away": "B", "kickoff": "2026-03-12T15:00:00+09:00",
            "decision_at": "2026-03-12T14:30:00+09:00", "available_at": "2026-03-10T01:00:00Z"}


class StrengthTests(unittest.TestCase):
    def setUp(self):
        self.h = [game("2026-03-01", goal={0: 2}), game("2026-03-05", range(1, 12), goal={11: 3}),
                  game("2026-03-09", goal={0: 99})]
        self.decision = case()["decision_at"]
        self.target = ("2026-03-12", "A", "B")

    def f(self, h=None, decision=None, team="A"):
        return rd.team_features(self.h if h is None else h, team, "K리그1", decision or self.decision, self.target)

    def test_actual_goal_value_not_renamed_xi_counts(self):
        f, evidence, _, _ = self.f()
        self.assertAlmostEqual(f["xi_goal_known_mean"], 2 / 11)
        self.assertEqual(f["rotation_goal_net"], -1)
        h = copy.deepcopy(self.h)
        h[0]["players"][0]["goal"] = 6
        changed = self.f(h)[0]
        self.assertAlmostEqual(changed["xi_goal_known_mean"], 6 / 11)
        self.assertEqual(changed["rotation_goal_net"], 3)
        self.assertEqual(changed["xi_goal_known_share"], f["xi_goal_known_share"])
        self.assertEqual(evidence["rotation_goal_net"]["valuation_max_date"], "2026-03-05")

    def test_reference_match_stats_not_used_in_player_valuation(self):
        h = copy.deepcopy(self.h)
        for p in h[-1]["players"]:
            p.update(goal=0, assists=99)
        self.assertEqual(self.f()[0], self.f(h)[0])

    def test_target_future_prior_season_exclusion(self):
        noise = [game("2026-03-12", range(50, 61), goal={50: 99}),
                 game("2026-12-01"), game("2027-01-01"), game("2025-12-31", goal={0: 99})]
        self.assertEqual(self.f()[0], self.f(self.h + noise)[0])

    def test_calendar_end_plus24_full_day_boundary(self):
        h = [game("2026-03-01"), game("2026-03-10")]
        # March 10 calendar end is March 11 midnight; +24h is March 12 midnight.
        self.assertNotIn("xi_goal_known_mean", self.f(h, "2026-03-11T23:59:59+09:00")[0])
        self.assertIn("xi_goal_known_mean", self.f(h, "2026-03-12T00:00:00+09:00")[0])
        self.assertEqual(rd.bound("2026-03-10").isoformat(), "2026-03-12T00:00:00+09:00")

    def test_missing_assists_not_zero(self):
        h = copy.deepcopy(self.h)
        for g in h:
            for p in g["players"]:
                p["assists"] = None
        f, _, _, reasons = self.f(h)
        self.assertEqual(f["xi_assist_known_share"], 0)
        self.assertNotIn("xi_assist_known_mean", f)
        self.assertNotIn("rotation_assist_net", f)
        self.assertTrue(any("missing" in x for x in reasons))

    def test_zero_is_observed_stat_but_unknown_player_not_imputed(self):
        h = copy.deepcopy(self.h)
        h[-1]["players"][0]["player_id"] = "29999999"
        f = self.f(h)[0]
        self.assertAlmostEqual(f["xi_goal_known_share"], 10 / 11)
        self.assertEqual(f["xi_goal_known_mean"], 0)
        self.assertNotIn("rotation_goal_net", f)

    def test_identity_team_league_season_and_ambiguity(self):
        self.assertEqual(self.f(team="Unknown")[0], {})
        self.assertEqual(rd.team_features(self.h, "A", "K리그2", self.decision, self.target)[0], {})
        self.assertEqual(self.f([game("2025-12-31")])[0], {})
        self.assertEqual(self.f(self.h + [game("2026-03-05", source="duplicate")])[0], {})

    def test_stale_history_and_missing_stats(self):
        self.assertEqual(self.f(decision="2026-06-01T12:00:00+09:00")[0], {})
        h = copy.deepcopy(self.h)
        for g in h:
            for p in g["players"]:
                p.update(goal=None, assists=None)
        f = self.f(h)[0]
        self.assertFalse(any("mean" in k or "net" in k for k in f))
        self.assertEqual(self.f([])[0], {})

    def test_exact_case_decision_identity_no_labels(self):
        r = case()
        r.update(outcome=999, odds=[999], market_p=[999], target_xi=["secret_unused"])
        rows, _ = rd.extract([r], self.h)
        row = rows[0]
        self.assertEqual(row["decision_at"], r["decision_at"])
        for k in ("cohort", "event_id", "league", "kickoff"):
            self.assertEqual(row[k], r[k])
        for k in ("outcome", "odds", "market_p", "target_xi"):
            self.assertNotIn(k, row)
        r["decision_at"] = r["kickoff"]
        self.assertEqual(rd.extract([r], self.h)[0], [])
        with self.assertRaises(ValueError):
            rd.extract([case(), case()], self.h)

    def test_no_invented_publication_and_parent_asof_gates(self):
        row = rd.extract([case()], self.h)[0][0]
        for key, value in row["features"].items():
            e = row["feature_evidence"][key]
            self.assertIsNone(e["max_fact_time"])
            self.assertIsNone(e["publication_at"])
            self.assertEqual(e["fact_date_timezone"], "Asia/Seoul")
            self.assertTrue(feature_gate(value, e, row["decision_at"], view="retrospective_date_lagged")[0])
            self.assertFalse(feature_gate(value, e, row["decision_at"], view="captured_pregame")[0])


class SourceAdapterTests(unittest.TestCase):
    def setUp(self):
        self.sample = json.loads((rd.OUT / "adapter-source-projection.json").read_text(encoding="utf-8"))

    def parse(self, sample=None):
        s = copy.deepcopy(sample or self.sample)
        page = s["page"].encode()
        body = json.dumps(s["stats"]).encode()
        # Projections have their own hash; original HTTP digests remain explicitly separate.
        pm = {**s["page_meta"], "original_http_sha256": s["page_meta"]["source_sha256"], "source_sha256": rd.sha(page)}
        sm = {**s["stats_meta"], "original_http_sha256": s["stats_meta"]["source_sha256"], "source_sha256": rd.sha(body)}
        return rd.parse_official(page, body, s["identity"], pm, sm, s["schedule_meta"])

    def test_actual_captured_source_projection_matches_collected_records(self):
        rows = self.parse()
        actual = rd.read_gz(rd.OUT / "official-raw-stats.jsonl.gz")[:2]
        self.assertEqual([r["players"] for r in rows], [r["players"] for r in actual])
        self.assertEqual([len(r["players"]) for r in rows], [11, 11])
        self.assertTrue(all(p["minutes"] is not None for r in rows for p in r["players"]))
        self.assertEqual(rows[0]["source_kind"], "official_match_player_stats")

    def test_actual_adapter_rejects_wrong_game_team_and_league(self):
        for key, value in (("gameId", 92), ("homeTeam", "K99"), ("leagueId", 2)):
            s = copy.deepcopy(self.sample)
            s["identity"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.parse(s)
        s = copy.deepcopy(self.sample)
        s["stats"]["data"]["listH"][0]["teamId"] = "K99"
        with self.assertRaises(ValueError):
            self.parse(s)

    def test_actual_adapter_rejects_missing_duplicate_and_hash_tampering(self):
        s = copy.deepcopy(self.sample)
        s["stats"]["data"]["listH"].pop(0)
        with self.assertRaisesRegex(ValueError, "missing player stats"):
            self.parse(s)
        s = copy.deepcopy(self.sample)
        s["stats"]["data"]["listH"].append(s["stats"]["data"]["listH"][0])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.parse(s)
        with self.assertRaisesRegex(ValueError, "hash"):
            rd.parse_official(b"tampered", b"{}", self.sample["identity"], self.sample["page_meta"],
                              self.sample["stats_meta"], self.sample["schedule_meta"])

    def test_capture_source_date_bounds_and_unknown_stats(self):
        s = copy.deepcopy(self.sample)
        s["stats_meta"]["observed_at"] = "2025-01-01T00:00:00Z"
        with self.assertRaisesRegex(ValueError, "source date"):
            self.parse(s)
        s = copy.deepcopy(self.sample)
        s["stats"]["data"]["listH"][0]["goal"] = None
        s["stats"]["data"]["listH"][0]["minutePlay"] = None
        rows = self.parse(s)
        player = next(p for p in rows[0]["players"] if p["player_id"] == s["stats"]["data"]["listH"][0]["playerId"])
        self.assertIsNone(player["goal"])
        self.assertIsNone(player["minutes"])

    def test_missing_headshot_keeps_actual_ul_player_identity(self):
        parser = rd.GroundParser()
        parser.feed('<div class="ground"><div style="background-image:url(/v1/player/2026/K05/player_20000000.png)"></div>'
                    + ''.join(f'<ul class="player_{i+20000000}"></ul>' for i in range(11)) + '</div>')
        self.assertEqual(len(parser.ids[("2026", "K05")]), 11)

    def test_local_raw_identity_date_and_numeric_missing(self):
        key = "2026030101011"
        players = [{"playerId": str(20000000+i), "goal": None, "assists": None, "pos": "FW"} for i in range(11)]
        raw = {key: {"gameId": key, "date": "2026-03-01", "home": "A", "away": "B",
                     "data": {"home": {"players": players}, "away": {"players": players}}}}
        rows, _ = rd.local_rows(raw, "a"*64, "2026-10-30T00:00:00Z")
        self.assertIsNone(rows[0]["players"][0]["goal"])
        raw[key]["date"] = "2026-03-02"
        self.assertEqual(rd.local_rows(raw, "a"*64, "2026-10-30T00:00:00Z")[0], [])
        for x in [False, "0", -1, float("nan"), float("inf")]:
            self.assertIsNone(rd.number(x))
        self.assertEqual(rd.number(0), 0)

    def test_no_unsafe_url_or_unbounded_requests_and_hash_checked_cache(self):
        with tempfile.TemporaryDirectory() as d:
            f = rd.Fetcher(Path(d))
            for url in ("http://www.kleague.com/", "https://user:pass@www.kleague.com/", "https://example.com/", "https://www.kleague.com:444/", "https://data.kleague.com/public"):
                with self.assertRaises(ValueError):
                    f.get(url)
            f.records = [{"url": str(i)} for i in range(60)]
            with self.assertRaisesRegex(RuntimeError, "budget"):
                f.get("https://www.kleague.com/index.do")
            data = b"actual cached bytes"
            import gzip
            obj = f.cache / "test.gz"
            obj.write_bytes(gzip.compress(data))
            f.records = [{"url": "https://www.kleague.com/index.do", "cache_path": "raw-cache/test.gz", "source_sha256": rd.sha(data)}]
            with patch.object(rd, "build_opener", side_effect=AssertionError("cache must not fetch")):
                self.assertEqual(f.get("https://www.kleague.com/index.do")[0], data)
            obj.write_bytes(gzip.compress(b"tampered"))
            with self.assertRaisesRegex(ValueError, "cache hash"):
                f.get("https://www.kleague.com/index.do")


class ActualArtifactTests(unittest.TestCase):
    def test_all_emitted_fields_bounds_and_frozen_identity(self):
        identities = {(r["cohort"], r["event_id"], r["kickoff"]): r for r in rd.read_gz(rd.OUT / "input-identities.jsonl.gz")}
        history = {g["source_id"]: g for g in rd.read_gz(rd.OUT / "parsed-player-history.jsonl.gz")}
        total_fields = 0
        for filename in ("features.jsonl.gz", "archive-features.jsonl.gz"):
            for r in rd.read_gz(rd.OUT / filename):
                original = identities[(r["cohort"], r["event_id"], r["kickoff"])]
                self.assertEqual(r["decision_at"], original["decision_at"])
                for key, value in r["features"].items():
                    e = r["feature_evidence"][key]
                    total_fields += 1
                    self.assertTrue(feature_gate(value, e, r["decision_at"], view="retrospective_date_lagged")[0])
                    ref = history[e["reference_xi_source_id"]]
                    self.assertTrue(all(history[s]["date"] < ref["date"] for s in e["valuation_source_ids"]))
                    self.assertEqual(e["max_fact_date"], ref["date"])
                    self.assertLess(rd.stamp(r["decision_at"]), rd.stamp(e["observed_at"]))
                    self.assertTrue(e["source_sha256"] and e["raw_field_paths"])
        self.assertGreater(total_fields, 1000)

    def test_negative_probes_real_stats_and_prior_xi_contract(self):
        s = json.loads((rd.OUT / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(s["local_raw_games"], 816)
        self.assertEqual(s["local_team_rows"], s["PR424_raw_xi_agree_team_rows"])
        self.assertGreater(s["official_start_rows"], 0)
        self.assertGreater(s["local_missing_assists"], 0)
        log = [json.loads(x) for x in (rd.OUT / "http.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertLessEqual(len(log), 60)
        self.assertEqual(sum("data.kleague.com" in m["url"] for m in log), 1)
        self.assertTrue(all(m["observed_at"] and m["request_started_at"] for m in log))
        self.assertTrue(any(m["reason"] for m in log))


if __name__ == "__main__":
    unittest.main()
