"""Check service parity against an existing verified ledger; not a new holdout."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ai_decision import build_decision_snapshot, validate_decision_snapshot
from fixed_tipster_hybrid import TEAMS, KST


def run(ledger_path, vote_paths):
    data = json.loads(Path(ledger_path).read_text(encoding="utf-8"))
    ledger = data["expanded_ledger"]
    needed = {e["id"] for r in ledger for e in r["tipster_evidence"]}
    found = {}
    for path in vote_paths:
        with Path(path).open(encoding="utf-8-sig") as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("pick_id") in needed and row.get("event_type") == "first_observed":
                    found.setdefault(row["pick_id"], row)
    reverse = {v: k for k, v in TEAMS.items()}
    mismatches, missing = [], []
    profit, hits, checked = 0, 0, 0
    for r in ledger:
        ids = [e["id"] for e in r["tipster_evidence"]]
        if any(i not in found for i in ids):
            missing.append(r["id"])
            continue
        kickoff = datetime.fromisoformat(r["kickoff"]).astimezone(KST)
        game = dict(league="MLB", sport="bs", home=reverse[r["home"]], away=reverse[r["away"]],
                    year=kickoff.year, date=kickoff.strftime("%m.%d(요일) %H:%M"),
                    options=[dict(market="승패", label="", 선택=s, 배당=r["odds"][i],
                                  시장확률=r["model"][i], 게임번호="1") for i, s in enumerate(("홈", "원정"))])
        snapshot = build_decision_snapshot(game, as_of=r["at"], tipster_votes=[found[i] for i in ids])
        validate_decision_snapshot(snapshot)
        expected = max(range(2), key=lambda i: .9*r["model"][i]+.1*r["tipster"][i])
        selected = 0 if game["추천"]["선택"] == "홈" else 1
        checked += 1
        if selected != expected or snapshot.get("tipster_hybrid", {}).get("status") != "applied":
            mismatches.append(r["id"])
        hit = selected == r["y"]
        hits += hit
        profit += r["odds"][selected] * hit - 1
    result = dict(scope="Existing verified ledger replay, no new evidence of profitability",
                  checked=checked, missing_events=len(missing), mismatches=mismatches,
                  hits=hits, roi=profit/checked if checked else None)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if missing or mismatches else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger")
    parser.add_argument("votes", nargs="+")
    args = parser.parse_args()
    raise SystemExit(run(args.ledger, args.votes))
