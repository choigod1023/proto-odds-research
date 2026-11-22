"""Independent exact-arithmetic audit of a frozen selection study export."""
import argparse
from decimal import Decimal
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path


def verify(export, folder):
    blob = export.read_bytes()
    summary = json.loads((folder/"summary.json").read_text(encoding="utf8"))
    if hashlib.sha256(blob).hexdigest() != summary["source"]["sha256"]:
        raise ValueError("Source receipt mismatch")
    raw = [json.loads(line) for line in gzip.decompress(blob).decode("utf8").splitlines()]
    predictions = {r["snapshot_id"]: r for r in raw if r["record_type"] == "prediction"}
    outcomes = {}
    for record in raw:
        if record["record_type"] == "settlement" and record["ledger_sequence"] > outcomes.get(record["snapshot_id"], {}).get("ledger_sequence", -1):
            outcomes[record["snapshot_id"]] = record
    selections_blob = (folder/"selections.jsonl.gz").read_bytes()
    rows = [json.loads(line) for line in gzip.decompress(selections_blob).decode("utf8").splitlines()]
    for row in rows:
        prediction = predictions[row["snapshot_id"]]
        payload, options = prediction["predictions"], prediction["features"]["options"]
        allowed = {(payload["selection_id"], payload["offer_id"])}
        allowed |= {(item["selection_id"], item["offer_id"])
                    for item in (payload.get("market_recommendations") or {}).get("items") or []
                    if item.get("automatic_recommendation") is True and item.get("candidate_only") is not True}
        ranked = []
        for option in options:
            if (option["selection_id"], option["offer_id"]) not in allowed:
                continue
            group = [peer for peer in options if (peer["market"], peer.get("line"), peer.get("label") or "") ==
                     (option["market"], option.get("line"), option.get("label") or "")]
            probability = (1/Fraction(str(option["odds"]))) / sum(1/Fraction(str(peer["odds"])) for peer in group)
            ranked.append((option, probability))
        chosen = min(ranked, key=lambda x: (-x[1], -(x[1]*Fraction(str(x[0]["odds"]))-1), x[0]["selection_id"]))[0]
        expected = row["choices"]["probability_first"]
        if (chosen["selection_id"], chosen["offer_id"], chosen["odds"]) != (expected["selection_id"], expected["offer_id"], expected["odds"]):
            raise ValueError("Exact rational selector disagrees with saved alternative")
        outcome = (outcomes.get(row["snapshot_id"]) or {}).get("outcome") or {}
        for choice in row["choices"].values():
            if choice["grade"] is None:
                continue
            if choice["selection_id"] == payload["selection_id"]:
                result = outcome.get("result")
                if outcome.get("selection_id") != choice["selection_id"]:
                    raise ValueError("Wrong original primary grade")
            else:
                original = (outcome.get("market_recommendation_results") or {}).get(choice["offer_id"]) or {}
                if any(original.get(k) != choice.get(k) for k in ("selection_id", "offer_id", "market", "line", "settlement_rule")):
                    raise ValueError("Wrong original market grade")
                if Decimal(str(original["odds"])) != Decimal(str(choice["odds"])):
                    raise ValueError("Wrong original market price")
                result = original.get("result")
            net = Decimal(str(choice["odds"]))-1 if result == "hit" else Decimal(-1) if result == "miss" else Decimal(0) if result == "void" else None
            if choice["grade"]["result"] != result or Decimal(choice["grade"]["net_units"]) != net:
                raise ValueError("Study grade differs from original outcome at the frozen price")
    for sport, rules in summary["by_sport"].items():
        for rule, expected in rules.items():
            common = [row["choices"][rule] for row in rows if row["sport"] == sport and
                      all(row["choices"][name]["grade"] is not None for name in rules)]
            wins = sum(choice["grade"]["result"] == "hit" for choice in common)
            net = sum((Decimal(choice["grade"]["net_units"]) for choice in common), Decimal(0))
            if wins != expected["hits"] or net != Decimal(expected["net_units"]):
                raise ValueError("Independent sport accounting mismatch")
    return dict(source_sha256=summary["source"]["sha256"],
                selections_sha256=hashlib.sha256(selections_blob).hexdigest(),
                exact_rational_selection_matches=len(rows), sport_profit_reconciliation=True,
                current_rule_replay_matches=summary["replay_matches"],
                full_chain_verified=False, operational_policy_changed=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    result = verify(args.export, args.folder)
    (args.folder/"verification.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf8")
    print(json.dumps(result))
