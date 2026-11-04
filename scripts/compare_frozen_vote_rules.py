"""Offline exact-cohort comparison. No production imports, network or writes to DB."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ai_decision import event_id, selection_id, offer_id
from prediction_ledger import PredictionLedger
from research_vote_rankings import evaluate, grade_selected, instant, select_rules


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def dump(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def build(args):
    raw = args.ledger.read_bytes()
    if args.ledger.suffix == ".gz":
        raw = gzip.decompress(raw)
    cohort_raw = args.cohort.read_bytes()
    cohort = json.loads(cohort_raw)
    if cohort["ledger_sha256"] != sha(raw) or cohort["policy"] != "tipster-vote-v1":
        raise ValueError("cohort is not bound to this actual ledger")
    records = [json.loads(line) for line in raw.splitlines() if line]
    verifier = object.__new__(PredictionLedger)
    verifier._database = SimpleNamespace(prediction_records=lambda: records)
    verifier._read_verified_storage()
    latest = {}
    for record in records:
        if record["record_type"] == "prediction":
            key = (instant(record["as_of"]), record["ledger_sequence"])
            previous = latest.get(record["event_id"])
            if previous is None or key > (instant(previous["as_of"]), previous["ledger_sequence"]):
                latest[record["event_id"]] = record
    declarations = cohort["cohorts"]["actual_tipster_vote"]["records"]
    if len(declarations) != 14 or len({row["event_id"] for row in declarations}) != 14:
        raise ValueError("this fixed retrospective study requires the original14 fixtures")
    cross_raw = args.crosscheck.read_bytes()
    checks = {row["snapshot_id"]: row for row in json.loads(cross_raw)["entries"]}
    manifest_raw = args.official_manifest.read_bytes()
    manifest = json.loads(manifest_raw)
    docs = {}
    for item in manifest:
        if not re.fullmatch(r"G101,\d{6}\.json", item["file"]):
            raise ValueError("invalid evidence filename")
        docs[item["sha256"]] = item
    features, labels = [], []
    for declared in declarations:
        pred = latest[declared["event_id"]]
        if pred["snapshot_id"] != declared["snapshot_id"]:
            raise ValueError("cohort revision is no longer the declared frozen revision")
        f, pick = pred["features"], pred["predictions"]
        fixture, vote = f["fixture"], f["tipster_hybrid"]
        if (event_id(fixture) != pred["event_id"]
                or pred["model"]["selection_policy"] != "tipster-vote-v1"
                or vote["version"] != "tipster-vote-v1" or vote["status"] != "applied"
                or pick["action"] != "market_reference"):
            raise ValueError("not an authenticated actual vote selection")
        date = instant(pred["kickoff"]).astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
        if not "2026-10-29" <= date <= "2026-11-03":
            raise ValueError("outside fixed same-period window")
        check = checks[pred["snapshot_id"]]
        evidence = check["source"]["official_source"]
        if check["source"]["manifest_sha256"] != sha(manifest_raw):
            raise ValueError("official manifest changed")
        item = docs[evidence["evidence_sha256"]]
        dom_raw = (args.official_dom / item["file"]).read_bytes()
        if sha(dom_raw) != item["sha256"]:
            raise ValueError("official DOM bytes changed")
        dom = json.loads(dom_raw)
        url = urlsplit(dom["url"])
        query = parse_qs(url.query)
        expected_round = f"G101,{str(fixture['year'])[2:]}{int(fixture['round']):04d}"
        if (url.scheme != "https" or url.netloc != "www.betman.co.kr"
                or url.path != "/main/mainPage/gamebuy/closedGameSlipIFR.do"
                or query.get("gmId") != ["G101"]
                or query.get("gmTs") != [expected_round.split(",")[1]]
                or dom["selectedRound"] != [{"text": dom["selectedRound"][0]["text"], "value": expected_round}]
                or dom["url"] != evidence["url"] or item["url"] != dom["url"]
                or instant(dom["received_at"]) != instant(evidence["received_at"])):
            raise ValueError("official selected round, URL or receipt differs")
        index = evidence["row_index"]
        if type(index) is not int or not 0 <= index < len(dom["rows"]):
            raise ValueError("invalid official row index")
        row = dom["rows"][index]
        for key in ("game_no", "game_name", "kickoff_text", "label", "league", "market_text", "result_text", "result_action", "rowname"):
            if row[key] != evidence[key]:
                raise ValueError("official row changed: " + key)
        sport_name = {"sc": "축구", "bs": "야구", "bk": "농구", "vl": "배구"}[fixture["sport"]]
        if (row["game_name"] != fixture["home"] + ":" + fixture["away"]
                or row["league"] != fixture["league"] or row["market_text"] != sport_name + " " + pick["market"]
                or row["label"] != pick["label"]):
            raise ValueError("different physical fixture or product")
        official_kickoff = instant(pred["kickoff"]).astimezone(ZoneInfo("Asia/Seoul"))
        if (official_kickoff.strftime("%Y%m%d%H%M%S") != row["kickoff_text"]
                or not row["rowname"].endswith("_" + str(int(official_kickoff.timestamp()*1000)))):
            raise ValueError("official kickoff or epoch differs")
        options = [option for option in f["options"]
                   if option["market"] == pick["market"] and option["label"] == pick["label"]]
        if len(row["options"]) != len(options):
            raise ValueError("different official n-way")
        for option in options:
            named = dict(option, **{"선택": option["selection"], "게임번호": row["game_no"]})
            if selection_id(fixture, named) != option["selection_id"] or offer_id(fixture, named) != option["offer_id"]:
                raise ValueError("frozen side/offer is not this original official product")
        feature = dict(snapshot_id=pred["snapshot_id"], event_id=pred["event_id"],
                       source_record_hash=pred["record_hash"], fixture=fixture, sport=fixture["sport"],
                       policy=pred["model"]["selection_policy"], selection_source="actual_tipster_vote",
                       kickoff=pred["kickoff"], as_of=pred["as_of"], captured_at=pred["captured_at"],
                       market_observed_at=pred["market_observed_at"], market=pick["market"], label=pick["label"],
                       original_selection=pick["selection"], original_selection_id=pick["selection_id"],
                       original_offer_id=pick["offer_id"], original_odds=pick["odds"],
                       vote_counts=vote["vote_counts"], evidence=vote["evidence"], options=options)
        label = dict(snapshot_id=pred["snapshot_id"], event_id=pred["event_id"],
                     kickoff=pred["kickoff"], market=pick["market"], label=pick["label"], n_way=len(options),
                     result_text=row["result_text"], result_action=row["result_action"],
                     official_source=evidence, manifest_sha256=sha(manifest_raw))
        select_rules(feature)
        baseline = grade_selected(feature, label, pick["selection"])
        if baseline["hit"] != (declared["result"] == "hit"):
            raise ValueError("original grade does not reproduce")
        features.append(feature)
        labels.append(label)
    args.output.mkdir(parents=True, exist_ok=True)
    contents = {"inputs.json": dump(features), "labels.json": dump(labels)}
    proof = dict(ledger_raw_sha256=sha(raw), full_ledger_chain_verified=True,
                 cohort_report_sha256=sha(cohort_raw), official_crosscheck_sha256=sha(cross_raw),
                 official_manifest_sha256=sha(manifest_raw), fixed_snapshots=len(features),
                 source_toolkit_pr="https://github.com/choigod1023/proto-odds-research/pull/558",
                 protocol_commit="5e4d1146", files={name: sha(body) for name, body in contents.items()},
                 protocol_sha256=sha((args.output / "protocol.json").read_bytes()),
                 note="Rendered official DOM, not raw HTTP bytes; frozen prices are recorded quotes, not independently proven execution prices.")
    for name, body in contents.items():
        (args.output / name).write_bytes(body)
    (args.output / "input-manifest.json").write_bytes(dump(proof))
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("build")
    for name in ("ledger", "cohort", "crosscheck", "official-manifest", "official-dom", "output"):
        capture.add_argument("--" + name, type=Path, required=True)
    run = sub.add_parser("evaluate")
    run.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build":
        print(json.dumps(build(args), ensure_ascii=False))
    else:
        directory = args.directory
        proof = json.loads((directory / "input-manifest.json").read_bytes())
        if sha((directory / "protocol.json").read_bytes()) != proof["protocol_sha256"]:
            raise ValueError("fixed protocol changed")
        for name, digest in proof["files"].items():
            if name not in {"inputs.json", "labels.json"} or sha((directory / name).read_bytes()) != digest:
                raise ValueError("study input changed")
        result = evaluate(json.loads((directory / "inputs.json").read_bytes()),
                          json.loads((directory / "labels.json").read_bytes()))
        result["input_manifest_sha256"] = sha((directory / "input-manifest.json").read_bytes())
        (directory / "results.json").write_bytes(dump(result))
        print(json.dumps({rule: {k: v for k, v in stats.items() if k not in {"rows", "by_sport"}}
                          for rule, stats in result["results"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
