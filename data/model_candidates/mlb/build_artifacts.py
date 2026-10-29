"""Offline export of already-frozen coefficients and necessary prior columns.

Usage: python data/model_candidates/mlb/build_artifacts.py --source <research/mlb>
No training, fitting, candidate selection, network or DB. Writes only beside this
script. The source experiment is read-only; its import-time pipeline is not run.
"""
import argparse
import ast
from collections import Counter
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from mlb_frozen_candidate import BAT_FIELDS, PIT_FIELDS, VERSION, canonical_hash


def rows(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def normalized_sha(path):
    data = path.read_bytes()
    if path.suffix != ".gz":
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                               allow_nan=False) + "\n", encoding="utf8", newline="\n")


def export(source):
    out = Path(__file__).resolve().parent
    script = source.parents[2] / "scripts/mlb_lineup_nonlinear.py"
    # Compile ONLY the original two table-loading helpers. Never import or execute
    # training, evaluation, NumPy or the research collector's network code.
    tree = ast.parse(script.read_text(encoding="utf8"))
    helpers = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("number", "load_tables")]
    assert len(helpers) == 2
    namespace = dict(math=math, json=json, gzip=gzip)
    exec(compile(ast.Module(body=helpers, type_ignores=[]), str(script), "exec"), namespace)
    tables = namespace["load_tables"](source)
    snapshots = rows(source / "sources/mlb-snapshots.jsonl.gz")
    external = rows(source / "external-snapshots.jsonl.gz")
    frozen = json.loads((source / "frozen-selection.json").read_text(encoding="utf8"))
    assert frozen["heads"]["primary"] == "linear:l2=10"
    artifacts = {}
    lineage = {"research_source_sha256": normalized_sha(script),
               "frozen_selection_sha256": normalized_sha(source / "frozen-selection.json"),
               "source_manifest_sha256": normalized_sha(source / "sources/manifest.json"),
               "protocol_commit": frozen["protocol_commit"]}

    def artifact(name, payload):
        envelope = dict(payload=payload, sha256=canonical_hash(payload))
        write_json(out / name, envelope)
        artifacts[name] = envelope["sha256"]

    models = {}
    for stage, roles in [("before2025", ("train",)), ("before2026", ("train", "validation"))]:
        model = dict(frozen["models"]["linear:l2=10"][stage])
        training = [r for r in snapshots if r.get("accepted") and r["role"] in roles]
        assert len(training) == model["train_n"]
        model["training_cutoff_at"] = max(r["start_at"] for r in training)
        model["training_game_ids_hash"] = canonical_hash(sorted(r["gamePk"] for r in training))
        models[stage] = model
    artifact("model.json", dict(version=VERSION, candidate="linear:l2=10", models=models, lineage=lineage))
    for season in (2024, 2025):
        slim = {}
        source_hashes = {}
        for kind, group, fields in [("batter", "hitting", BAT_FIELDS), ("pitcher", "pitching", PIT_FIELDS)]:
            t = tables[season, kind]
            slim[kind] = dict(season=season, means=t["means"],
                              stats={str(pid): {k: s[k] for k in fields if k in s} for pid, s in t["stats"].items()},
                              savant={str(pid): {k: s[k] for k in ("pa", "est_woba", "est_slg", "xera") if k in s}
                                      for pid, s in t["savant"].items()})
            for key in ("stats_source", "savant_source"):
                source_hashes[t[key]] = normalized_sha(source / t[key])
        artifact(f"priors-{season}.json", dict(version=VERSION, season=season, tables=slim, sources=source_hashes,
                 time_scope="previous_completed_season_retrospectively_downloaded_not_point_in_time_capture"))

    predictions = rows(source / "predictions.jsonl")
    inputs = {r["gamePk"]: r for r in rows(source / "inputs.jsonl.gz")}
    snaps = {r["gamePk"]: r for r in snapshots + external}
    external_features = {r["gamePk"]: r for r in rows(source / "sources/mlb-external-features.jsonl")}
    fixtures, excluded = [], []
    for prediction in predictions:
        if not prediction["announced"]:
            excluded.append(dict(gamePk=prediction["gamePk"], reason=prediction["rejection_reason"]))
            continue
        original = snaps[prediction["gamePk"]]
        names = external_features.get(original["gamePk"], original)
        snap = {k: original[k] for k in ("gamePk", "start_at", "snapshot_at", "cutoff_at", "collected_at",
                                         "status", "source_kind", "url", "response_sha256", "accepted")}
        snap.update(home=names["home"], away=names["away"], sides={})
        if "feed_start_at" in original:
            snap["feed_start_at"] = original["feed_start_at"]
        for side in ("home", "away"):
            s = original["sides"][side]
            snap["sides"][side] = dict(lineup=[dict(id=p["id"], batting={k: p["batting"][k] for k in BAT_FIELDS})
                                                      for p in s["lineup"]],
                                       starter=dict(id=s["starter"]["id"], pitching={
                                           k: s["starter"]["pitching"][k] for k in PIT_FIELDS}))
        decision_at = prediction["quote_decision_at"] or original["cutoff_at"]
        # old116 does not have a verified quote receipt timestamp. Explicitly
        # marked fixture evidence permits math/settlement parity, not live ROI.
        odds = dict(home=prediction["prices"][0], away=prediction["prices"][1],
                    observed_at=None, time_scope="archived_unverified_time",
                    source="archived_research_prices_not_verified_receipt",
                    snapshot_id=f"research-mlb-{prediction['gamePk']}")
        fixtures.append(dict(snapshot=snap, decision_at=decision_at,
                    matchup={k: snap[k] for k in ("gamePk", "home", "away")} | {"league": "MLB"}, odds=odds,
                    archive_evidence="accepted_S_or_P_no_pitch;old_feed_start_and_quote_time_may_be_unverified",
                    expected=dict(features=inputs[original["gamePk"]]["features"][:23],
                                  probability_home=prediction["probabilities"]["primary"],
                                  model_stage=prediction["model_stage"]),
                    labels=dict(cohort=prediction["cohort"], date=prediction["date"],
                                hg=prediction["hg"], ag=prediction["ag"], baseline_kind=prediction["baseline_kind"],
                                probabilities={k: prediction["probabilities"][k] for k in ("baseline", "market_only")},
                                ledger={k: prediction["ledger"][k] for k in ("primary", "baseline", "market_only")})))
    assert Counter(f["labels"]["cohort"] for f in fixtures) == {"old116": 116, "external230": 194}
    raw = ("\n".join(json.dumps(f, ensure_ascii=False, separators=(",", ":")) for f in fixtures) + "\n").encode()
    (out / "parity-fixtures.jsonl.gz").write_bytes(gzip.compress(raw, mtime=0))
    write_json(out / "manifest.json", dict(version=VERSION, artifacts=artifacts, lineage=lineage,
        fixture_sha256=normalized_sha(out / "parity-fixtures.jsonl.gz"), accepted=len(fixtures),
        excluded=excluded, source_files={name: normalized_sha(source / name) for name in (
            "inputs.jsonl.gz", "predictions.jsonl", "external-snapshots.jsonl.gz", "sources/mlb-snapshots.jsonl.gz")}))
    print(json.dumps(dict(artifacts=artifacts, accepted=len(fixtures), excluded=len(excluded),
                          bytes={p.name: p.stat().st_size for p in out.glob("*.json*")})))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    export(parser.parse_args().source)
