from copy import deepcopy
import pytest
from scripts.audit_non_soccer_performance import audit, source, stats


def prediction():
    return dict(record_type="prediction", event_id="e", snapshot_id="s",
                kickoff="2026-11-08T10:00:00Z", captured_at="2026-11-08T09:00:00Z",
                as_of="2026-11-08T09:00:00Z", market_observed_at="2026-11-08T09:00:00Z",
                features={"fixture": {"sport": "bk", "league": "NBA"},
                          "tipster_hybrid": dict(status="applied", authors=0, selection_source="market_fallback")},
                model={"selection_policy": "tipster-vote-v1"},
                predictions=dict(action="market_reference", market="승패", odds=1.8, selection_id="sel"))


def test_latest_revision_replaces_grade_and_keeps_price_and_legacy_separate():
    p = prediction()
    old = dict(record_type="settlement", snapshot_id="s", ledger_sequence=2,
               outcome=dict(result="miss", selection_id="sel"))
    new = dict(old, ledger_sequence=3, outcome=dict(result="hit", selection_id="sel"))
    legacy = deepcopy(p)
    legacy.update(event_id="old-event", snapshot_id="old-snapshot", model={"selection_policy": "legacy"})
    result = audit([p, new, old, legacy])  # Intentionally out of file order.
    assert result["current_primary_pre_t30"]["by_sport"]["bk"]["hits"] == 1
    assert result["current_primary_pre_t30"]["by_sport"]["bk"]["roi"] == .8
    assert result["primary_all_policies"]["by_sport"]["bk"]["captured"] == 2
    assert source(p) == "market_fallback"
    late = deepcopy(p)
    late["captured_at"] = "2026-11-08T09:30:00Z"
    assert audit([late])["current_primary_pre_t30"]["by_sport"] == {}
    with pytest.raises(ValueError, match="canonical"):
        audit([p, p])


def test_refund_is_not_a_hit_or_loss_and_pending_is_not_staked():
    s = stats([dict(result="hit", net_units=".8"), dict(result="miss", net_units="-1"),
               dict(result="void", net_units="0"), dict(result="pending", net_units="0")])
    assert s["decided"] == 2 and s["settled"] == 3 and s["hit_rate"] == .5
    assert s["roi"] == pytest.approx(-.2/3)
    assert s["wilson95"][0] < .5 < s["wilson95"][1]
