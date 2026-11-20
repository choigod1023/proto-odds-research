"""Freeze audited agent extracts into a portable offline research input.

Raw extracts live in separate .local-test/{MLB,KBO,NPB,OTHER}/records.json.
Source reports/manifests accompany the committed snapshot. No live I/O.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


def prepare(root, output):
    rows, sources, counts = [], {}, {}
    for group in ("MLB", "KBO", "NPB", "OTHER"):
        path = root/group/"records.json"
        raw = path.read_bytes()
        sources[group] = dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())
        doc = json.loads(raw)
        records = doc if isinstance(doc, list) else doc["records"]
        kept = []
        for original in records:
            r = dict(original)
            if group == "NPB":
                r["original_phase_cohort"] = r["cohort"]
                r["cohort"] = "tipster122/whole_retrospective"
            if group == "OTHER":
                if r["league"] in {"MLB", "KBO"}:
                    continue
                if not r["cohort"].startswith(r["dataset"]+"/"):
                    r["cohort"] = r["dataset"]+"/"+r["cohort"]
                if r["cohort"].endswith("/training"):
                    continue
                if r["dataset"].startswith("year_"):
                    if not r["common_all_models"]:
                        continue
                    r["original_phase_cohort"] = r["cohort"]
                    r["cohort"] = r["dataset"]+"/matched_whole_retrospective"
                # The original NPB study excluded these heads from selection:
                # historical starter coverage was missing, even though retrospective
                # evaluation forecasts were saved. Preserve them as diagnostics.
                if r["dataset"] == "npb_league_fit" and ":rotation-" in r["model"]:
                    r["eligible_for_policy_selection"] = False
                    r["policy_exclusion"] = "original_2023_starter_coverage_missing"
            r["source_group"] = group
            kept.append(r)
        rows.extend(kept)
        counts[group] = len(kept)
    payload = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":"))+"\n" for r in rows).encode()
    compressed = gzip.compress(payload, mtime=0)
    output.mkdir(parents=True, exist_ok=True)
    (output/"records.jsonl.gz").write_bytes(compressed)
    metadata = dict(source_normalized_sha256=sources, kept_by_group=counts, record_count=len(rows),
                    bytes=len(compressed), normalized_sha256=hashlib.sha256(compressed).hexdigest(),
                    unique_league_event_identifiers=len({(r["league"], r["event_id"]) for r in rows}),
                    description="Model/event rows, not new independent matches. IDs may differ between sources; cohorts overlap and must not be pooled. NPB122 and yearly common-model cohorts are continuous paths, with original phases retained. OTHER MLB/KBO and training omitted. Unsupported NPB rotation heads excluded from policy selection.")
    (output/"snapshot-manifest.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, args.output)))
