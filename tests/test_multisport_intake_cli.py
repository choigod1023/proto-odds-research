import gzip
import hashlib
import json

import pytest

from scripts.run_pregame_multisport_study import (
    receipt_index, normalize, verified_models, read_records, collect, replay,
)


def write_json(path, obj):
    path.write_bytes(json.dumps(obj).encode())


def metadata(tmp_path, *, filename="one.body.gz"):
    raw = b'{"status":"before"}'
    (tmp_path / filename).write_bytes(gzip.compress(raw, mtime=0))
    meta = dict(url="https://www.koreabaseball.com/source", received_at="2026-11-04T05:00:00Z",
                status=200, body_sha256=hashlib.sha256(raw).hexdigest(), body_file=filename)
    write_json(tmp_path / "one.json", meta)
    return meta


def test_manifest_url_clock_and_uncompressed_body_hash_all_bind_receipt(tmp_path):
    meta = metadata(tmp_path)
    index, audit = receipt_index([tmp_path])
    assert audit["verified_cached_bodies"] == 1
    row = dict(kind="fixture", payload={}, body_sha256=meta["body_sha256"], source_url=meta["url"], received_at=meta["received_at"])
    assert normalize(row, index, set())["receipt_verified"] is True
    for key in ("body_sha256", "source_url", "received_at"):
        changed = dict(row); changed[key] = "wrong"
        assert normalize(changed, index, set())["receipt_verified"] is False


def test_asserted_receipt_is_not_a_manifest(tmp_path):
    row = dict(kind="fixture", payload={}, receipt_verified=True, model_artifact_verified=True)
    actual = normalize(row, set(), set())
    assert actual["receipt_verified"] is False and actual["model_artifact_verified"] is False


def test_body_mutation_fails_closed(tmp_path):
    metadata(tmp_path)
    (tmp_path / "one.body.gz").write_bytes(gzip.compress(b"changed"))
    index, audit = receipt_index([tmp_path])
    assert not index and "mismatch" in audit["errors"][0]["error"]


def test_source_manifest_cannot_read_outside_explicit_evidence_root(tmp_path):
    metadata(tmp_path)
    obj = json.loads((tmp_path / "one.json").read_bytes())
    obj["body_file"] = "../outside.body.gz"
    write_json(tmp_path / "one.json", obj)
    index, audit = receipt_index([tmp_path])
    assert not index and "escapes" in audit["errors"][0]["error"]


def test_model_registry_file_hash_and_training_clock_bind_forecast(tmp_path):
    model = tmp_path / "artifact.json"; model.write_bytes(b'{"coefficient":1}')
    entry = dict(model_id="kbo_m0", artifact_file=str(model), artifact_sha256=hashlib.sha256(model.read_bytes()).hexdigest(), train_end="2024-12-31T00:00:00Z")
    path = tmp_path / "registry.json"; write_json(path, [entry])
    registry = verified_models(path)
    row = dict(kind="model_prediction", payload=entry, model_artifact_verified=True)
    assert normalize(row, set(), registry)["model_artifact_verified"] is True
    row["payload"]["train_end"] = "2026-12-31T00:00:00Z"
    assert normalize(row, set(), registry)["model_artifact_verified"] is False
    model.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="model_artifact_hash"):
        verified_models(path)


def test_record_files_support_json_array_jsonlines_and_gzip(tmp_path):
    records = [dict(record_id="a"), dict(record_id="b")]
    for name, raw in (("a.json", json.dumps(records).encode()),
                      ("b.jsonl", b'\n'.join(json.dumps(x).encode() for x in records)),
                      ("c.gz", gzip.compress(json.dumps(records).encode()))):
        p = tmp_path / name; p.write_bytes(raw)
        assert read_records(p) == records


def test_only_finite_registered_python_collectors_can_execute():
    with pytest.raises(ValueError, match="registered_collector"):
        collect([dict(collector="source-controlled-shell", args=["delete"] )])
    with pytest.raises(ValueError, match="finite_registered"):
        collect([{}] * 10)


def test_changed_comparison_artifact_is_not_silently_replayed(tmp_path):
    write_json(tmp_path / "manifest.json", dict(normalized_records_sha256="0"*64))
    (tmp_path / "records.json.gz").write_bytes(gzip.compress(b"[]"))
    with pytest.raises(ValueError, match="normalized_input_hash"):
        replay(tmp_path)
