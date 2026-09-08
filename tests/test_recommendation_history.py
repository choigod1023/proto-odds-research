import json
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from recommendation_history import capture_history, highlights, probability, selection_key, settle_history
from runtime_db import RuntimeDatabase

NOW = datetime(2026, 9, 6, 0, tzinfo=timezone.utc)


def candidate(i=1, **kw):
    return {"home": f"H{i}", "away": f"A{i}", "date": "09.06(일) 12:00", "sport": "bs", "league": "MLB",
            "kickoff_at": "2026-09-06T12:00:00+09:00", "round": 1, "game_no": i,
            "market": "승패", "market_label": "", "sel": "홈", "odds": 1.6,
            "market_prob": .56, "predicted_hit_prob": .56, "n_way": 2, **kw}


def payload(rows, now=NOW):
    return {"generated_at": now.isoformat(), "candidates": rows}


INVALID_PROBABILITIES = [None, "", "bad", 0, -0.1, 1, 1.2,
                         float("nan"), float("inf"), -float("inf"), "NaN", "Infinity"]


@pytest.mark.parametrize("invalid", INVALID_PROBABILITIES)
def test_invalid_final_falls_back_only_to_valid_market(invalid):
    row = candidate(predicted_hit_prob=invalid, market_prob="0.56")
    assert probability(row) == .56
    assert highlights([row]) == {selection_key(row)}


@pytest.mark.parametrize("invalid", INVALID_PROBABILITIES)
def test_invalid_market_fallback_is_missing_and_never_highlighted(invalid):
    row = candidate(predicted_hit_prob=1.2, market_prob=invalid)
    assert probability(row) is None
    assert highlights([row]) == set()
    entry = next(iter(capture_history(payload([row]), {}, NOW).values()))
    assert entry["probability"] is None
    assert entry["recommended"] is False


def test_legacy_final_probability_and_valid_final_remain_supported():
    row = candidate(final_probability="0.61")
    del row["predicted_hit_prob"]
    assert probability(row) == .61
    assert probability(candidate(predicted_hit_prob=.62, market_prob=1.2)) == .62


def test_capture_copies_available_provenance_without_substituting_publication_time():
    metadata = {
        "observed_at": "2026-09-05T23:50:00+00:00",
        "market_prob": .56, "predicted_hit_prob": .56,
        "price_source": "live_odds", "probability_source": "shin_market_fallback",
        "decision_id": "decision-original", "decision_model": "shin-market-anchor-v1",
        "decision_pipeline_status": "market_fallback", "decision_promotion_gate": None,
        "decision_artifact_hash": None, "decision_pipeline_applied": False,
        "has_validated_edge": False, "policy_authorized": False,
        "validated_uncertainty_available": False, "uncertainty_source": "shin_market_fallback",
        "probability_lower_bound": .56, "probability_interval": None,
        "selection_basis": "shin_market_fallback", "decision_evidence_ids": ["price-original"],
    }
    source = {
        "source_generated_at": "2026-09-05T23:55:00+00:00",
        "observed_at": "2026-09-05T23:54:00+00:00",
        "candidate_source": "live_odds", "probability_method": "shin",
        "live_odds_at": "2026-09-05T23:55:00+00:00",
        "recommendation_revision": "revision-original",
    }
    row = candidate(**deepcopy(metadata))
    current = {**payload([row]), **source}
    entry = next(iter(capture_history(current, {}, NOW).values()))
    for field, value in {**source, **metadata}.items():
        assert entry[field] == value
    assert entry["published_at"] == entry["recorded_at"] == NOW.isoformat()
    assert entry["probability"] == .56
    row["decision_evidence_ids"].append("later-price")
    assert entry["decision_evidence_ids"] == ["price-original"]


def test_missing_provenance_stays_missing_and_explicit_unknown_stays_unknown():
    row = candidate(observed_at=None, decision_artifact_hash=None)
    current = {**payload([row]), "observed_at": "2026-09-05T23:50:00+00:00"}
    entry = next(iter(capture_history(current, {}, NOW).values()))
    assert entry["observed_at"] is None
    assert entry["decision_artifact_hash"] is None
    assert entry["market_prob"] == .56
    for field in ("source_generated_at", "candidate_source", "probability_source",
                  "decision_model", "has_validated_edge", "validated_uncertainty_available"):
        assert field not in entry


@pytest.mark.parametrize("legacy", [False, True])
def test_t30_freezes_provenance_with_the_exact_offer_and_preserves_legacy_entries(legacy):
    row = candidate(decision_id="original", decision_evidence_ids=["original"])
    current = {**payload([row]), "source_generated_at": "2026-09-05T23:50:00+00:00"}
    history = capture_history(current, {}, NOW)
    if legacy:
        for entry in history.values():
            for field in ("decision_id", "decision_evidence_ids", "source_generated_at",
                          "market_prob", "predicted_hit_prob"):
                entry.pop(field, None)
    before = deepcopy(history)
    freeze = NOW + timedelta(hours=2, minutes=30)
    replacement = candidate(sel="원정", odds=1.8, predicted_hit_prob=.8,
                            decision_id="replacement", decision_evidence_ids=["replacement"])
    later = {**payload([replacement], freeze), "source_generated_at": freeze.isoformat()}
    assert capture_history(later, {"recommendation_history": history}, freeze) == before
    assert history == before


def test_freeze_dedup_partial_and_no_postgame_backfill():
    row = candidate()
    h = capture_history(payload([row]), {}, NOW)
    assert len(h) == 1
    key = next(iter(h))
    later = NOW + timedelta(hours=3)
    frozen = capture_history(payload([candidate(sel="원정"), candidate(2)], later),
                             {"recommendation_history": h}, later)
    assert frozen == h
    assert frozen[key]["sel"] == "홈"
    assert capture_history(payload([row]), {}, later) == {}
    assert capture_history(payload([], later), {"recommendation_history": h}, later) == h
    changed = capture_history(payload([candidate(predicted_hit_prob=.54)]),
                              {"recommendation_history": h}, NOW)
    assert changed[key]["recommended"] is False


def test_official_result_exact_line_persists_when_feed_expires():
    h = capture_history(payload([candidate()]), {}, NOW)
    key = next(iter(h))
    result = {**candidate(), "label": "", "result": "홈승"}
    later = NOW + timedelta(hours=5)
    settle_history(h, {"markets": {"1": {"1": {**result, "label": "H -1"}}}}, later)
    assert "result" not in h[key]
    settle_history(h, {"markets": {"1": {"1": result}}}, later)
    assert h[key]["result"] == "hit"
    settle_history(h, {}, later)
    assert h[key]["result"] == "hit"
    settle_history(h, {"markets": {"1": {"1": {**result, "result": "무효"}}}}, later)
    assert h[key]["result"] == "void"


def test_database_keeps_archive_and_rejects_stale_writer(tmp_path):
    db = RuntimeDatabase(tmp_path / "test.sqlite3")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    row = candidate(kickoff_at=(now+timedelta(hours=3)).isoformat())
    db.store_artifact("today_combo", payload([row], now))
    saved = db.get_artifact("today_combo")
    assert len(saved["recommendation_history"]) == 1
    db.store_artifact("today_combo", payload([], now-timedelta(hours=1)))
    assert db.get_artifact("today_combo") == saved
    db.store_artifact("today_combo", payload([], now))
    assert db.get_artifact("today_combo")["recommendation_history"] == saved["recommendation_history"]


def test_highlight_policy_matches_browser():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    rows = [candidate(i, predicted_hit_prob=p) for i, p in enumerate([.61,.61,.61,.60,.59,.58,.54],1)]
    rows += [candidate(20, odds=1.3, predicted_hit_prob=.8), candidate(21, market="홀짝", predicted_hit_prob=.9),
             candidate(22, final_reversal=True, predicted_hit_prob=.9),
             candidate(23, is_market_favorite=False, predicted_hit_prob=.9),
             candidate(24, league="other", odds=1.3, predicted_hit_prob=.8)]
    rows += [candidate(i, league="tied", predicted_hit_prob=.56) for i in (2, 1, 11, 10)]
    script = "import {dailyHighlightedSelections,selectionKey} from './web/src/lib/unified-recommendation.js';let input='';for await(const c of process.stdin) input+=c;console.log(JSON.stringify(dailyHighlightedSelections(JSON.parse(input)).map(r=>selectionKey(r))));"
    actual = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(rows),
                            text=True, encoding="utf-8", capture_output=True, cwd=ROOT, check=True)
    assert highlights(rows) == set(json.loads(actual.stdout))
