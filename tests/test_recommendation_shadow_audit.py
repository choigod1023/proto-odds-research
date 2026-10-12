import copy
import unittest
from datetime import timedelta

from scripts.audit_recommendation_shadow import audit, eligible, payout, replay, stamp


def snapshot(count=20, result="miss"):
    history = {}
    for day in range(count):
        at = stamp("2026-09-01T10:00:00+09:00") + timedelta(days=day)
        for leg in range(2):
            key = f"{day}-{leg}"
            history[key] = dict(id=key, recorded_at=at.isoformat(), published_at=at.isoformat(),
                kickoff_at=(at+timedelta(hours=4)).isoformat(),
                settled_at=(at+timedelta(hours=7)).isoformat(), probability=.6, odds=1.8,
                sport="sc", league="test", home=key, away="away", round=day,
                recommended=True, result=result, result_source="official", policy="old")
    return dict(generated_at="2026-09-30T23:00:00+09:00", recommendation_history=history)


class ShadowTests(unittest.TestCase):
    def test_round_original_product(self):
        self.assertEqual(payout([1.8, 1.8]), 33000)
        self.assertEqual(payout([1.49, 1]), 15000)

    def test_monthly_limit(self):
        result = audit(snapshot())
        for w in ("2", "3"):
            x = result["cohorts"]["historical_flags_mixed_policies"]["weekly"][w]
            self.assertEqual(x["net"], -50000)
            self.assertEqual(x["tickets"], 5)
            self.assertEqual(x["max_drawdown"], 50000)
        self.assertEqual(result["verified_recommendations"], 0)
        self.assertIsNone(result["verified_cash_control"]["roi"])

    def test_pending_exposure_and_no_future_payout(self):
        doc = snapshot(result="hit")
        for r in doc["recommendation_history"].values():
            r["settled_at"] = "2026-10-20T12:00:00+09:00"
        x = audit(doc)["cohorts"]["historical_flags_mixed_policies"]["weekly"]["3"]
        self.assertEqual(x["tickets"], 5)
        self.assertEqual(x["pending_stake"], 50000)
        self.assertIsNone(x["roi"])
        self.assertTrue(all("payout" not in t for t in x["ticket_log"]))
        changed = copy.deepcopy(doc)
        for r in changed["recommendation_history"].values():
            r["result"] = "miss"
        self.assertEqual(audit(doc), audit(changed))

    def test_cross_month_pending_blocks_new_exposure(self):
        doc = snapshot()
        cutoff = stamp(doc["generated_at"])
        rows, _ = eligible(doc, cutoff)
        for r in rows:
            r["s"] = stamp("2026-12-01T12:00:00+09:00")
        extra = copy.deepcopy(rows[:2])
        for r in extra:
            r["t"] = stamp("2026-10-01T10:00:00+09:00")
            r["k"] = r["t"]+timedelta(hours=4)
            r["event"] = (r["k"], r["id"])
        x = replay(rows+extra, stamp("2026-10-30T12:00:00+09:00"), 3)
        self.assertEqual(x["tickets"], 5)

    def test_official_miss_settles_without_other_result(self):
        doc = snapshot(1)
        doc["recommendation_history"]["0-1"]["result"] = "unknown"
        doc["recommendation_history"]["0-1"]["settled_at"] = None
        x = audit(doc)["cohorts"]["historical_flags_mixed_policies"]["weekly"]["2"]
        self.assertEqual(x["net"], -10000)
        self.assertEqual(x["pending_stake"], 0)

    def test_same_event_cannot_pair_and_late_snapshot_excluded(self):
        doc = snapshot(1)
        doc["recommendation_history"]["0-1"]["home"] = "0-0"
        self.assertEqual(audit(doc)["cohorts"]["p55_proxy"]["weekly"]["2"]["tickets"], 0)
        doc = snapshot(1)
        doc["recommendation_history"]["0-1"]["published_at"] = "2026-09-01T13:30:00+09:00"
        self.assertEqual(audit(doc)["cohorts"]["p55_proxy"]["weekly"]["2"]["tickets"], 0)

    def test_void_and_flags_preserved(self):
        doc = snapshot(1, result="hit")
        before = copy.deepcopy(doc)
        audit(doc)
        self.assertEqual(doc, before)
        doc["recommendation_history"]["0-1"]["result"] = "void"
        x = audit(doc)["cohorts"]["historical_flags_mixed_policies"]["weekly"]["2"]
        self.assertEqual(x["net"], 8000)
        doc["recommendation_history"]["0-1"]["recommended"] = False
        self.assertEqual(audit(doc)["cohorts"]["historical_flags_mixed_policies"]["weekly"]["2"]["tickets"], 0)


if __name__ == "__main__":
    unittest.main()
