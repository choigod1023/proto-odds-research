"""Run one local, bounded capture of forward K League research evidence."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from ai_decision import event_id
from featured_pick import VERSION
from kleague_snapshot_replay import _fixture, _receipts, compare
from live_market_refresh import _aware_timestamp
from research.kbo_forward.locking import single_writer
from research.kbo_forward.runner import atomic_json
import snapshot

API = "https://proto-odds-web.fly.dev"
FIXTURE_KEYS = ("year", "round", "date", "sport", "league", "home", "away",
                "source_event_id", "match_id")
MAX_PUBLIC_BYTES = 8 * 1024 * 1024
MAX_RAW_ARCHIVE_BYTES = 64 * 1024 * 1024


def fetch_public(session, path):
    with session.get(API + path, timeout=(5, 12), stream=True) as response:
        response.raise_for_status()
        body = bytearray()
        for part in response.iter_content(65536):
            body.extend(part)
            if len(body) > MAX_PUBLIC_BYTES:
                raise ValueError("public evidence response exceeds size limit")
    return json.loads(body), bytes(body), datetime.now(timezone.utc)


def project_predictions(document, received):
    """Use a public snapshot only if it was seen before T-30."""
    for game in document.get("live", []):
        if (game.get("league") not in snapshot.KLEAGUES or game.get("status") != "경기전"
                or game.get("prediction_status") != "recorded_pregame"):
            continue
        saved = game.get("featured_pick_record") or {}
        decision = game.get("decision_snapshot") or {}
        pick = saved.get("pick") or {}
        market, as_of, captured, kickoff = map(_aware_timestamp, (
            decision.get("market_observed_at") or game.get("live_market_observed_at"),
            decision.get("as_of"), saved.get("captured_at"), saved.get("kickoff"),
        ))
        if pick.get("version") != VERSION or None in (market, as_of, captured, kickoff):
            continue
        snapshot_id = saved.get("prediction_snapshot_id")
        if (not snapshot_id or snapshot_id != decision.get("decision_id")
                or snapshot_id != game.get("prediction_revision_id")
                or not market <= as_of <= captured <= received < kickoff - timedelta(minutes=30)):
            continue
        fixture = {key: game[key] for key in FIXTURE_KEYS if key in game}
        identifier = event_id(fixture)
        if identifier != game.get("event_id") or identifier != decision.get("event_id"):
            continue
        matches = [option for option in game.get("options", [])
                   if option.get("selection_id") == pick.get("selection_id")
                   and option.get("offer_id") == pick.get("offer_id")]
        if len(matches) != 1 or matches[0].get("배당") != pick.get("odds"):
            continue
        yield dict(record_type="prediction", snapshot_id=snapshot_id, event_id=identifier,
                   as_of=as_of.isoformat(), captured_at=captured.isoformat(), kickoff=kickoff.isoformat(),
                   market_observed_at=market.isoformat(), features={"fixture": fixture},
                   predictions={"featured_pick": pick})


def archive(path, name, raw, received):
    path.mkdir(parents=True, exist_ok=True)
    body = path / (name + ".json.gz")
    manifest = path / (name + ".json")
    if not manifest.exists():
        temp = body.with_suffix(".tmp")
        temp.write_bytes(gzip.compress(raw, compresslevel=3, mtime=0))
        temp.replace(body)
        atomic_json(manifest, {
            "body_sha256": hashlib.sha256(raw).hexdigest(), "received_at": received.isoformat(),
        })


def archived(path):
    for metadata in sorted(path.glob("*.json")):
        receipt = json.loads(metadata.read_bytes())
        with gzip.open(metadata.with_suffix(".json.gz"), "rb") as handle:
            body = handle.read(MAX_PUBLIC_BYTES + 1)
        if len(body) > MAX_PUBLIC_BYTES or hashlib.sha256(body).hexdigest() != receipt["body_sha256"]:
            raise ValueError("saved public response failed hash verification")
        yield json.loads(body), _aware_timestamp(receipt["received_at"])


def capture_rounds(games, received):
    rounds = set()
    for g in games:
        f = _fixture(g, g.get("year"), g.get("round"), g.get("date"))
        if f and g.get("status") == "경기전" and f[3] > received + timedelta(minutes=30):
            rounds.add(f[:2])
    return sorted(rounds, reverse=True)[:2]


def retain_round_bodies(receipts, slots, retained):
    retained.mkdir(exist_ok=True)
    used = sum(path.stat().st_size for path in retained.glob("*.html.gz"))
    for receipt in receipts:
        digest = receipt["body_sha256"]
        target = retained / (digest + ".html.gz")
        slot = snapshot.receipt_body_slot(slots, digest)
        if target.exists() or not slot.is_file():
            continue
        compressed = slot.read_bytes()
        if used + len(compressed) > MAX_RAW_ARCHIVE_BYTES:
            return
        with gzip.open(slot, "rb") as handle:
            raw = handle.read(snapshot.MAX_RECEIPT_BODY + 1)
        if len(raw) <= snapshot.MAX_RECEIPT_BODY and hashlib.sha256(raw).hexdigest() == digest:
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(compressed)
            temporary.replace(target)
            used += len(compressed)


def metrics(rows):
    net = Counter()
    hits = Counter()
    decided = 0
    for row in rows:
        pick = row["frozen_pick"]
        product = next(p for p in row["products"] if p["game_no"] == pick["game_no"])
        if product["winner"] is None:
            continue
        decided += 1
        for arm, side in (("existing_pick", pick["selection"]),
                          ("same_offer_market_favorite", product["market_favorite"])):
            hit = side == product["winner"]
            hits[arm] += hit
            net[arm] += product["odds"][side] - 1 if hit else -1
    return {arm: dict(hits=hits[arm], decided=decided,
                      hit_rate=hits[arm] / decided if decided else None,
                      roi=net[arm] / len(rows) if rows else None)
            for arm in ("existing_pick", "same_offer_market_favorite")}


def cross_market_diagnostic(rows):
    # For a home -1 line, home win = handicap home win or handicap draw.
    # The mirror identity applies to away win with a home +1 line.
    cohorts = {"below_2pp": [], "at_least_2pp": []}
    for row in rows:
        winner = next((p for p in row["products"] if p["market"] == "승무패"), None)
        handicaps = [p for p in row["products"] if p["market"] == "핸디캡" and p["line"] in (-1, 1)]
        if winner is None or len(handicaps) != 1:
            continue
        handicap = handicaps[0]
        side = "홈" if handicap["line"] == -1 else "원정"
        implied = handicap["probabilities"]["핸디" + side] + handicap["probabilities"]["핸디무"]
        gap = abs(implied - winner["probabilities"][side])
        cohorts["at_least_2pp" if gap >= 0.02 else "below_2pp"].append(row)
    return {name: {"fixtures": len(group), **metrics(group)["existing_pick"]}
            for name, group in cohorts.items()}


def run(state):
    if os.environ.get("PROODD_DB_PATH"):
        raise ValueError("local collector requires file mode")
    state.mkdir(parents=True, exist_ok=True)
    with single_writer(state / "capture.lock") as acquired:
        if not acquired:
            return {"status": "busy"}
        odds_dir = state / "odds"
        odds_dir.mkdir(exist_ok=True)
        snapshot.OUT = odds_dir
        snapshot.CH_FILE = odds_dir / "changes.csv"
        predictions_dir, results_dir = state / "published-picks", state / "published-results"
        recorded_ids = {row["snapshot_id"] for doc, t in archived(predictions_dir)
                        for row in project_predictions(doc, t)}
        with requests.Session() as session:
            document, raw, received = fetch_public(session, "/api/matches?scope=recent")
            new_predictions = [row for row in project_predictions(document, received)
                               if row["snapshot_id"] not in recorded_ids]
            if new_predictions:
                archive(predictions_dir, hashlib.sha256(raw).hexdigest(), raw, received)
            results, raw, observed = fetch_public(session, "/live_odds.json")
            # Save a new full public result response only when official K League outcomes change.
            grades = sorted((str(rnd), str(no), str(row["result"]))
                            for rnd, bucket in results.get("markets", {}).items()
                            for no, row in bucket.items()
                            if row.get("league") in snapshot.KLEAGUES
                            and row.get("result") not in snapshot.UNPLAYED)
            if grades:
                archive(results_dir, hashlib.sha256(json.dumps(grades).encode()).hexdigest(), raw, observed)
        rounds = capture_rounds(document.get("live", []), received)
        retained = odds_dir / "verified-bodies"
        for year, rnd in rounds:
            snapshot.snap(year, [rnd], kleague_receipts=True)
            today = datetime.now(timezone.utc)
            paths = sorted(odds_dir.glob("kleague_odds_receipts_*.jsonl"))[-2:]
            recent = [entry for entry in _receipts(paths, None) if entry["round"] == rnd
                      and _aware_timestamp(entry["received_at"]) >= today - timedelta(minutes=5)]
            retain_round_bodies(recent, odds_dir / snapshot.RECEIPT_BODY_DIR, retained)
        receipts = sorted(odds_dir.glob("kleague_odds_receipts_*.jsonl"))
        report = compare(_receipts(receipts, None),
                         [row for doc, t in archived(predictions_dir) for row in project_predictions(doc, t)],
                         [doc for doc, _ in archived(results_dir)],
                         bodies=retained)
        verified = [row for row in report["rows"] if row["receipt_evidence"] == "raw_verified"]
        report["raw_verified_fixtures"] = len(verified)
        report["same_offer_performance"] = metrics(verified)
        report["cross_market_diagnostic"] = cross_market_diagnostic(verified)
        report["baseline_evidence"] = "public operating pick witnessed before T-30"
        report["rounds_requested"] = rounds
        atomic_json(state / "comparison.json", report)
        return {key: report[key] for key in ("eligible_fixtures", "decided_fixtures", "raw_verified_fixtures", "counts",
                                             "same_offer_performance", "rounds_requested")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--log", type=Path, help="Redirect console output for Windows pythonw")
    args = parser.parse_args()
    state = args.state.resolve()
    if args.log is None:
        print(json.dumps(run(state), ensure_ascii=False))
        return 0
    with args.log.open("w", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
        try:
            print(json.dumps(run(state), ensure_ascii=False))
        except Exception:
            traceback.print_exc()
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
