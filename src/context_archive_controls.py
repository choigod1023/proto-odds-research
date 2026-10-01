"""Build reproducible score-form controls, not new xG/pitch-level evidence.

Archived prices have unknown observation times. Results become reconstructed
research features only 24 hours after kickoff. No production database access.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
LEAGUES = ("KBO", "NPB", "MLB", "EPL", "라리가", "세리에A", "분데스리", "프리그1", "K리그1", "J1리그")


def read_games(path):
    groups = defaultdict(list)
    audit = Counter()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            audit["raw_rows"] += 1
            if r["league"] not in LEAGUES or r["market_family"] not in ("승패", "승무패"):
                continue
            if r["is_void"].lower() not in ("false", "0"):
                continue
            try:
                h = re.fullmatch(r"(.+?)\s+(\d+)", r["home"].strip())
                a = re.fullmatch(r"(\d+)\s+(.+)", r["away"].strip())
                if not h or not a:
                    raise ValueError("no_final_score")
                dt = re.search(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})", r["date_text"])
                mm, dd, hh, minute = map(int, dt.groups())
                year = int(r["year"]) - int(int(r["round"]) == 1 and mm == 12)
                kickoff = datetime(year, mm, dd, hh, minute, tzinfo=KST)
                odds = list(map(float, r["odds"].split(",")))
                n = int(r["n_way"])
                if n not in (2, 3) or len(odds) != n or any(not math.isfinite(x) or x <= 1 for x in odds):
                    raise ValueError("invalid_market")
                hs, away = int(h[2]), int(a[1])
                expected = "홈승" if hs > away else "홈패" if hs < away else "무승부"
                if r["result"] != expected:
                    raise ValueError("settlement_score_mismatch")
                key = (r["league"], h[1], a[2], kickoff.isoformat())
                groups[key].append({"event_id": "|".join(key), "league": r["league"],
                    "home": h[1], "away": a[2], "kickoff": kickoff.isoformat(),
                    "home_score": hs, "away_score": away, "odds": odds,
                    "source_order": (int(r["year"]), int(r["round"]), int(r["game_no"]))})
            except (ValueError, TypeError, AttributeError):
                audit["invalid_rows"] += 1
    result = []
    for group in groups.values():
        if len({(r["home_score"], r["away_score"]) for r in group}) != 1:
            audit["conflicting_events"] += 1
            continue
        # Earliest archive offer is deterministic, NOT a pregame timestamp claim.
        row = min(group, key=lambda r: r["source_order"])
        audit["duplicate_offer_rows"] += len(group)-1
        audit["events_with_multiple_prices"] += int(len({tuple(r["odds"]) for r in group}) > 1)
        result.append(row)
    return sorted(result, key=lambda r: r["kickoff"]), dict(audit)


def build_controls(games):
    history = defaultdict(list)
    rows = []
    audit = Counter()
    for r in games:
        stamp = datetime.fromisoformat(r["kickoff"])
        cutoff = stamp - timedelta(minutes=30)
        summaries = []
        used_times = []
        for team in (r["home"], r["away"]):
            previous = [(at, gf, ga) for at, gf, ga in history[(r["league"], team)]
                        if at + timedelta(hours=24) <= cutoff][-20:]
            if len(previous) < 5:
                summaries.append(None)
                continue
            gf, ga = [x[1] for x in previous], [x[2] for x in previous]
            summaries.append([statistics.mean(gf), statistics.mean(ga),
                              statistics.pvariance(gf), statistics.pvariance(ga),
                              statistics.mean([float(a>b) + .5*float(a==b) for a,b in zip(gf,ga)])])
            used_times.extend(at + timedelta(hours=24) for at, _, _ in previous)
        hs, aw = r["home_score"], r["away_score"]
        if len(r["odds"]) == 2 and hs == aw:
            audit["two_way_draws_not_graded"] += 1
        elif all(x is not None for x in summaries):
            h, a = summaries
            features = [h[0]-a[1], a[0]-h[1], h[2]-a[2], h[3]-a[3], h[4]-a[4]]
            rows.append({"event_id": r["event_id"], "league": r["league"], "kickoff": r["kickoff"],
                         "feature_as_of": max(used_times).isoformat(), "features": features,
                         "target": (0 if hs > aw else (1 if hs == aw else 2)) if len(r["odds"])==3 else int(hs < aw),
                         "odds": r["odds"], "odds_as_of": None,
                         "feature_provenance": "reconstructed_prior_scores_24h_lag_not_observed_history"})
        else:
            audit["insufficient_form"] += 1
        history[(r["league"], r["home"])].append((stamp, hs, aw))
        history[(r["league"], r["away"])].append((stamp, aw, hs))
    return rows, dict(audit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.games.resolve() in (args.output.resolve(), args.output.with_suffix('.manifest.json').resolve()):
        parser.error('outputs must not overwrite source')
    games, audit = read_games(args.games)
    rows, form_audit = build_controls(games)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    manifest = {"input_sha256": hashlib.sha256(args.games.read_bytes()).hexdigest(),
                "audit": audit, "form_audit": form_audit, "league_counts": dict(Counter(r["league"] for r in rows)),
                "limits": ["score-form replication control, not novel xG or pitcher experiment",
                           "unknown archived price observation times; no T30 or executable ROI claim"]}
    args.output.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
