from copy import deepcopy
from datetime import datetime, timezone
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.form_probability_shadow import candidate
from src.prediction_runtime import PredictionRuntime
from src.ai_decision import build_decision_snapshot

NOW = "2026-09-26T01:00:00+00:00"
KICKOFF = "2026-09-26T10:00:00+00:00"


def game():
    rows = [{"date": f"2026-09-{d:02d}T18:00:00", "gf": 4, "ga": 4} for d in range(20, 25)]
    g = dict(year=2026, round=114, date="09.26(토) 19:00", sport="bs", league="KBO",
             home="KIA", away="SSG", form_src="database", form_before="2026-09-26T10:00:00",
             form_home={"recent_games": deepcopy(rows)}, form_away={"recent_games": deepcopy(rows)},
             options=[{"market": "승패", "label": "", "line": None, "n_way": 2,
                       "선택": s, "배당": o, "시장확률": p, "게임번호": "7100"}
                      for s, o, p in [("홈", 1.55, .6), ("원정", 2.05, .4)]])
    g["decision_snapshot"] = build_decision_snapshot(g, as_of=NOW, built_at=NOW)
    return g


def test_symmetric_baseline_does_not_change_operating_pick():
    g = game()
    before = deepcopy(g)
    result = candidate(g, observed_at=NOW, kickoff=KICKOFF)
    assert result["probability"] == .5
    assert result["status"] == "shadow"
    assert result["market_probability"] == .6
    assert result["affects_selection"] is False
    assert g == before


@pytest.mark.parametrize("reason,change", [
    ("unsupported_selected_market", lambda g: g.update(sport="sc")),
    ("missing_or_future_form_provenance", lambda g: g.update(form_src="unknown")),
    ("insufficient_recent_games", lambda g: g["form_home"].update(recent_games=[])),
    ("invalid_scores", lambda g: g["form_home"]["recent_games"][0].update(gf=float("nan"))),
    ("invalid_or_duplicate_result_times", lambda g: g["form_home"]["recent_games"][0].update(date="2026-09-27")),
])
def test_missing_or_future_data_fails_closed(reason, change):
    g = game()
    change(g)
    result = candidate(g, observed_at=NOW, kickoff=KICKOFF)
    assert result["probability"] is None
    assert result["reason"] == reason


def test_no_capture_at_t30_or_after():
    result = candidate(game(), observed_at="2026-09-26T09:30:00+00:00", kickoff=KICKOFF)
    assert result["reason"] == "not_before_t30"


def test_actual_shadow_and_inputs_survive_ledger_and_settlement(tmp_path):
    g = game()
    g["validation_shadow"] = candidate(g, observed_at=NOW, kickoff=KICKOFF)
    clock = [datetime(2026, 9, 26, 1, 1, tzinfo=timezone.utc)]
    runtime = PredictionRuntime(tmp_path / "ledger.jsonl", clock=lambda: clock[0])
    result = runtime.record_pregame(g, kickoff=KICKOFF, market_observed_at=NOW)
    row = result.record
    assert row["predictions"]["validation_shadow"]["probability"] == .5
    assert row["features"]["validation_shadow"]["inputs"]["home"][0]["gf"] == 4
    assert row["predictions"]["probability"] == .6
    assert row["predictions"]["probability_detail"]["ai_candidate"] is None
    clock[0] = datetime(2026, 9, 26, 14, tzinfo=timezone.utc)
    runtime.settle_latest(row["event_id"], outcome={"result": "hit", "selection_id": row["predictions"]["selection_id"]},
                          settled_at=clock[0], source="test_fixture")
    assert runtime.records()[-1]["snapshot_id"] == row["snapshot_id"]
    assert runtime.records()[0] == row
    from src.shadow_probability_evaluation import evaluate
    report = evaluate(runtime.records())
    assert report["paired"] == 1
    metrics = report["by_version"][g["validation_shadow"]["version"]]
    assert metrics["market_brier"] == pytest.approx(.16)
    assert metrics["candidate_brier"] == .25
    assert report["promotion_allowed"] is False
    assert report["roi"] is None
    assert evaluate(runtime.records()[:1])["paired"] == 0
    bad = deepcopy(runtime.records())
    bad[-1]["outcome"]["selection_id"] = "wrong"
    assert evaluate(bad)["excluded"]["identity_mismatch"] == 1


def test_light_refresh_captures_shadow_then_preserves_pinned_revision(monkeypatch):
    import src.live_market_refresh as module
    import team_form
    g = game()
    forms = {k: g[k] for k in ("form_src", "form_before", "form_home", "form_away")}
    class Provider:
        def __init__(self, *args, **kwargs): pass
        def for_game(self, *args): return deepcopy(forms)
    monkeypatch.setattr(module, "database_enabled", lambda: True)
    monkeypatch.setattr(team_form, "DatabaseTeamForms", Provider)
    feed = {"generated_at": NOW, "markets": {"114": {"7100": {
        "date": g["date"], "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "game_no": "7100", "market": "승패", "label": "", "n_way": 2,
        "odds": [1.55, 2.05], "result": "경기전"}}}}
    doc, _ = module.refresh_document({"live": [], "past": []}, feed,
                                     now=datetime.fromisoformat(NOW))
    saved = doc["live"][0]
    assert saved["validation_shadow"]["status"] == "shadow"
    original = deepcopy(saved["validation_shadow"])
    snapshot = deepcopy(saved["decision_snapshot"])
    saved["prediction_status"] = "recorded_pregame"
    saved["prediction_record"] = {"selection": "홈", "market": "승패"}
    feed["generated_at"] = "2026-09-26T02:00:00+00:00"
    feed["markets"]["114"]["7100"]["odds"] = [1.6, 1.9]
    module.refresh_document(doc, feed, now=datetime.fromisoformat(feed["generated_at"]))
    assert saved["validation_shadow"] == original
    assert saved["decision_snapshot"] == snapshot
