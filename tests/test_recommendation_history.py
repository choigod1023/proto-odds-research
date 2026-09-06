import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from recommendation_history import capture_history, highlights, selection_key, settle_history
from runtime_db import RuntimeDatabase

NOW = datetime(2026, 9, 6, 0, tzinfo=timezone.utc)


def candidate(i=1, **kw):
    return {"home": f"H{i}", "away": f"A{i}", "date": "09.06(일) 12:00", "sport": "bs", "league": "MLB",
            "kickoff_at": "2026-09-06T12:00:00+09:00", "round": 1, "game_no": i,
            "market": "승패", "market_label": "", "sel": "홈", "odds": 1.6,
            "market_prob": .56, "predicted_hit_prob": .56, "n_way": 2, **kw}


def payload(rows, now=NOW):
    return {"generated_at": now.isoformat(), "candidates": rows}


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
    script = "import {dailyHighlightedSelections,selectionKey} from './web/src/lib/unified-recommendation.js';let input='';for await(const c of process.stdin) input+=c;console.log(JSON.stringify(dailyHighlightedSelections(JSON.parse(input)).map(r=>selectionKey(r))));"
    actual = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(rows),
                            text=True, encoding="utf-8", capture_output=True, cwd=ROOT, check=True)
    assert highlights(rows) == set(json.loads(actual.stdout))
