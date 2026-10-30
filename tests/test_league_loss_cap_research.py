import unittest
from scripts.league_loss_cap_research import simulate, cap_comparison, normalize, filter_experiment, matched_model_selection


def row(i, day, net=-1, **kwargs):
    return dict(cohort="test", league="KBO", model="saved", event_id=str(i),
                kickoff=day, odds=2, net_per_unit=net, **kwargs)


class LossCapTests(unittest.TestCase):
    def test_extra_losses_are_not_hidden(self):
        rows = [row(i, f"2026-01-{i+1:02d}") for i in range(10)]
        r = cap_comparison(rows)
        self.assertEqual(r["capped"]["net"], -50000)
        self.assertEqual(r["uncapped"]["net"], -100000)
        self.assertEqual(r["extra_roi"], -1)

    def test_skipped_recovery_is_explicit(self):
        rows = [row(i, f"2026-01-{i+1:02d}", -1 if i < 5 else 1) for i in range(10)]
        r = cap_comparison(rows)
        self.assertEqual(r["capped"]["net"], -50000)
        self.assertEqual(r["uncapped"]["net"], 0)
        self.assertEqual(r["extra_net"], 50000)

    def test_simultaneous_pending_stakes_reserve_budget(self):
        r = simulate([row(i, "2026-01-01", 1) for i in range(7)])
        self.assertEqual(r["n"], 5)
        self.assertEqual(r["max_drawdown"], 0)
        self.assertEqual(r["minimum_starting_bankroll"], 50000)
        self.assertEqual(simulate([row(i, "2026-01-01", 1) for i in range(7)], cap=None)["minimum_starting_bankroll"], 70000)

    def test_pending_prior_month_is_reserved(self):
        rows = [row(i, "2026-01-31", 1) for i in range(5)] + [row(9, "2026-02-01", 1)]
        r = simulate(rows, settlement_lag_days=2)
        self.assertEqual(r["n"], 5)
        self.assertEqual(r["monthly_net"]["2026-01"], 50000)
        self.assertEqual(r["monthly_net"]["2026-02"], 0)

    def test_profits_replenish_budget_and_reset_month(self):
        rows = [row(0, "2026-01-01", 1)] + [row(i, f"2026-01-{i+1:02d}") for i in range(1, 8)]
        rows.append(row(9, "2026-02-01", -1))
        r = simulate(rows)
        self.assertEqual(r["monthly_net"], {"2026-01":-50000, "2026-02":-10000})

    def test_outcomes_do_not_choose_admission(self):
        rows = [row(i, "2026-01-01", -1) for i in range(7)]
        first = simulate(rows)
        second = simulate([{**r, "net_per_unit":1} for r in reversed(rows)])
        self.assertEqual([x["id"] for x in first["tickets"]], [x["id"] for x in second["tickets"]])

    def test_same_time_drawdown_is_batch_not_label_order(self):
        r = simulate([row(0, "2026-01-01", -1), row(1, "2026-01-01", 1)], cap=None)
        self.assertEqual(r["max_drawdown"], 0)

    def test_duplicate_and_future_result_validation(self):
        r = row(0, "2026-01-01")
        with self.assertRaises(ValueError):
            normalize([r, r])
        with self.assertRaises(ValueError):
            simulate([row(0, "2026-01-02", settled_at="2026-01-01")])

    def test_filter_selection_is_training_only(self):
        rows = [row(i, f"2026-01-{1+i//4:02d}", 1 if i%2 else -1,
                    probability=.6 if i%2 else .4) for i in range(120)]
        a = filter_experiment(rows)
        changed = [{**r, "net_per_unit":-r["net_per_unit"]} if r["kickoff"] >= a["split_date"] else r for r in rows]
        b = filter_experiment(changed)
        self.assertEqual(a["selected_on_train_only"], b["selected_on_train_only"])
        self.assertEqual(a["selected_on_train_only"], "probability_55")
        self.assertEqual(a["test_selected"]["roi"], 1)
        self.assertEqual(b["test_selected"]["roi"], -1)

    def test_no_cap_effect_if_budget_not_hit(self):
        r = cap_comparison([row(0, "2026-01-01", 1), row(1, "2026-01-02", -1)])
        self.assertEqual(r["extra_bets"], 0)
        self.assertEqual(r["roi_delta"], 0)

    def test_model_selection_does_not_repick_after_test_losses(self):
        rows = [row(i, f"2026-01-{1+i//4:02d}", 1 if i < 80 else -1) for i in range(120)]
        rows += [{**r, "model":"other", "net_per_unit":0} for r in rows]
        r = matched_model_selection(rows)["KBO/test"]
        self.assertEqual(r["selected_model"], "saved")
        self.assertEqual(r["selected_rule"], "all")
        self.assertEqual(r["test"]["roi"], -1)

    def test_unsupported_head_does_not_shrink_valid_matched_population(self):
        rows = [row(i, f"2026-01-{1+i//4:02d}", 1) for i in range(120)]
        rows += [{**r, "model":"other", "net_per_unit":0} for r in rows]
        rows.append({**rows[0], "model":"unsupported", "eligible_for_policy_selection":False})
        r = matched_model_selection(rows)["KBO/test"]
        self.assertEqual(r["matched_events"], 120)
        self.assertEqual(r["selected_model"], "saved")


if __name__ == "__main__":
    unittest.main()
