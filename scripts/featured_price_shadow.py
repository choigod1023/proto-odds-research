"""Prospective comparison of featured picks using the collector's frozen ledger."""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import sqlite3
import time

SPORTS = {"sc", "bs", "bk", "vl"}
BASE = "preferred_150"
LOW = "strict_above_130"
ARMS = {"NBA": "probability_first", "KBO": "preferred_145"}
PICK_FIELDS = ("selection_id", "offer_id", "market", "line", "odds", "settlement_rule")
MARKETS = {
    "승패": {"홈", "원정"}, "승무패": {"홈", "무", "원정"},
    "핸디캡": {"핸디홈", "핸디원정"}, "언더오버": {"언더", "오버"},
}


def now():
    return datetime.now(timezone.utc)


def stamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("timezone missing")
    return parsed.astimezone(timezone.utc)


def terms(pick):
    return tuple(pick.get(key) for key in PICK_FIELDS)


def pick_key(pick):
    return -pick["probability"], -(pick["probability"] * pick["odds"] - 1), pick["selection_id"]


def candidates(row, cutoff):
    payload = row["predictions"]
    features = row["features"]
    options = features["options"]
    primary = dict(payload, settlement_rule="official_winner")
    items = payload.get("market_recommendations") or {}
    picks = [primary] if payload.get("action") == "market_reference" and payload.get("market") in {"승패", "승무패"} else []
    if items.get("version") == "multi-market-recommendations-v1":
        picks.extend(item for item in items.get("items", []) if item.get("automatic_recommendation") is True
                     and item.get("candidate_only") is not True)
    seen = set()
    results = []
    for pick in picks:
        ident = pick["selection_id"], pick["offer_id"]
        if not all(ident) or ident in seen:
            raise ValueError("invalid candidate identity")
        seen.add(ident)
        if pick["settlement_rule"] != "official_winner":
            if pick["market"] not in {"핸디캡", "언더오버"} or pick.get("period") not in {"full", "full_game"}:
                raise ValueError("unsupported line")
            if not pick.get("market_observed_at") or stamp(pick["market_observed_at"]) >= cutoff:
                raise ValueError("line price outside cutoff")
        group = pick["market"], pick.get("label") or "", pick.get("line")
        peers = [o for o in options if (o["market"], o.get("label") or "", o.get("line")) == group]
        exact = [o for o in options if (o.get("selection_id"), o.get("offer_id")) == ident]
        if len(exact) != 1 or any(exact[0].get(k) != pick.get(k) for k in ("market", "line", "selection", "odds")):
            raise ValueError("candidate differs from price snapshot")
        if (exact[0].get("label") or "") != group[1]:
            raise ValueError("candidate label mismatch")
        expected = MARKETS[group[0]]
        if group[0] == "핸디캡" and len(peers) == 3:
            expected = expected | {"핸디무"}
        if len(peers) != len(expected) or {o["selection"] for o in peers} != expected:
            raise ValueError("incomplete or ambiguous market")
        prices = [o["odds"] for o in peers]
        if any(type(p) not in (int, float) or not math.isfinite(p) or p <= 1 for p in prices):
            raise ValueError("invalid market prices")
        results.append({key: pick.get(key) for key in (*PICK_FIELDS, "selection", "label")}
                       | {"probability": 1 / (pick["odds"] * sum(1 / p for p in prices))})
    return results


def freeze(row, captured):
    payload = row["predictions"]
    saved = payload.get("featured_pick") or {}
    cutoff = stamp(row["kickoff"]) - timedelta(minutes=30)
    clocks = [stamp(row[k]) for k in ("as_of", "captured_at", "market_observed_at")]
    if max(*clocks, captured) >= cutoff or max(clocks) > captured:
        raise ValueError("outside pregame cutoff")
    if saved.get("version") != "featured-pregame-v1":
        raise ValueError("no saved operating pick")
    picks = candidates(row, cutoff)
    if not picks:
        raise ValueError("no eligible candidates")
    baseline = min(picks, key=lambda p: (p["odds"] < 1.50, *pick_key(p)))
    if terms(baseline) != terms(saved):
        raise ValueError("operating pick could not be reproduced")
    league = row["features"]["fixture"]["league"]
    arms = {BASE: baseline, LOW: min((p for p in picks if p["odds"] > 1.30), key=pick_key, default=None)}
    if league == "NBA":
        arms[ARMS[league]] = min(picks, key=pick_key)
    elif league == "KBO":
        arms[ARMS[league]] = min(picks, key=lambda p: (p["odds"] < 1.45, *pick_key(p)))
    return {"captured_at": captured.isoformat(), "source_captured_at": row["captured_at"],
            "market_observed_at": row["market_observed_at"], "cutoff": cutoff.isoformat(),
            "source_hash": row.get("record_hash"), "kickoff": row["kickoff"], "arms": arms}


def grade(pick, outcome):
    if pick is None:
        return None
    if pick["settlement_rule"] == "official_winner":
        if outcome.get("selection_id") != pick["selection_id"]:
            return None
        return outcome.get("result") if outcome.get("result") in {"hit", "miss", "void"} else None
    official = (outcome.get("market_recommendation_results") or {}).get(pick["offer_id"]) or {}
    if terms(official) != terms(pick) or official.get("result") not in {"hit", "miss", "void"}:
        return None
    return official["result"]


def database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS progress (
            singleton INTEGER PRIMARY KEY CHECK (singleton=1),
            sequence INTEGER NOT NULL, started_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL, sort_time TEXT NOT NULL,
            sport TEXT NOT NULL, league TEXT NOT NULL, frozen TEXT, reason TEXT, grades TEXT
        );
        CREATE INDEX IF NOT EXISTS events_snapshot ON events(snapshot_id);
    """)
    return conn


def process(source, state, captured):
    with state:
        # The write reservation serializes overlapping invocations on the research DB only.
        state.execute("BEGIN IMMEDIATE")
        cursor = state.execute("SELECT sequence FROM progress WHERE singleton=1").fetchone()
        if cursor is None:
            seq = source.execute("SELECT COALESCE(MAX(ledger_sequence), 0) FROM prediction_records").fetchone()[0]
            state.execute("INSERT INTO progress VALUES (1, ?, ?)", (max(0, seq - 512), captured.isoformat()))
            cursor = (max(0, seq - 512),)
        rows = source.execute(
            "SELECT ledger_sequence, record_type, snapshot_id, record_json FROM prediction_records "
            "WHERE ledger_sequence > ? ORDER BY ledger_sequence LIMIT 128", cursor
        )
        for sequence, kind, snapshot, raw in rows:
            if kind == "prediction":
                row = json.loads(raw)
                fixture = row.get("features", {}).get("fixture") or {}
                sport, league = fixture.get("sport"), fixture.get("league")
                if sport in SPORTS and league and captured < stamp(row["kickoff"]) - timedelta(minutes=30):
                    frozen, reason = None, None
                    try:
                        frozen = json.dumps(freeze(row, captured), ensure_ascii=False)
                    except (ValueError, TypeError, KeyError) as exc:
                        reason = str(exc)
                    state.execute(
                        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, NULL) "
                        "ON CONFLICT(event_id) DO UPDATE SET snapshot_id=excluded.snapshot_id, "
                        "sort_time=excluded.sort_time, sport=excluded.sport, league=excluded.league, "
                        "frozen=excluded.frozen, reason=excluded.reason, grades=NULL "
                        "WHERE excluded.sort_time>=events.sort_time",
                        (row["event_id"], snapshot, stamp(row["as_of"]).isoformat(),
                         sport, league, frozen, reason),
                    )
            elif kind == "settlement":
                matched = state.execute("SELECT event_id, frozen FROM events WHERE snapshot_id=? AND frozen IS NOT NULL",
                                        (snapshot,)).fetchone()
                if matched:
                    row = json.loads(raw)
                    frozen = json.loads(matched[1])
                    if stamp(row["settled_at"]) >= stamp(frozen["kickoff"]):
                        outcome = row.get("outcome") or {}
                        grades = {name: grade(pick, outcome) for name, pick in frozen["arms"].items()}
                        published = outcome.get("featured_pick_result") or {}
                        if published and published.get("result") != grades[BASE]:
                            grades[BASE] = None
                        grades["_settlement_sequence"] = sequence
                        state.execute("UPDATE events SET grades=? WHERE event_id=?",
                                      (json.dumps(grades), matched[0]))
            state.execute("UPDATE progress SET sequence=? WHERE singleton=1", (sequence,))
        return state.execute("SELECT sequence, started_at FROM progress").fetchone()


def performance(rows, name):
    grades = [(picks[name], results[name]) for picks, results in rows]
    hits = sum(result == "hit" for _, result in grades)
    refunds = sum(result == "void" for _, result in grades)
    decided = len(grades) - refunds
    net = sum((Decimal(str(pick["odds"])) - 1 if result == "hit" else
               Decimal(-1) if result == "miss" else Decimal(0) for pick, result in grades), Decimal(0))
    return {"hits": hits, "decided": decided, "refunds": refunds, "net_units": str(net),
            "accuracy": hits / decided if decided else None,
            "roi": float(net / len(grades)) if grades else None}


def report(state, captured, sequence, started_at):
    groups = {}
    holds = Counter()
    for sport, league, frozen, reason, grade_data in state.execute(
        "SELECT sport, league, frozen, reason, grades FROM events ORDER BY event_id"
    ):
        if not frozen:
            holds[reason] += 1
            continue
        picks = json.loads(frozen)["arms"]
        grades = json.loads(grade_data) if grade_data else {}
        for group, arm in ((sport, LOW), (league, LOW), (league, ARMS.get(league))):
            if arm:
                groups.setdefault((group, arm), []).append((picks, grades))
    comparisons = {}
    for (group, arm), rows in sorted(groups.items()):
        paired = [(picks, grades) for picks, grades in rows if grades.get(BASE) and grades.get(arm)]
        abstained = sum(picks[arm] is None for picks, _ in rows)
        comparisons.setdefault(group, {})[arm] = {
            "captured": len(rows), "abstained": abstained,
            "pending": len(rows) - abstained - len(paired),
            "common_settled": len(paired), "baseline": performance(paired, BASE),
            "alternative": performance(paired, arm),
        }
    return {"generated_at": captured.isoformat(), "started_at": started_at, "source_sequence": sequence,
            "held": dict(holds), "comparisons": comparisons}


def cycle(source_path, state_root):
    with closing(sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as source:
        source.execute("PRAGMA query_only=ON")
        source.execute("PRAGMA cache_size=-1024")
        with closing(database(state_root / "comparison.sqlite3")) as state:
            for _ in range(8):
                captured = now()
                seq, start = process(source, state, captured)
                newest = source.execute("SELECT MAX(ledger_sequence) FROM prediction_records").fetchone()[0]
                if newest is None or seq >= newest:
                    break
            result = report(state, now(), seq, start)
    target = state_root / "report.json"
    pending = state_root / f".report-{time.time_ns()}.tmp"
    pending.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(target)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--loop", type=int, default=0)
    args = parser.parse_args()
    if args.loop < 0:
        parser.error("--loop must be nonnegative")
    if args.loop:
        import fcntl

        args.state.mkdir(parents=True, exist_ok=True)
        loop_lock = (args.state / "runner.lock").open("a")
        try:
            fcntl.flock(loop_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
    while True:
        try:
            cycle(args.source, args.state)
        except sqlite3.OperationalError as exc:
            if not args.loop:
                raise
            print(f"SQLite retry: {exc}", flush=True)
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
