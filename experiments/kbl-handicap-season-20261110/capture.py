"""Bounded capture of official archives to fill the two KBL season windows."""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from collect_basketball_comparison_sources import capture, kbl_records
from collect_proto_comparison_prices import BASE, CLOSED, DETAIL, EVIDENCE, KST, MAX_BODY_BYTES

WINDOWS = (("2024-10-15", "2025-05-18"), ("2025-10-01", "2026-05-15"))
KBL_URL = "https://api.kbl.or.kr/match/list?fromDate=20241001&toDate=20241231&tcodeList=all"
HEADERS = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest",
           "Referer": BASE + "/main/mainPage/gamebuy/closedGameList.do"}


def write_json(path, value):
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


class PublicArchive:
    def __init__(self, directory, budget):
        self.directory = directory
        self.budget = budget
        self.last_request = 0.0

    def get(self, url, payload, label):
        meta = self.directory / (label + ".json")
        filename = label + ".body.gz"
        bodyfile = self.directory / filename
        if meta.exists():
            receipt = json.loads(meta.read_bytes())
            body = gzip.decompress(bodyfile.read_bytes())
            if (hashlib.sha256(body).hexdigest() != receipt["body_sha256"]
                    or receipt["source_url"] != url or receipt["request"] != payload):
                raise ValueError(f"Cache identity mismatch: {label}")
            return json.loads(body), receipt
        if self.budget <= 0:
            raise RuntimeError("HTTP budget exhausted")
        if (EVIDENCE / "STOP_403_429.json").exists() or (self.directory / "STOP_403_429.json").exists():
            raise RuntimeError("An archive access restriction is in effect")
        time.sleep(max(0.0, 1.05 - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        self.budget -= 1
        with requests.post(url, json=payload, headers=HEADERS, timeout=(7, 8), stream=True, allow_redirects=False) as response:
            if response.status_code in (403, 429):
                write_json(self.directory / "STOP_403_429.json", {
                    "status": response.status_code, "at": datetime.now(timezone.utc).isoformat(),
                    "url": url, "retry_after": response.headers.get("Retry-After"),
                })
                raise RuntimeError(f"HTTP {response.status_code}: stop")
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError(f"Unexpected archive status {response.status_code}")
            limit = time.monotonic() + 15
            body = bytearray()
            for part in response.iter_content(65536):
                body.extend(part)
                if len(body) > MAX_BODY_BYTES or time.monotonic() > limit:
                    raise ValueError("Archive response exceeds limit")
        receipt = {"source_url": url, "request": payload, "body_file": filename,
                   "body_sha256": hashlib.sha256(body).hexdigest(),
                   "received_at": datetime.now(timezone.utc).isoformat(),
                   "timestamp_basis": "http_response_body_completed"}
        parsed = json.loads(body)
        bodyfile.write_bytes(gzip.compress(body, mtime=0))
        write_json(meta, receipt)
        return parsed, receipt


def kst_day(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, KST).date().isoformat()


def relevant_rounds(client):
    start, rounds = 0, set()
    while True:
        args = {"gmId": "G101", "draw": 1, "start": start, "length": 20, "_sbmInfo": {"debugMode": "false"}}
        data, _ = client.get(CLOSED, args, f"closed-{start}")
        info = data["schedules"]
        rows = info["data"]
        for r in rows:
            begin, end = kst_day(r["saleStartDate"]), kst_day(r["saleEndDate"])
            if any(begin <= b and end >= a for a, b in WINDOWS):
                rounds.add(str(r["gmTs"]).zfill(6))
        start += len(rows)
        if not rows or start >= info["recordsFiltered"] or max(kst_day(r["saleEndDate"]) for r in rows) < WINDOWS[0][0]:
            return rounds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=230, choices=range(1, 251), metavar="1..250")
    args = parser.parse_args()
    dest = args.out.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    meta, body = capture("kbl-2024-q4", KBL_URL, dest)
    schedule = kbl_records(meta, body)
    if not schedule:
        raise ValueError("Official KBL schedule unavailable")
    (dest / "kbl-2024-q4.jsonl").write_bytes(b"".join(
        (json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n").encode() for r in schedule
    ))
    print("additional KBL fixture/result records", len(schedule), flush=True)
    manifest = json.loads((ROOT / "experiments/pregame-proto-prices-20261104/manifest-v2.json").read_bytes())
    index = len(manifest["source_roots"])
    manifest["source_roots"].append(str(dest))
    client = PublicArchive(dest, args.budget)
    wanted = sorted(relevant_rounds(client) - {source["round"] for source in manifest["sources"]})
    print("missing round details", len(wanted), flush=True)
    try:
        for i, number in enumerate(wanted, 1):
            payload = {"gmId": "G101", "gmTs": number, "gameYear": "20" + number[:2], "_sbmInfo": {"debugMode": "false"}}
            detail, receipt = client.get(DETAIL, payload, "round-" + number)
            if str(detail["gmTs"]).zfill(6) != number or str(detail["currentLottery"]["gmId"]) != "G101":
                raise ValueError(f"Unexpected Betman round identity {number}")
            manifest["sources"].append(dict(receipt, source_root_index=index, round=number))
            if i % 20 == 0:
                print(f"details {i}/{len(wanted)}", flush=True)
    finally:
        manifest["source_round_count"] = len(manifest["sources"])
        write_json(dest / "manifest-v2.json", manifest)
    print("total archive rounds", manifest["source_round_count"], flush=True)


if __name__ == "__main__":
    main()
