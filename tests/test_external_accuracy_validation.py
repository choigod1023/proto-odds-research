import sys
import json
import hashlib
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from external_accuracy_validation import (MARKET_CONFIDENCE_CUTOFF,
                                           MODEL_CONFIDENCE_CUTOFF, ODDS_FLOOR,
                                           all_market_accuracy, load_frozen_rule)


def test_all_market_accuracy_uses_market_direction():
    frame = pd.DataFrame({"p_market": [.6, .4, .7], "y": [1., 0., 0.]})
    assert all_market_accuracy(frame) == 2/3


def test_external_rule_matches_frozen_kbo_report():
    report = json.loads((Path(__file__).resolve().parents[1] / "findings"
                         / "accuracy_pareto.json").read_text(encoding="utf-8"))
    assert ODDS_FLOOR == report["selected_rule"]["odds_floor"]
    assert MODEL_CONFIDENCE_CUTOFF == report["selected_rule"]["confidence_cutoff"]
    assert MARKET_CONFIDENCE_CUTOFF == report["test_2026"]["market_selector"][
        "confidence_cutoff"]


def test_generated_external_report_matches_current_rule_artifact():
    root = Path(__file__).resolve().parents[1]
    rule = json.loads((root / "findings" / "accuracy_rule.json").read_text(encoding="utf-8"))
    report = json.loads((root / "findings" / "external_accuracy_validation.json").read_text(
        encoding="utf-8"))
    assert report["frozen_rule"]["artifact_sha256"] == rule["artifact_sha256"]
    assert report["validation_type"].startswith("cutoff transportability audit")


def test_frozen_rule_rejects_tampered_artifact(tmp_path):
    source = Path(__file__).resolve().parents[1] / "findings" / "accuracy_rule.json"
    rule = json.loads(source.read_text(encoding="utf-8"))
    rule["model_confidence_cutoff"] += .01
    tampered = tmp_path / "rule.json"
    tampered.write_text(json.dumps(rule), encoding="utf-8")
    try:
        load_frozen_rule(tampered)
    except ValueError as error:
        assert "hash mismatch" in str(error)
    else:
        raise AssertionError("tampered artifact was accepted")


def test_frozen_rule_rejects_rehashed_but_unregistered_artifact(tmp_path):
    source = Path(__file__).resolve().parents[1] / "findings" / "accuracy_rule.json"
    rule = json.loads(source.read_text(encoding="utf-8"))
    rule.pop("artifact_sha256")
    rule["model_confidence_cutoff"] += .01
    canonical = json.dumps(rule, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    rule["artifact_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    tampered = tmp_path / "rule.json"
    tampered.write_text(json.dumps(rule), encoding="utf-8")
    try:
        load_frozen_rule(tampered)
    except ValueError as error:
        assert "reviewed" in str(error)
    else:
        raise AssertionError("unregistered rehashed artifact was accepted")
