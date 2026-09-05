"""Prediction readers use DB inputs without materializing legacy files."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import bets
import cup_tier
import evolutionary_policy
import generate_v2
import model_v2
import pitcher_form
from runtime_db import RuntimeDatabase


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("PROODD_DB_PATH", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setattr(generate_v2, "ROOT", tmp_path)
    monkeypatch.setattr(generate_v2, "CACHE", tmp_path / "archives")
    monkeypatch.setattr(cup_tier, "PROC", tmp_path / "data" / "processed")
    return RuntimeDatabase()


def forbid_fixture_reads(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("DB mode attempted a legacy file read")
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(pd, "read_csv", forbidden)
    monkeypatch.setattr(Path, "glob", forbidden)


def selector_artifact():
    artifact = {"schema": "evolutionary-selection-v1", "profiles": {},
                "default_profile": "balanced"}
    artifact["artifact_sha256"] = evolutionary_policy.canonical_hash(artifact)
    return artifact


def boxscores():
    return {str(day): {
        "date": f"2026-04-{day:02d}", "home": "Home", "away": "Away",
        "data": {
            "home": [{"pcode": "001", "name": "Home starter", "inn": "6",
                      "er": "2", "hr": "0", "bb": "1", "kk": "7"}],
            "away": [{"pcode": "002", "name": "Away starter", "inn": "4",
                      "er": "4", "hr": "1", "bb": "3", "kk": "2"}],
        },
    } for day in range(1, 6)}


def shots():
    return {str(day): {
        "date": f"2026-04-{day:02d}", "home": "Home", "away": "Away",
        "data": {"home": {"sog": 5, "goals": 2},
                 "away": {"sog": 3, "goals": 1}},
    } for day in range(1, 6)}


def lineup_rows():
    return [{"team": "김천상무", "churn": "2", "n_reserve": "3",
             "formation_changed": "False", "formation": "4-4-2"}
            for _ in range(20)] + [
                {"team": "김천상무", "churn": "", "n_reserve": "9",
                 "formation_changed": "True", "formation": "3-5-2"}]


def game_row(**changes):
    return {
        "year": "2026", "round": "2", "game_no": "001",
        "date_text": "04.01(수) 18:00", "sport": "bs", "league": "KBO",
        "home": "Home 3", "away": "1 Away", "is_void": "False",
        "market_family": "승패", "n_way": "2", "result": "홈승",
        "odds": "1.60,2.10", **changes,
    }


def store_rows(database, name, rows):
    database.replace_dataset_rows(name, rows, list(rows[0]))


def test_probability_and_selector_documents_are_fileless(database, tmp_path, monkeypatch):
    probability = {"schema": "probability-pipeline-v1", "models": {"sc": {}}}
    selector = selector_artifact()
    database.put_document("model_probability_pipeline_v1", probability)
    database.put_document("model_evolutionary_selector", selector)
    forbid_fixture_reads(monkeypatch)

    assert generate_v2._load_probability_artifact(tmp_path / "absent.json") == probability
    assert evolutionary_policy.load_artifact(tmp_path / "absent.json") == selector


@pytest.mark.parametrize("payload", [[], "invalid", 3, {"artifact_sha256": "bad"}])
def test_optional_model_documents_fail_closed(database, tmp_path, payload):
    database.put_document("model_evolutionary_selector", payload)
    assert evolutionary_policy.load_artifact(tmp_path / "absent.json") is None
    if not isinstance(payload, dict):
        database.put_document("model_probability_pipeline_v1", payload)
        assert generate_v2._load_probability_artifact(tmp_path / "absent.json") is None


def test_selector_db_document_still_checks_hash(database, tmp_path):
    artifact = selector_artifact()
    artifact["default_profile"] = "challenge"
    database.put_document("model_evolutionary_selector", artifact)
    assert evolutionary_policy.load_artifact(tmp_path / "absent.json") is None


def test_missing_optional_inputs_never_fall_back_to_stale_files(database, tmp_path, monkeypatch):
    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps(selector_artifact()), encoding="utf-8")
    detail = tmp_path / "data" / "raw" / "detail"
    detail.mkdir(parents=True)
    (detail / "kleague_shots_2023_2026.json").write_text(json.dumps(shots()), encoding="utf-8")
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    pd.DataFrame(lineup_rows()).to_csv(processed / "lineup_soccer.csv", index=False)
    forbid_fixture_reads(monkeypatch)

    assert generate_v2._load_probability_artifact(stale) is None
    assert evolutionary_policy.load_artifact(stale) is None
    assert evolutionary_policy.live_snapshot([], None)["status"] == "unavailable"
    assert generate_v2.shot_form() == {}
    assert generate_v2.lineup_profiles() == {}
    with pytest.raises(FileNotFoundError):
        pitcher_form.load_starter_boxscores(stale)
    with pytest.raises(FileNotFoundError):
        pitcher_form.StarterForm.from_artifact(stale)
    assert generate_v2._load_starter_form() is None


def test_shot_and_lineup_profiles_are_fileless_and_typed(database, monkeypatch):
    database.put_document("detail_kleague_shots", shots())
    store_rows(database, "processed_lineup_soccer", lineup_rows())
    forbid_fixture_reads(monkeypatch)

    assert generate_v2.shot_form()["Home"] == {"sog": 5.0, "sog_a": 3.0, "conv": .4}
    profiles = generate_v2.lineup_profiles()
    assert profiles["김천"] == profiles["김천상무"] == {
        "churn": 2.0, "reserve": 3.0, "form_change": 0.0,
        "formation": "4-4-2", "n": 20,
    }


def test_empty_optional_datasets_have_empty_profiles(database):
    database.put_document("detail_kleague_shots", {})
    database.replace_dataset_rows("processed_lineup_soccer", [], list(lineup_rows()[0]))
    assert generate_v2.shot_form() == {}
    assert generate_v2.lineup_profiles() == {}


def test_pitcher_boxscores_and_artifact_round_trip_without_files(database, tmp_path, monkeypatch):
    database.put_document("detail_kbo_baseball", boxscores())
    forbid_fixture_reads(monkeypatch)
    rows = pitcher_form.load_starter_boxscores(tmp_path / "absent-detail.json")
    assert len(rows) == 5
    assert rows[0]["home_sp"]["pcode"] == "001"
    form = generate_v2._load_starter_form()
    assert form.xfip("001", "2026-04-03") is None
    delta = form.matchup_delta("Home starter", "Away starter", "2026-04-06")
    assert delta["xfip_diff"] > 0

    path = tmp_path / "absent-model.json"
    pitcher_form.write_artifact(path, tmp_path / "absent-detail.json")
    assert not path.exists()
    rebuilt = pitcher_form.StarterForm.from_artifact(path)
    assert rebuilt.matchup_delta("Home starter", "Away starter", "2026-04-06") == delta
    with database.transaction() as connection:
        connection.execute("DELETE FROM documents WHERE name='detail_kbo_baseball'")
    fallback = generate_v2._load_starter_form()
    assert fallback.matchup_delta("Home starter", "Away starter", "2026-04-06") == delta


def test_archive_round_hints_use_only_db_document_names(database, tmp_path, monkeypatch):
    for name in ("archive:2026:2", "archive:2026:010", "archive:2026:9",
                 "archive:2025:999", "archive:2026:invalid", "archive:2026:3:extra"):
        database.put_document(name, "<html>archived response</html>")
    stale = tmp_path / "archives" / "2026"
    stale.mkdir(parents=True)
    (stale / "999.html.gz").touch()
    forbid_fixture_reads(monkeypatch)
    assert generate_v2._archive_rounds(2026) == [2, 9, 10]
    assert generate_v2._archive_rounds(2024) == []


def test_attach_odds_db_matches_csv_and_excludes_voids_and_conflicting_resales(
        database, tmp_path, monkeypatch):
    rows = [game_row(), game_row(round="3"),
            game_row(game_no="002", date_text="04.01(수) 14:00", odds="1.50,2.20"),
            game_row(game_no="002", round="3", date_text="04.01(수) 14:00"),
            game_row(is_void="True", odds="1.01,1.01")]
    frame = pd.DataFrame({
        "kickoff": pd.to_datetime(["2026-04-01 14:00", "2026-04-01 18:00"]),
        "league": ["KBO", "KBO"], "home_team": ["Home", "Home"],
        "away_team": ["Away", "Away"],
    })
    path = tmp_path / "games.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    with monkeypatch.context() as fixture_mode:
        fixture_mode.delenv("PROODD_DB_PATH")
        expected = model_v2.attach_odds(frame, path)
    path.unlink()
    store_rows(database, "processed_games", rows)
    forbid_fixture_reads(monkeypatch)
    actual = model_v2.attach_odds(frame, path)
    pd.testing.assert_frame_equal(actual, expected)
    assert actual["o_home"].tolist() == [1.60]
    assert actual["kickoff"].tolist() == [pd.Timestamp("2026-04-01 18:00")]


def test_processed_games_selftests_and_cup_read_without_csv(database, monkeypatch):
    store_rows(database, "processed_games", [game_row(league="한국FA컵", sport="sc")])
    # Match-history migration belongs to another task; isolate only this dependency.
    monkeypatch.setattr(cup_tier, "load_matches", lambda: pd.DataFrame([
        {"year": 2026, "league": "K리그1", "home_team": "Home", "away_team": "Other"},
        {"year": 2026, "league": "K리그2", "home_team": "Away", "away_team": "Other2"},
    ]))
    forbid_fixture_reads(monkeypatch)
    assert generate_v2._selftest() == 0
    assert bets._selftest() is None
    assert cup_tier.main() == 0


def test_required_games_missing_does_not_read_stale_csv(database, tmp_path, monkeypatch):
    stale = tmp_path / "games.csv"
    pd.DataFrame([game_row()]).to_csv(stale, index=False)
    forbid_fixture_reads(monkeypatch)
    with pytest.raises(KeyError, match="processed_games"):
        model_v2.attach_odds(pd.DataFrame(), stale)
    assert bets._selftest() is None


def test_document_and_profile_fixture_mode_remains_available(tmp_path, monkeypatch):
    monkeypatch.delenv("PROODD_DB_PATH", raising=False)
    monkeypatch.setattr(generate_v2, "ROOT", tmp_path)
    monkeypatch.setattr(generate_v2, "CACHE", tmp_path / "archives")
    artifact = selector_artifact()
    path = tmp_path / "model.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    assert evolutionary_policy.load_artifact(path) == artifact
    assert generate_v2._load_probability_artifact(path) == artifact

    detail = tmp_path / "data" / "raw" / "detail"
    detail.mkdir(parents=True)
    (detail / "kleague_shots_2023_2026.json").write_text(json.dumps(shots()), encoding="utf-8")
    processed = tmp_path / "data" / "processed"
    processed.mkdir(parents=True)
    pd.DataFrame(lineup_rows()).to_csv(processed / "lineup_soccer.csv", index=False)
    assert generate_v2.shot_form()["Home"]["sog"] == 5.0
    assert generate_v2.lineup_profiles()["김천"]["form_change"] == 0.0

    path.write_text(json.dumps(boxscores()), encoding="utf-8")
    model_path = tmp_path / "pitcher.json"
    pitcher_form.write_artifact(model_path, path)
    assert pitcher_form.StarterForm.from_artifact(model_path).xfip("001", "2026-04-06") is not None
    archive = tmp_path / "archives" / "2026"
    archive.mkdir(parents=True)
    for name in ("10.html.gz", "2.html.gz", "invalid.html.gz"):
        (archive / name).touch()
    assert generate_v2._archive_rounds(2026) == [2, 10]
