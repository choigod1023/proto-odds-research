"""Refresh the verified operating ledger when local evidence can be settled."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import traceback
from xml.etree.ElementTree import Element, SubElement, tostring

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import forward_research_cycle as kleague
from scripts import sync_operating_ledger
from ai_decision import offer_id, selection_id
from kleague_snapshot_replay import _observations, _official_results, _products
from prediction_ledger import PredictionLedger
from research.kbo_forward import paired_comparison as kbo
from src.prospective_tipster_pairs import evaluate


def fingerprint(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def kbo_source(root, now):
    state = root / "kbo-m0-forward-v1"
    rows = kbo.ledger.read(state / "paired-ledger.jsonl")
    if not any(row["type"] == "capture" for row in rows) and not any(
        (state / kbo.CAPTURE_DIR).glob("*.jsonl")
    ):
        return [], None
    replay = kbo.replay(state, now)
    possible = kbo.compare(replay, None, rows, state)
    offers = {row["market_id"] for row in possible["excluded"]
              if row["missing"] == {"existing_pick": "operating_ledger_export_missing"}}
    keys = [fingerprint(row) for row in replay["rows"] if row["market"]["market_id"] in offers]
    return sorted(keys), (replay, rows, state)


def league_source(root):
    state = root / "kleague-forward-v1"
    bodies = state / "odds/verified-bodies"
    documents = [doc for doc, _ in kleague.archived(state / "published-results")]
    results = _official_results(documents)
    if not results:
        return [], None
    receipts = list(kleague._receipts(sorted((state / "odds").glob("kleague_odds_receipts_*.jsonl")), None))
    observed = _observations(receipts, Counter(), bodies)
    keys = set()
    for fixture, snapshots in observed.items():
        for instant, digest, evidence, rows in snapshots:
            if evidence != "raw_verified" or instant >= fixture[3] - timedelta(minutes=30):
                continue
            markets = _products(rows)
            if markets and all(len(results.get((fixture, p), (None, set()))[1]) == 1 for p in markets):
                keys.add(fingerprint([digest, [str(x) for x in fixture], [
                    [list(p), sorted(results[fixture, p][1])] for p in sorted(markets)
                ]]))
    return sorted(keys), (receipts, documents, bodies)


def tipster_source(root, now):
    keys, rounds = [], {}
    for folder in sorted((root / "tipster-forward-v1/paired-v1").glob("*")):
        if not folder.is_dir() or not re.fullmatch(r"[0-9]{6}", folder.name):
            continue
        dirs = sorted(path for path in folder.iterdir() if path.is_dir())
        report = evaluate(folder.name, capture_dirs=dirs, evaluated_at=now.isoformat())
        settled = [event for event in report["events"]
                   if event["result"]["status"] in ("settled", "void")
                   and event["deduplicated_vote"]["selection"] is not None]
        if settled:
            keys.extend(fingerprint(row) for row in settled)
            rounds[folder.name] = dirs
    return sorted(keys), rounds


def percent(fraction):
    return round(fraction * 100, 6) if fraction is not None else None


def same_league_prices(row, prediction):
    features = prediction["features"]
    game = features["fixture"]
    number = row["frozen_pick"]["game_no"]
    product = next(p for p in row["products"] if p["game_no"] == number)
    for side, price in product["odds"].items():
        option = {"게임번호": number, "market": product["market"], "label": product["label"],
                  "line": product["line"], "선택": side}
        sid, oid = selection_id(game, option), offer_id(game, option)
        prices = [item.get("odds") for item in features.get("options", [])
                  if (item.get("selection_id"), item.get("offer_id")) == (sid, oid)]
        if prices != [price]:
            return False
    return True


def compare(operating, now, kbo_input, league_input, tipster_rounds):
    reports = {}
    if kbo_input:
        replay, rows, state = kbo_input
        report = kbo.compare(replay, operating, rows, state)
        if report["paired_offers"]:
            reports["KBO 타순 변경 M0"] = {
                "settled": report["paired_offers"],
                "arms": {arm: {"accuracy": percent(m["hit_rate"]),
                               "roi": percent(m["unit_stake_paper_roi"])}
                         for arm, m in report["metrics"].items()},
                "samples": [row["market_id"] for row in report["rows"]],
            }
    if league_input:
        receipts, documents, bodies = league_input
        report = kleague.compare(receipts, operating, documents, bodies=bodies)
        predictions = {record["snapshot_id"]: record for record in operating
                       if record["record_type"] == "prediction"}
        verified = [row for row in report["rows"] if row["receipt_evidence"] == "raw_verified"
                    and same_league_prices(row, predictions[row["prediction_snapshot_id"]])]
        if verified:
            reports["K리그 배당 기준 픽"] = {
                "settled": len(verified),
                "arms": {arm: {"accuracy": percent(m["hit_rate"]), "roi": percent(m["roi"])}
                         for arm, m in kleague.metrics(verified).items()},
                "samples": [row["event_id"] for row in verified],
            }
    for number, dirs in tipster_rounds.items():
        report = evaluate(number, capture_dirs=dirs, evaluated_at=now.isoformat(), ledger_records=operating)
        paired = report["paired_vs_frozen_operating_pick"]
        if paired["graded_products"]:
            reports[f"픽스터 {number}회차"] = {
                "settled": paired["graded_products"],
                "arms": {arm: {"accuracy": m["accuracy_pct"], "roi": m["roi_pct"]}
                         for arm, m in paired["arms"].items()},
                "samples": [row["product"]["product_id"] for row in report["events"]
                            if row["product"]["product_id"] in paired["paired_product_ids"]
                            and row["result"]["status"] in ("settled", "void")],
            }
    return reports


POWERSHELL = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotification,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotifier,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null
[Windows.UI.Notifications.NotificationSetting,Windows.UI.Notifications,ContentType=WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime] > $null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app)
if ($null -ne $notifier.Setting -and $notifier.Setting -ne [Windows.UI.Notifications.NotificationSetting]::Enabled) {
    throw "Windows toast setting is $($notifier.Setting)"
}
"""


def powershell(script):
    command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError(f"Windows notification failed (exit {result.returncode})")


def toast(title, lines, *, check=False):
    xml = Element("toast")
    binding = SubElement(SubElement(xml, "visual"), "binding", template="ToastGeneric")
    SubElement(binding, "text").text = title
    SubElement(binding, "text").text = "\n".join(lines)
    body = base64.b64encode(tostring(xml, encoding="utf-8")).decode()
    script = POWERSHELL + f"""
$xml = [Windows.Data.Xml.Dom.XmlDocument]::new()
$xml.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{body}')))
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
"""
    if not check:
        script += "$notifier.Show($toast)\n"
    powershell(script)


def notify(reports):
    names = {"existing_pick": "기존 픽", "frozen_operating_pick": "기존 픽", "revised_m0": "M0",
             "same_offer_market_favorite": "배당 기준", "deduplicated_vote": "중복 제거 픽스터"}

    def display(value):
        return f"{value:.1f}%" if value is not None else "미산출"

    lines = []
    for cohort, report in reports.items():
        lines.append(f"{cohort} {report['settled']}건")
        for arm, metrics in report["arms"].items():
            lines.append(f"{names[arm]} 적중률 {display(metrics['accuracy'])}, ROI {display(metrics['roi'])}")
    toast("스포츠 예측 정산 비교", lines)


def run(root, state):
    now = datetime.now(timezone.utc)
    status_path = state / "status.json"
    saved = json.loads(status_path.read_bytes()) if status_path.exists() else {}
    k_keys, k_input = kbo_source(root, now)
    l_keys, l_input = league_source(root)
    t_keys, t_input = tipster_source(root, now)
    counts = [len(k_keys), len(l_keys), len(t_keys)]
    current = dict(checked_at=now.isoformat(), candidate_counts=dict(zip(("kbo", "kleague", "tipster"), counts)))
    if not any(counts):
        kleague.atomic_json(status_path, saved | current | {"status": "no_settlement_candidates"})
        return "no_settlement_candidates"

    key = fingerprint([k_keys, l_keys, t_keys])
    if saved.get("compared_evidence") != key:
        ledger = root / "verified-operating-ledger.jsonl"
        receipt = sync_operating_ledger.run(ledger, ROOT / "src")
        operating = PredictionLedger(ledger).records()
        if len(operating) != receipt["verified_rows"]:
            raise ValueError("operating ledger changed during settlement")
        reports = compare(operating, now, k_input, l_input, t_input)
        kleague.atomic_json(state / "comparison.json", {
            "evaluated_at": now.isoformat(), "ledger_sequence": receipt["verified_rows"],
            "candidate_counts": current["candidate_counts"], "cohorts": reports,
        })
        saved.update(compared_evidence=key, reports=reports)
        kleague.atomic_json(status_path, saved | current | {"status": "compared"})
    reports = saved["reports"]
    result_key = fingerprint(reports)
    if reports and saved.get("notified_result") != result_key:
        notify(reports)
        saved["notified_result"] = result_key
    status = "compared" if reports else "no_exact_operating_matches"
    kleague.atomic_json(status_path, saved | current | {"status": status})
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--check-notifier", action="store_true")
    args = parser.parse_args()
    if args.check_notifier:
        toast("스포츠 예측 정산 비교", ["알림 형식 확인"], check=True)
        return 0
    state = args.root / "settlement-forward-v1"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "last-process.log").open("w", encoding="utf-8") as log:
        with redirect_stdout(log), redirect_stderr(log), kleague.single_writer(state / "task.lock") as owned:
            if not owned:
                return 0
            try:
                print(run(args.root, state))
            except Exception:
                traceback.print_exc()
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
