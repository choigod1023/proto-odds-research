"""Finite local collection/intake/replay; no AI polling, production or wallet writes."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.multisport_pregame_study import canonical, compare, digest, moment

COLLECTORS = {"baseball": "collect_baseball_comparison_sources.py",
              "soccer": "collect_soccer_comparison_sources.py",
              "basketball": "collect_basketball_comparison_sources.py",
              "proto_prices": "collect_proto_comparison_prices.py",
              "tipsters": "collect_tipster_comparison_sources.py"}
MAX_BODY_BYTES = 16_000_000


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_records(path):
    raw = path.read_bytes()
    if path.suffix == ".gz":
        raw = gzip.decompress(raw)
    try:
        obj = json.loads(raw)
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict) and isinstance(obj.get("records"), list):
            return obj["records"]
        if isinstance(obj, dict) and "record_id" in obj:
            return [obj]
    except json.JSONDecodeError:
        pass
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def receipt_index(roots):
    """Only explicit operator-provided roots; never follow a source-controlled path."""
    index, checked, errors = set(), 0, []
    for root in roots:
        root = root.resolve()
        paths = sorted(root.rglob("*.json"))
        if len(paths) > 2000:
            raise ValueError("receipt_manifest_scan_cap")
        for path in paths:
            if path.stat().st_size > 2_000_000:
                continue
            try:
                meta = json.loads(path.read_bytes())
                if not isinstance(meta, dict) or not meta.get("body_sha256") or not meta.get("url") or not meta.get("received_at"):
                    continue
                status = meta.get("http_status", meta.get("status"))
                if status != 200:
                    continue
                filename = meta.get("body_file") or path.with_suffix(".body.gz").name
                body_path = (path.parent / filename).resolve()
                if not body_path.is_relative_to(root):
                    raise ValueError("body_path_escapes_operator_evidence_root")
                if body_path.suffix == ".gz":
                    with gzip.open(body_path, "rb") as f:
                        raw = f.read(MAX_BODY_BYTES + 1)
                else:
                    with body_path.open("rb") as f:
                        raw = f.read(MAX_BODY_BYTES + 1)
                if len(raw) > MAX_BODY_BYTES or sha(raw) != meta["body_sha256"]:
                    raise ValueError("receipt_body_hash_or_size_mismatch")
                received = moment(meta["received_at"]).isoformat()
                index.add((meta["body_sha256"], meta["url"], received, meta.get("timestamp_basis")))
                checked += 1
            except (ValueError, OSError, TypeError, KeyError) as exc:
                errors.append(dict(manifest=str(path), error=str(exc)))
    return index, dict(verified_cached_bodies=checked, errors=errors,
                      trust="local manifest/body consistency; not independent receipt-time certification")


def verified_models(registry_path):
    if registry_path is None:
        return set()
    allowed = set()
    for entry in json.loads(registry_path.read_bytes()):
        raw = Path(entry["artifact_file"]).read_bytes()
        if sha(raw) != entry["artifact_sha256"]:
            raise ValueError("model_artifact_hash_mismatch")
        allowed.add((entry["model_id"], entry["artifact_sha256"], moment(entry["train_end"]).isoformat()))
    return allowed


def normalize(record, receipts, models):
    row = deepcopy(record)
    # Drop caller assertions; receipt/body and model-file checks are recomputed.
    row["receipt_verified"] = False
    row["model_artifact_verified"] = False
    try:
        row["receipt_verified"] = (row.get("body_sha256"), row.get("source_url"), moment(row.get("received_at")).isoformat(), row.get("timestamp_basis")) in receipts
    except (TypeError, ValueError):
        pass
    p = row.get("payload") or {}
    if row.get("kind") == "model_prediction":
        try:
            row["model_artifact_verified"] = (p.get("model_id"), p.get("artifact_sha256"), moment(p.get("train_end")).isoformat()) in models
        except (TypeError, ValueError):
            pass
    # Preserve raw worker fields. Unknown settlement/product terms stay unknown;
    # no guessed crosswalk may turn a historical quote into an executable price.
    if row.get("kind") == "price" and "prices" in p and "odds" not in p:
        row["payload"]["odds"] = deepcopy(p["prices"])
    return row


def collect(config):
    if not isinstance(config, list) or len(config) > 9:
        raise ValueError("finite_registered_collection_batch_required")
    calls = []
    for job in config:
        sport = job["collector"]
        if sport not in COLLECTORS or not isinstance(job.get("args"), list) or not all(isinstance(a, str) and "\x00" not in a for a in job["args"]):
            raise ValueError("invalid_registered_collector_job")
        timeout = job.get("timeout_seconds", 600)
        if type(timeout) is not int or not 1 <= timeout <= 900:
            raise ValueError("invalid_finite_collector_timeout")
        command = [sys.executable, "-B", "-X", "utf8", str(ROOT / "scripts" / COLLECTORS[sport]), *job["args"]]
        try:
            run = subprocess.run(command, cwd=ROOT, shell=False, capture_output=True, timeout=timeout)
            calls.append(dict(collector=sport, returncode=run.returncode,
                              stdout_sha256=sha(run.stdout), stderr_sha256=sha(run.stderr),
                              stderr_tail=run.stderr.decode("utf8", errors="replace")[-1000:]))
        except subprocess.TimeoutExpired:
            calls.append(dict(collector=sport, returncode=None, status="finite_timeout"))
    return calls


def replay(directory):
    manifest = json.loads((directory / "manifest.json").read_bytes())
    raw = gzip.decompress((directory / "records.json.gz").read_bytes())
    if sha(raw) != manifest["normalized_records_sha256"]:
        raise ValueError("normalized_input_hash_mismatch")
    computed = compare(json.loads(raw), **manifest["comparison_args"])
    saved = json.loads(gzip.decompress((directory / "results.json.gz").read_bytes()))
    if digest(computed) != manifest["result_sha256"] or computed != saved:
        raise ValueError("saved_comparison_replay_mismatch")
    return {k: v for k, v in computed.items() if k not in {"events", "blocked"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", default=[])
    parser.add_argument("--receipt-root", type=Path, action="append", default=[])
    parser.add_argument("--model-registry", type=Path)
    parser.add_argument("--collect-config", type=Path)
    parser.add_argument("--from", dest="start", default="2025-01-01T00:00:00+09:00")
    parser.add_argument("--until", dest="end")
    parser.add_argument("--evaluated-at")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--replay", type=Path)
    args = parser.parse_args()
    if args.replay:
        print(json.dumps(replay(args.replay), ensure_ascii=False))
        return
    if not args.input or args.output is None:
        parser.error("explicit input(s) and new output directory required")
    if args.output.exists():
        raise FileExistsError("choose a new output directory; prior research is immutable")
    calls = collect(json.loads(args.collect_config.read_bytes())) if args.collect_config else []
    receipts, body_audit = receipt_index(args.receipt_root)
    models = verified_models(args.model_registry)
    records, inputs = [], []
    for path in args.input:
        values = read_records(path)
        records.extend(normalize(r, receipts, models) for r in values)
        inputs.append(dict(path=str(path), file_sha256=sha(path.read_bytes()), record_count=len(values)))
    now = datetime.now(timezone.utc).isoformat()
    comparison_args = dict(start=args.start, end=args.end or now, evaluated_at=args.evaluated_at or now)
    result = compare(records, **comparison_args)
    args.output.mkdir(parents=True, exist_ok=False)
    raw = canonical(records)
    (args.output / "records.json.gz").write_bytes(gzip.compress(raw, mtime=0))
    (args.output / "results.json.gz").write_bytes(gzip.compress(canonical(result), mtime=0))
    summary = {k: v for k, v in result.items() if k not in {"events", "blocked"}}
    (args.output / "summary.json").write_bytes(canonical(summary))
    manifest = dict(inputs=inputs, comparison_args=comparison_args, normalized_records_sha256=sha(raw),
                    result_sha256=digest(result), receipt_audit=body_audit, collector_calls=calls,
                    model_artifact_count=len(models), schedules_activated=False, production_mutations=False,
                    source_semantics_and_training_lineage="Require source-specific independent review; a matching hash/declared train_end is not semantic certification.")
    (args.output / "manifest.json").write_bytes(canonical(manifest))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
