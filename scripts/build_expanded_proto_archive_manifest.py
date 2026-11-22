"""Verify and deduplicate archived official Proto captures; never fetch or alter raw data."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

DETAIL_URL = "https://www.betman.co.kr/buyPsblGame/gameInfoInq.do"


def verified_body(root: Path, relative: str, expected_hash: str, round_id: str) -> dict:
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("archive path escapes its declared source root")
    raw = path.read_bytes()
    if raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError(f"body hash mismatch: {path}")
    body = json.loads(raw)
    if str(body.get("gmTs")) != round_id:
        raise ValueError(f"request/body round mismatch: {path}")
    if str(body.get("currentLottery", {}).get("gmTs")) != round_id:
        raise ValueError(f"lottery round mismatch: {path}")
    schedules = body.get("compSchedules", {})
    if not isinstance(schedules.get("keys"), list) or not isinstance(schedules.get("datas"), list):
        raise ValueError(f"missing raw schedule table: {path}")
    if len(set(schedules["keys"])) != len(schedules["keys"]):
        raise ValueError("duplicate schedule columns")
    if any(len(row) != len(schedules["keys"]) for row in schedules["datas"]):
        raise ValueError("schedule row/column length mismatch")
    return body


def build(base_manifest: Path, attempt_files: list[Path]) -> dict:
    base = json.loads(base_manifest.read_bytes())
    roots = list(base["source_roots"])
    sources, seen_hashes, seen_rounds, variants = [], set(), set(), []
    duplicate_captures = 0

    def add(source: dict, root: Path, provenance: str) -> None:
        nonlocal duplicate_captures
        round_id = str(source["round"])
        if len(round_id) != 6 or not round_id.isdigit() or round_id[:2] not in {"25", "26"}:
            return
        body = verified_body(root, source["body_file"], source["body_sha256"], round_id)
        key = (round_id, source["body_sha256"])
        if key in seen_hashes:
            duplicate_captures += 1
            return
        seen_hashes.add(key)
        if round_id in seen_rounds:
            variants.append({"round": round_id, "body_sha256": source["body_sha256"],
                             "provenance": provenance, "selection": "excluded_duplicate_round_variant"})
            return
        seen_rounds.add(round_id)
        root_string = str(root)
        if root_string not in roots:
            roots.append(root_string)
        sources.append({**source, "source_root_index": roots.index(root_string),
                        "source_url": DETAIL_URL, "provenance": provenance,
                        "timing_basis": "retrospective", "timestamp_basis": "legacy_unverified",
                        "execution_price_verified": False, "purchase_availability_verified": False,
                        "raw_product_count": len(body["compSchedules"]["datas"])})

    # Preserve the old cohort's exact snapshots. Extra captures are ordered by
    # receipt time and path, never by settlement, model accuracy, or return.
    for source in base["sources"]:
        add(source, Path(base["source_roots"][source["source_root_index"]]), str(base_manifest))
    extras = []
    for attempts_path in attempt_files:
        for record in json.loads(attempts_path.read_bytes()):
            request = record.get("request_json") or {}
            if (record.get("url") != DETAIL_URL or record.get("http_status") != 200
                    or request.get("gmId") != "G101"):
                continue
            round_id = str(request.get("gmTs", ""))
            if len(round_id) != 6 or not round_id.isdigit() or round_id[:2] not in {"25", "26"}:
                continue
            relative = record.get("body_file")
            if not isinstance(relative, str):
                raise ValueError("successful detail capture has no body path")
            # Legacy attempts store evidence/... relative to the experiment.
            candidates = {(p / relative).resolve(): (p, relative)
                          for p in (attempts_path.parent, attempts_path.parent.parent)
                          if (p / relative).is_file()}
            flat = attempts_path.parent / Path(relative).name
            if flat.is_file():
                candidates[flat.resolve()] = (attempts_path.parent, flat.name)
            if len(candidates) != 1:
                raise ValueError(f"ambiguous or missing archive root: {attempts_path} / {relative}")
            root, relative = next(iter(candidates.values()))
            record = {**record, "body_file": relative}
            extras.append((str(record.get("received_at", "")), str(attempts_path),
                           record, root))
    for _, provenance, record, root in sorted(extras, key=lambda row: (row[0], row[1], row[2]["body_file"])):
        add({"round": str(record["request_json"]["gmTs"]), "body_file": record["body_file"],
             "body_sha256": record["body_sha256"], "received_at": record.get("received_at")}, root, provenance)
    return {"version": "expanded-official-archives-v1", "start": "2025-01-01", "end": "2026-11-05",
            "source_roots": roots, "sources": sources, "source_round_count": len(sources),
            "duplicate_captures": duplicate_captures, "excluded_round_variants": variants,
            "selection_policy": "base manifest snapshot first; new rounds earliest received_at then attempts path",
            "timing_basis": "retrospective", "execution_price_verified": False,
            "purchase_availability_verified": False,
            "base_manifest_sha256": hashlib.sha256(base_manifest.read_bytes()).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-manifest", type=Path, required=True)
    parser.add_argument("--attempts", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.base_manifest, args.attempts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\r\n")
    print(json.dumps({"rounds": manifest["source_round_count"],
                      "duplicate_captures": manifest["duplicate_captures"],
                      "round_variants": len(manifest["excluded_round_variants"]),
                      "output": str(args.output)}))


if __name__ == "__main__":
    main()
