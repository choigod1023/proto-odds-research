import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import atomic_publish  # noqa: E402
import generate_picks  # noqa: E402
import generate_v2  # noqa: E402
from atomic_publish import PublishGuardError, publish_nonempty_json  # noqa: E402


@pytest.mark.parametrize(
    ("rounds", "records"),
    [([], [{"game": 1}]), ([101], [])],
)
def test_empty_source_never_replaces_existing_artifact(tmp_path, rounds, records):
    target = tmp_path / "picks.json"
    previous = '{"generation":"known-good"}'
    target.write_text(previous, encoding="utf-8")

    with pytest.raises(PublishGuardError):
        publish_nonempty_json(
            target, {"rounds": rounds, "picks": records},
            rounds=rounds, records=records, artifact_name="picks.json")

    assert target.read_text(encoding="utf-8") == previous
    assert not list(tmp_path.glob(".picks.json.*.tmp"))


def test_valid_publish_replaces_atomically_and_leaves_no_temp_file(tmp_path):
    target = tmp_path / "picks.json"
    target.write_text('{"generation":"old"}', encoding="utf-8")
    document = {"rounds": [101], "picks": [{"game": 1}]}

    assert publish_nonempty_json(
        target, document, rounds=document["rounds"], records=document["picks"],
        artifact_name="picks.json") == target

    assert json.loads(target.read_text(encoding="utf-8")) == document
    assert not list(tmp_path.glob(".picks.json.*.tmp"))


def test_failed_atomic_swap_preserves_previous_file_and_cleans_temp(
        tmp_path, monkeypatch):
    target = tmp_path / "picks.json"
    previous = '{"generation":"known-good"}'
    target.write_text(previous, encoding="utf-8")

    def fail_replace(source, destination):
        assert Path(source).parent == target.parent
        assert Path(destination) == target
        assert target.read_text(encoding="utf-8") == previous
        raise OSError("simulated swap failure")

    monkeypatch.setattr(atomic_publish.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated swap failure"):
        publish_nonempty_json(
            target, {"rounds": [101], "picks": [{"game": 1}]},
            rounds=[101], records=[{"game": 1}], artifact_name="picks.json")

    assert target.read_text(encoding="utf-8") == previous
    assert not list(tmp_path.glob(".picks.json.*.tmp"))


def _prepare_generate_picks(monkeypatch, tmp_path, live_rounds):
    history = pd.DataFrame([{
        "year": 2024, "league": "KBO", "home_team": "홈", "away_team": "원정",
    }])
    target = tmp_path / "picks.json"
    target.write_text('{"generation":"known-good"}', encoding="utf-8")
    monkeypatch.setattr(generate_picks, "OUT", target)
    monkeypatch.setattr(generate_picks, "CACHE", tmp_path / "missing-cache")
    monkeypatch.setattr(generate_picks, "load_results", lambda: history)
    monkeypatch.setattr(generate_picks, "run_elo", lambda frame: (frame, {}))
    monkeypatch.setattr(generate_picks, "fit_logistic", lambda frame: (0.0, 0.0))
    monkeypatch.setattr(
        generate_picks, "build_forms", lambda *args, **kwargs: ({}, {}))
    monkeypatch.setattr(generate_picks, "_session", object)
    monkeypatch.setattr(
        generate_picks, "find_live_rounds",
        lambda session, season, start: list(live_rounds))
    monkeypatch.setattr(generate_picks, "_fetch", lambda *args: [])
    return target


@pytest.mark.parametrize("live_rounds", [[], [101]])
def test_generate_picks_preserves_previous_json_on_empty_collection(
        tmp_path, monkeypatch, live_rounds):
    target = _prepare_generate_picks(monkeypatch, tmp_path, live_rounds)

    assert generate_picks.main() == 1
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "generation": "known-good"}


def _prepare_generate_v2(monkeypatch, tmp_path, live_rounds):
    output = tmp_path / "v2"
    output.mkdir()
    target = output / "picks_v2.json"
    target.write_text('{"generation":"known-good"}', encoding="utf-8")
    monkeypatch.setattr(generate_v2, "OUT", output)
    monkeypatch.setattr(generate_v2, "CACHE", tmp_path / "missing-cache")
    monkeypatch.setattr(generate_v2, "team_lambdas", lambda: {})
    monkeypatch.setattr(generate_v2, "_session", object)
    monkeypatch.setattr(generate_v2, "load_history", pd.DataFrame)
    monkeypatch.setattr(generate_v2, "build_forms", lambda *args, **kwargs: ({}, {}))
    monkeypatch.setattr(generate_v2, "starters", lambda **kwargs: {})
    monkeypatch.setattr(generate_v2, "lineup_profiles", lambda: {})
    monkeypatch.setattr(generate_v2, "team_tiers", lambda: {})
    monkeypatch.setattr(generate_v2, "shot_form", lambda: {})
    monkeypatch.setattr(
        generate_v2, "find_live_rounds",
        lambda session, season, start: list(live_rounds))
    monkeypatch.setattr(generate_v2, "_fetch", lambda *args: [])
    monkeypatch.setattr(generate_v2.commentary_llm, "flush", lambda: None)
    return target


@pytest.mark.parametrize("live_rounds", [[], [101]])
def test_generate_v2_preserves_previous_json_on_empty_collection(
        tmp_path, monkeypatch, live_rounds):
    target = _prepare_generate_v2(monkeypatch, tmp_path, live_rounds)

    assert generate_v2.main() == 1
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "generation": "known-good"}
