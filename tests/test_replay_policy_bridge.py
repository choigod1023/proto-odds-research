"""Exercise the actual JS selector through its outcome-free JSON CLI."""
import json
from pathlib import Path
import shutil
import subprocess
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "scripts" / "replay_recommendation_policy.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def option(i, p=.56, **changes):
    return {
        "selection_id": str(i), "event_key": f"event-{i}", "sport": "bs", "league": "KBO",
        "kickoff_at": "2023-01-01T18:00:00+09:00", "market": "승패", "market_label": "",
        "sel": "홈", "odds": 1.6, "market_prob": .56, "predicted_hit_prob": p,
        "is_market_favorite": True, "n_way": 2, "game_no": f"offer-{i}",
        "round": "research", **changes,
    }


def run_bridge(strategies):
    return subprocess.run(
        [NODE, str(BRIDGE)], input=json.dumps({"strategies": strategies}, ensure_ascii=False),
        capture_output=True, text=True, encoding="utf-8", timeout=15, cwd=ROOT,
    )


def select(rows):
    result = run_bridge({"test": rows})
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["strategies"]["test"]


def test_threshold_base_three_and_strong_extras():
    assert select([option(1, .55), option(2, .5499)])["highlighted"] == ["1"]
    rows = [option(i, p) for i, p in enumerate([.64, .63, .62, .60, .5999, .55])]
    result = select(rows)
    assert len(result["event_choices"]) == 6
    assert result["highlighted"] == ["0", "1", "2", "3"]


def test_primary_per_event_and_one_choice_per_event():
    result = select([
        option("low", .85, odds=1.2, event_key="same"),
        option("primary", .56, odds=1.5, event_key="same", market="핸디캡", market_label="H -1.5"),
        option("other", .6, event_key="same", market="언더오버", market_label="U 8.5"),
    ])
    assert result == {"event_choices": ["other"], "highlighted": ["other"]}
    assert select([option("fallback", .8, odds=1.2)])["event_choices"] == ["fallback"]


def test_primary_per_date_league_after_threshold():
    rows = [option("low", .85, odds=1.2), option("primary", .55, odds=1.5),
            option("other-league", .8, odds=1.2, league="MLB"),
            option("other-day", .8, odds=1.2, kickoff_at="2023-01-02T18:00:00+09:00")]
    assert set(select(rows)["highlighted"]) == {"primary", "other-league", "other-day"}
    assert select([option("low", .85, odds=1.2), option("weak", .5499)])["highlighted"] == ["low"]


def test_odds_exclusion_and_market_favorite_companions():
    rows = [option("ceiling", .9, odds=2.2), option("above", .9, odds=2.5),
            option("invalid", .9, odds=1), option("parity", .9, market="홀짝"),
            option("favorite", .56, event_key="pair", market_prob=.56),
            option("nonfavorite", .95, event_key="pair", sel="원정", market_prob=.44,
                   is_market_favorite=False),
            option("unmarked-dog", .95, event_key="pair", sel="무", market_prob=.3),
            option("false-alone", .95, is_market_favorite=False)]
    assert select(rows) == {"event_choices": ["favorite"], "highlighted": ["favorite"]}


def test_kst_midnight_and_independent_sport_quotas():
    # Both times are Jan 1 UTC; KST dates must nevertheless get separate quotas.
    rows = [option(f"before-{i}", kickoff_at="2023-01-01T23:59:00+09:00") for i in range(4)]
    rows += [option(f"after-{i}", kickoff_at="2023-01-02T00:01:00+09:00") for i in range(4)]
    rows += [option(f"soccer-{i}", sport="sc", kickoff_at="2023-01-02T00:01:00+09:00") for i in range(4)]
    result = select(rows)
    assert len(result["event_choices"]) == 12
    assert len(result["highlighted"]) == 9
    for prefix in ("before", "after", "soccer"):
        assert sum(key.startswith(prefix) for key in result["highlighted"]) == 3


@pytest.mark.parametrize("field", ["won", "winner", "result", "hit", "final_score"])
def test_reject_outcomes_even_when_null(field):
    result = run_bridge({"test": [option(1, **{field: None})]})
    assert result.returncode != 0 and result.stdout == ""
    assert f"forbidden outcome field {field}" in result.stderr


def test_duplicate_ids_rejected_per_strategy():
    result = run_bridge({"test": [option(1), option(1, event_key="different")]})
    assert result.returncode != 0 and result.stdout == ""
    assert "duplicate selection_id" in result.stderr
    result = run_bridge({"first": [option(1)], "second": [option(1)], "empty": []})
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["strategies"]["first"] == payload["strategies"]["second"]
    assert payload["strategies"]["empty"] == {"event_choices": [], "highlighted": []}
    assert payload["policy_constants"] == {
        "preferred_auto_odds": 1.5, "max_auto_odds_exclusive": 2.2,
        "daily_highlight_min_hit": .55, "daily_highlight_base_per_league": 3,
        "daily_highlight_strong_min_hit": .6,
    }
    assert payload["historical_runtime_replay"] is False


@pytest.mark.parametrize("changes", [
    {"market_prob": 0}, {"market_prob": 1}, {"predicted_hit_prob": -.1},
    {"predicted_hit_prob": "0.6"}, {"predicted_hit_prob": None},
    {"kickoff_at": "2023-01-01T18:00:00Z"}, {"kickoff_at": "2023-01-01T18:00:00"},
    {"kickoff_at": "2023-02-30T18:00:00+09:00"}, {"is_market_favorite": "true"},
    {"probability_lower_bound": .5}, {"final_reversal": False},
])
def test_reject_invalid_inputs(changes):
    result = run_bridge({"test": [option(1, **changes)]})
    assert result.returncode != 0 and result.stdout == ""


def test_event_metadata_must_be_consistent():
    result = run_bridge({"test": [option(1), option(2, event_key="event-1", sport="sc")]})
    assert result.returncode != 0
    assert "inconsistent event metadata" in result.stderr


@pytest.mark.parametrize("split_bytes", [1, 2])
def test_utf8_character_split_across_stdin_chunks(split_bytes):
    rows = [option("favorite", league="테스트리그", event_key="same"),
            option("companion", league="테스트리그", event_key="same", sel="원정",
                   market_prob=.44, is_market_favorite=False)]
    strategies = {"selector_market": rows}
    expected = run_bridge(strategies)
    assert expected.returncode == 0, expected.stderr
    payload = json.dumps({"strategies": strategies}, ensure_ascii=False).encode("utf-8")
    boundary = payload.index("테".encode("utf-8")) + split_bytes
    with subprocess.Popen([NODE, str(BRIDGE)], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT) as process:
        try:
            process.stdin.write(payload[:boundary])
            process.stdin.flush()
            # Let Node consume the incomplete character before the next write.
            time.sleep(.25)
            stdout, stderr = process.communicate(payload[boundary:], timeout=15)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
    assert process.returncode == 0, stderr.decode("utf-8")
    assert stderr == b""
    assert json.loads(stdout.decode("utf-8")) == json.loads(expected.stdout)
