from collections import Counter
import gzip
import hashlib
from io import StringIO
import json
from pathlib import Path
from unittest.mock import patch

import primary_archive as archive


REPO = Path(__file__).resolve().parents[2]


def test_archived_prices_labels_and_probabilities_are_preserved():
    originals = {}
    source_path = archive.SOURCE.parent / "inputs" / "domestic.jsonl.gz"
    with gzip.open(REPO / source_path, "rt", encoding="utf-8") as stream:
        for row in map(json.loads, stream):
            originals[row["id"]] = row
    expected = {}
    for sport in ("sc", "bs", "bk", "vl"):
        path = archive.SOURCE / sport / "domestic_archive" / "ledger.jsonl.gz"
        with gzip.open(REPO / path, "rt", encoding="utf-8") as stream:
            for row in map(json.loads, stream):
                original = originals.pop(row["id"])
                assert all(row[key] == value for key, value in original.items())
                if (row["sport"], row["league"]) != ("bk", "KBL"):
                    expected[row["id"]] = row
    assert not originals

    counts = Counter()
    previous_day = ""
    for row in archive.rows(REPO):
        source = expected.pop(row["id"])
        assert row["day"] >= previous_day
        previous_day = row["day"]
        for key in ("odds", "y", "league", "sport", "day"):
            assert row[key] == source[key]
        assert row["prob"] == {
            name: source["predictions"][name]["p"]
            for name in ("market", "outcome_form", "market_residual", "half_market_form")
        }
        assert row["prob"]["market"] == source["market_p"]
        assert row["prob"]["half_market_form"] == [
            (market + form) / 2 for market, form in zip(
                row["prob"]["market"], row["prob"]["outcome_form"]
            )
        ]
        assert row["line"] is None and row["period"] == "full_game"
        assert row["provenance"].endswith(":market_fallback") == source["fallback"]
        counts[row["sport"], row["market"]] += 1
    assert not expected
    assert counts == {("sc", "1x2"): 23998, ("bs", "win_loss"): 14978,
                      ("bk", "win_loss"): 5721, ("vl", "win_loss"): 2005}


def test_audit_counts_archive_exclusions_and_fallback():
    audit = archive.audit_counts(REPO)
    assert [c["source"] for c in audit.values()] == [23998, 14978, 6748, 2005]
    assert [c["market_fallback"] for c in audit.values()] == [346, 708, 239, 275]
    assert audit["bk"]["kbl_excluded"] == 1027
    for counts in audit.values():
        assert counts["source"] == counts["included"] + counts["kbl_excluded"]


def test_audit_hashes_match_frozen_archive():
    root = REPO / archive.SOURCE.parent
    handoff = json.loads((root / "handoff.json").read_text(encoding="utf-8"))
    for files in archive.audit_metadata(REPO).values():
        for name, metadata in files.items():
            path = REPO / metadata["path"]
            body = path.read_bytes()
            assert metadata["sha256"] == hashlib.sha256(body).hexdigest()
            if name == "states.json":
                body = body.replace(b"\r\n", b"\n")
            key = path.relative_to(root).as_posix()
            assert hashlib.sha256(body).hexdigest() == handoff["artifact_lf_sha256"][key]


def test_adapter_omits_void_push_and_models_fit_through_target_day():
    q = [0.5, 0.5]
    models = {
        "outcome_form": {"offset": False, "training_rows": 200},
        "market_residual": {"offset": True, "training_rows": 200},
    }
    digest = hashlib.sha256(json.dumps(models, sort_keys=True).encode()).hexdigest()
    base = {
        "id": "fixture", "day": "2026-01-02", "sport": "vl", "cohort": "domestic_archive",
        "league": "KOVO", "y": 1, "odds": [2, 2], "fallback": False, "market_p": q,
        "month_state_sha256": digest, "predictions": {name: {"p": q} for name in archive.MODELS},
    }
    state = {
        "month": "2026-01", "training_cutoff_inclusive": base["day"], "states": models,
        "state_sha256": digest, "fallback": False, "training_rows": 200,
    }
    source = [base, dict(base, is_void=True), dict(base, is_push=True), dict(base, y=None)]
    stream = StringIO("\n".join(map(json.dumps, source)))
    counts = Counter()
    with patch.object(Path, "read_text", return_value=json.dumps([state])):
        with patch.object(archive.gzip, "open", return_value=stream):
            assert list(archive._sport_rows(REPO, "vl", counts)) == []
    assert counts == {"source": 4, "void_push_or_unlabeled": 3, "unverified_model_timing": 1}

    state["training_cutoff_inclusive"] = "2025-12-28"
    invalid_blend = dict(base, predictions=dict(base["predictions"], half_market_form={"p": [0.6, 0.4]}))
    wrong_digest = dict(base, month_state_sha256="unverified")
    stream = StringIO("\n".join(map(json.dumps, [base, invalid_blend, wrong_digest])))
    counts.clear()
    with patch.object(Path, "read_text", return_value=json.dumps([state])):
        with patch.object(archive.gzip, "open", return_value=stream):
            accepted = list(archive._sport_rows(REPO, "vl", counts))
    assert len(accepted) == 1
    assert counts == {
        "source": 3, "included": 1, "market_fallback": 0,
        "invalid_probability": 1, "unverified_model_timing": 1,
    }
