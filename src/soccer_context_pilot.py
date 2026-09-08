"""Offline prospective xG adapter. No network, DB access, fitting or production imports.

Writes a JSON array for a generic H/D/A evaluator and a separate provenance audit.
snapshot_at is a batch START: availability is conservatively snapshot_at + 24h.
Both feature availability and timestamped odds must be <= kickoff - 30 minutes.
This lag is an assumption, not proof of the collector's actual completion time.
Only explicit result labels determine targets; scores never infer labels or aliases.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re

UTC = timezone.utc
KST = timezone(timedelta(hours=9))
TARGETS = {"홈승": 0, "무승부": 1, "홈패": 2}
FEATURE_NAMES = ["home_xg_home", "home_xga_home", "away_xg_away", "away_xga_away"]
# Deliberate provider-to-archive names. No fuzzy matching, score matching or suffix
# normalization (which would merge Suwon FC with Suwon Samsung).
K1_ALIASES = {
    "Anyang": "FC안양", "Bucheon 1995": "부천FC",
    "Daejeon Citizen": "대전하나", "FC Seoul": "FC서울",
    "Gangwon": "강원FC", "Gwangju": "광주FC",
    "Incheon United": "인천유나", "Jeju United": "제주SKFC",
    "Jeonbuk Motors": "전북현대", "Pohang Steelers": "포항스틸",
    "Sangju Sangmu": "김천상무", "Ulsan": "울산HDFC",
}


def timestamp(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timestamp requires timezone")
    return dt.astimezone(UTC)


def fixture(row):
    """Strip score display tokens solely to recover archive team names."""
    h = re.sub(r"\s+-?\d+\s*$", "", row["home"]).strip()
    a = re.sub(r"^\s*-?\d+\s+", "", row["away"]).strip()
    m = re.fullmatch(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})", row["date_text"].strip())
    if m is None:
        raise ValueError("Missing precise kickoff")
    month, day, hour, minute = map(int, m.groups())
    year = int(row["year"]) - int(int(row["round"]) == 1 and month == 12)
    kick = datetime(year, month, day, hour, minute, tzinfo=KST).astimezone(UTC)
    return kick, h, a


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


def three_way(row):
    return (row.get("sport") == "sc" and row.get("league") == "K리그1"
            and row.get("market_family") == "승무패" and row.get("n_way") == "3")


def valid_odds(value):
    odds = [float(v) for v in value.split(",")]
    if len(odds) != 3 or not all(math.isfinite(v) and v > 1 for v in odds):
        raise ValueError("Invalid H/D/A odds")
    return odds


def source_info(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path.resolve()), "bytes": path.stat().st_size,
            "sha256": digest.hexdigest()}


def build_rows(games_path, xg_path, odds_paths, *, since, until):
    """Return generic rows + audit; inputs are read-only, outputs not written here."""
    games_path, xg_path = Path(games_path), Path(xg_path)
    odds_paths = sorted(set(map(Path, odds_paths)))
    counts = Counter()
    audit = {
        "class_order": ["H", "D", "A"], "feature_names": FEATURE_NAMES,
        "policy": {"decision_minutes_before_kickoff": 30,
                   "maximum_odds_age_minutes": 35,
                   "snapshot_availability_lag_hours": 24, "maximum_snapshot_age_days": 7,
                   "target_source": "explicit archive result label only",
                   "odds_source": "timestamped snapshots only; no archive-odds fallback"},
        "requested_dates_kst": [since.isoformat(), until.isoformat()],
        "team_aliases": K1_ALIASES, "missing_real_data": [], "unknown_maps": [],
        "malformed_xg_lines": [], "sources": [], "excluded_events": [],
        "provenance": [], "model_fitted": False,
        "limitations": ["K1 only; not independent cross-league validation",
                        "Aliases are explicit, not provider-ID verified",
                        "24h batch-start lag is conservative, not a measured completion timestamp",
                        "Season-average xG snapshots are not per-match historical xG"],
    }
    for path in [games_path, xg_path, *odds_paths]:
        if not path.is_file():
            audit["missing_real_data"].append(str(path))
        else:
            audit["sources"].append(source_info(path))
    if not odds_paths:
        audit["missing_real_data"].append("No timestamped odds CSVs supplied")
    if not games_path.is_file() or not xg_path.is_file():
        audit.update(status="missing_real_data", sufficient_for_fit=False, counts={"emitted": 0})
        return [], audit
    snapshots = defaultdict(list)
    unknown = Counter()
    coverage = defaultdict(list)
    with xg_path.open(encoding="utf-8-sig") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    raise ValueError("Expected object")
            except (ValueError, TypeError):
                audit["malformed_xg_lines"].append(line_no)
                continue
            counts["valid_json_snapshots"] += 1
            try:
                at = timestamp(raw["snapshot_at"])
                league = str(raw["league"])
                coverage[league].append(at)
                if league != "kleague1":
                    counts["unsupported_league_snapshots"] += 1
                    continue
                home, away = raw["home_team"], raw["away_team"]
                if not all(isinstance(v, str) and v.strip() for v in (home, away)):
                    raise ValueError('Invalid team identity')
                if home not in K1_ALIASES or away not in K1_ALIASES:
                    for name in (home, away):
                        if name not in K1_ALIASES:
                            unknown[name] += 1
                    counts["unknown_map_snapshots"] += 1
                    continue
                vals = [raw["home"]["xg_home"], raw["home"]["xga_home"],
                        raw["away"]["xg_away"], raw["away"]["xga_away"]]
                if any(v is None or isinstance(v, bool) for v in vals):
                    raise ValueError("Missing metric")
                features = list(map(float, vals))
                if not all(math.isfinite(v) and v >= 0 for v in features):
                    raise ValueError("Nonfinite or negative metric")
                snapshots[K1_ALIASES[home], K1_ALIASES[away]].append(
                    {"at": at, "available": at + timedelta(hours=24),
                     "features": features, "line": line_no, "url": raw.get("url")})
            except (KeyError, ValueError, TypeError, OverflowError):
                counts["invalid_snapshot_fields"] += 1
    audit["snapshot_coverage"] = {
        k: {"count": len(v), "first": min(v).isoformat(), "last": max(v).isoformat()}
        for k, v in sorted(coverage.items())}
    events = {}
    conflicts = set()
    for row in csv_rows(games_path):
        counts["archive_rows"] += 1
        if any(not isinstance(row.get(k), str) for k in ('home','away','is_void','date_text','year','round')):
            counts['malformed_archive_rows'] += 1
            continue
        if not three_way(row):
            continue
        if row.get("is_void", "").lower() not in ("false", "0"):
            counts["void_or_unknown_void_state"] += 1
            continue
        if row.get("result") not in TARGETS:
            counts["unsettled_or_unknown_result"] += 1
            continue
        try:
            key = fixture(row)
        except (KeyError, ValueError):
            counts["invalid_archive_fixture"] += 1
            continue
        if not since <= key[0].astimezone(KST).date() <= until:
            continue
        if any(name not in K1_ALIASES.values() for name in key[1:]):
            for name in key[1:]:
                if name not in K1_ALIASES.values():
                    unknown[name] += 1
            counts["unknown_map_archive"] += 1
            continue
        target = TARGETS[row["result"]]
        if key in events:
            counts["duplicate_archive_fixture"] += 1
            if events[key] != target:
                conflicts.add(key)
        events[key] = target
    for key in conflicts:
        del events[key]
        audit["excluded_events"].append({"kickoff": key[0].isoformat(),
                                         "home": key[1], "away": key[2],
                                         "reason": "conflicting_result_labels"})
    counts["conflicting_result_fixtures"] = len(conflicts)
    counts["settled_fixtures_in_window"] = len(events)
    markets = {}
    latest_closed = {}
    ambiguous_markets = set()
    for path in odds_paths:
        if not path.is_file():
            continue
        for line_no, row in enumerate(csv_rows(path), 2):
            if not three_way(row):
                continue
            counts["k1_odds_observations"] += 1
            try:
                key = fixture(row)
                if key not in events:
                    continue
                at = timestamp(row["ts"])
                if at > key[0] - timedelta(minutes=30):
                    counts["odds_after_t30"] += 1
                    continue
                if key[0] - timedelta(minutes=30) - at > timedelta(minutes=35):
                    counts['stale_odds_before_t30'] += 1
                    continue
                # A recorded final/cancelled market is not an executable pregame quote.
                if row.get("result") not in ("", "경기전"):
                    counts["odds_not_pregame_state"] += 1
                    latest_closed[key] = max(at, latest_closed.get(key, at))
                    continue
                odds = valid_odds(row["odds"])
            except (KeyError, ValueError, TypeError):
                counts["invalid_odds_observation"] += 1
                continue
            old = markets.get(key)
            if old is None or at > old["at"]:
                markets[key] = {"at": at, "odds": odds, "path": str(path.resolve()), "line": line_no}
                ambiguous_markets.discard(key)
            elif at == old["at"] and odds != old["odds"]:
                ambiguous_markets.add(key)
    rows = []
    for key, target in sorted(events.items()):
        kick, home, away = key
        decision = kick - timedelta(minutes=30)
        candidates = [s for s in snapshots[home, away]
                      if s["available"] <= decision and decision - s["at"] <= timedelta(days=7)]
        reason = None
        if not candidates:
            reason = "missing_xg_with_24h_lag_by_t30"
        elif key not in markets:
            reason = "missing_timestamped_pregame_odds_by_t30"
        elif key in latest_closed and latest_closed[key] >= markets[key]['at']:
            reason = 'latest_market_closed'
        elif key in ambiguous_markets:
            reason = "conflicting_latest_odds"
        if reason is None:
            latest = max(s["at"] for s in candidates)
            chosen = [s for s in candidates if s["at"] == latest]
            if len({tuple(s["features"]) for s in chosen}) > 1:
                reason = "conflicting_latest_xg"
        if reason:
            counts[reason] += 1
            audit["excluded_events"].append({"kickoff": kick.isoformat(), "home": home,
                                             "away": away, "reason": reason})
            continue
        snap, market = chosen[0], markets[key]
        event_id = "kleague1|" + "|".join([kick.isoformat(), home, away])
        rows.append({"event_id": event_id, "league": "kleague1", "kickoff": kick.isoformat(),
                     "feature_availability_verified": False,
                     "feature_provenance": "batch_start_plus_assumed_24h_lag",
                     "feature_as_of": snap["available"].isoformat(), "features": snap["features"],
                     "target": target, "odds": market["odds"], "odds_as_of": market["at"].isoformat()})
        audit["provenance"].append({"event_id": event_id, "decision_at": decision.isoformat(),
                                    "snapshot_at": snap["at"].isoformat(), "xg_line": snap["line"],
                                    "xg_url": snap["url"], "odds_path": market["path"],
                                    "odds_line": market["line"]})
    audit["unknown_maps"] = dict(sorted(unknown.items()))
    counts["emitted"] = len(rows)
    audit["counts"] = dict(sorted(counts.items()))
    audit["outcomes"] = dict(Counter(["H", "D", "A"][r["target"]] for r in rows))
    audit["sufficient_for_fit"] = False
    audit["fit_reason"] = "Adapter never fits; downstream evaluator must gate chronological splits and class support"
    audit["insufficient_sample"] = len(rows) < 100
    audit["status"] = "insufficient_sample" if len(rows) < 100 else "requires_downstream_validation"
    audit["fixed_probe"] = fixed_probe(rows)
    return rows, audit


def fixed_probe(rows):
    """Descriptive, zero-fit sanity probe; never claims validation or selects bets."""
    if not rows:
        return {"n": 0, "status": "missing_real_data"}
    scores = defaultdict(list)
    for row in rows:
        hf, ha, af, aa = row["features"]
        rates = (hf/2+aa/2, af/2+ha/2)
        if any(not math.isfinite(v) or not 0 <= v <= 50 for v in rates):
            return {'n': len(rows), 'status': 'poisson_numerical_range_exceeded'}
        def pmf(rate):
            p = [math.exp(-rate)]
            for k in range(1, 100):
                p.append(p[-1] * rate / k)
            return p
        h, a = pmf(rates[0]), pmf(rates[1])
        xg = [0., 0., 0.]
        for i, hi in enumerate(h):
            for j, aj in enumerate(a):
                xg[0 if i > j else 1 if i == j else 2] += hi * aj
        total = sum(xg)
        if not math.isfinite(total) or total <= 0:
            return {"n": len(rows), "status": "poisson_numerical_range_exceeded"}
        xg = [v / total for v in xg]
        inv = [1 / o for o in row["odds"]]
        market = [v / sum(inv) for v in inv]
        blend = [(p + q) / 2 for p, q in zip(xg, market)]
        for name, pred in (("market", market), ("xg_poisson", xg), ("fixed_half_blend", blend)):
            scores[name].append((sum((v - int(i == row["target"])) ** 2 for i, v in enumerate(pred)),
                                 -math.log(max(1e-15, pred[row["target"]])),
                                 int(max(range(3), key=lambda i: pred[i]) == row["target"])))
    return {"n": len(rows), "status": "descriptive_only_no_fit_no_uplift_claim",
            "metrics": {name: dict(zip(["brier_sum", "log_loss", "accuracy"],
                                       [sum(v) / len(v) for v in zip(*values)]))
                        for name, values in scores.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=Path, required=True)
    parser.add_argument("--xg", type=Path, required=True)
    parser.add_argument("--odds-dir", type=Path, required=True)
    parser.add_argument("--since", type=date.fromisoformat, required=True)
    parser.add_argument("--until", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args()
    if args.since > args.until:
        parser.error("since must be <= until")
    paths = sorted(args.odds_dir.glob("odds_timeseries_*.csv"))
    # Retain the preceding seven UTC file dates for pregame observations.
    paths = [p for p in paths if (args.since - timedelta(days=7)).strftime("%Y%m%d")
             <= p.stem[-8:] <= args.until.strftime("%Y%m%d")]
    protected = {p.resolve() for p in [args.games, args.xg, *paths]}
    if args.output.resolve() == args.audit.resolve() or any(
            p.resolve() in protected for p in (args.output, args.audit)):
        parser.error("Output paths must be distinct from each other and all source files")
    rows, audit = build_rows(args.games, args.xg, paths, since=args.since, until=args.until)
    for path, payload in ((args.output, rows), (args.audit, audit)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                        encoding="utf-8")
    print(json.dumps({"rows": len(rows), "status": audit["status"],
                      "output": str(args.output.resolve()), "audit": str(args.audit.resolve())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
