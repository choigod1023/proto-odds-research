"""Retain the two eligible K League price observations from the live collector."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import requests

from snapshot import KLEAGUES, MAX_COMPRESSED_RECEIPT_BODY, MAX_RECEIPT_BODY, _snapshot_record

DETAIL = "https://www.betman.co.kr/buyPsblGame/gameInfoInq.do"
KST = timezone(timedelta(hours=9))
FIRST_ROUND = (2026, 133)
LEAGUES = {"SC001": "K리그1", "SC003": "K리그2"}
DATE_TEXT = re.compile(r"(\d{2}\.\d{2})\([^)]*\)\s+(\d{2}:\d{2})")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write(data)
            handle.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())


def read_json(path):
    return json.loads(path.read_bytes())


def store_body(root, raw):
    if len(raw) > MAX_RECEIPT_BODY:
        raise ValueError("raw response exceeds the capture limit")
    digest = sha(raw)
    path = root / "bodies" / f"{digest}.gz"
    if not path.exists():
        body = gzip.compress(raw, compresslevel=3, mtime=0)
        if len(body) > MAX_COMPRESSED_RECEIPT_BODY:
            raise ValueError("compressed response exceeds the capture limit")
        atomic_write(path, body)
    return digest


def read_body(root, digest):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid response digest")
    with gzip.open(root / "bodies" / f"{digest}.gz", "rb") as file:
        body = file.read(MAX_RECEIPT_BODY + 1)
    if len(body) > MAX_RECEIPT_BODY or sha(body) != digest:
        raise ValueError("archived response digest mismatch")
    return body


def instant(ms):
    if type(ms) is not int:
        raise ValueError("expected official millisecond timestamp")
    return datetime.fromtimestamp(ms / 1000, timezone.utc)


def catalog(raw, year, round_number):
    doc = json.loads(raw)
    lottery = doc.get("currentLottery")
    if not lottery:
        return None
    expected = (year % 100) * 10000 + round_number
    if lottery["gmId"] != "G101" or int(lottery["gmTs"]) != expected or int(doc["gmTs"]) != expected:
        raise ValueError("Betman returned a different product round")
    if lottery.get("saleProgress") is not True or lottery.get("saleStatus") != "SaleProgress":
        return None
    opened, closing = (instant(lottery[key]) for key in ("saleStartDate", "saleEndDate"))
    schedule = doc["compSchedules"]
    keys = schedule["keys"]
    if len(set(keys)) != len(keys):
        raise ValueError("ambiguous Betman response columns")
    products = {}
    seen = set()
    for values in schedule["datas"]:
        row = dict(zip(keys, values, strict=True))
        number = row["matchSeq"]
        if type(number) is not int or number <= 0 or number in seen:
            raise ValueError("invalid or duplicate Betman product")
        seen.add(number)
        league = LEAGUES.get(row.get("leagueCode"))
        identity = tuple(row.get(key) for key in ("itemCode", "betId", "betTypId", "betNm", "betTypNm",
                         "live", "handi", "winHandi", "drawHandi", "loseHandi", "unsetSchedule", "unsetEndDate"))
        if (not league or identity != ("SC", "1", "1", "축구 승무패", "승무패", "N", 0, 0, 0, 0, "N", "N")
                or row.get("gameSubject") not in (None, "") or not row.get("homeId") or not row.get("awayId")
                or (row.get("gameReject"), row.get("buyReject")) != ("0", "0")):
            continue
        kickoff = instant(row["gameDate"])
        deadline = min(instant(row["endDate"]), closing, kickoff)
        if opened < deadline:
            products[str(number)] = dict(league=league, kickoff=kickoff.isoformat(),
                                         deadline=deadline.isoformat(), home_id=row["homeId"], away_id=row["awayId"])
    return {"opened": opened.isoformat(), "closing": closing.isoformat(), "products": products}


def market(row):
    return (row.sport == "sc" and row.league in KLEAGUES and row.market_family == "승무패"
            and not row.market_label and not row.is_void and row.result == "경기전" and len(row.odds) == 3
            and all(math.isfinite(value) and value > 1 for value in row.odds))


def eligible_windows(row, reference, received):
    product = reference["products"].get(str(row.game_no))
    if not product or product["league"] != row.league:
        return ()
    kickoff = datetime.fromisoformat(product["kickoff"])
    stamp = DATE_TEXT.fullmatch(row.date_text)
    if not stamp or stamp.groups() != (kickoff.astimezone(KST).strftime("%m.%d"), kickoff.astimezone(KST).strftime("%H:%M")):
        return ()
    opened = datetime.fromisoformat(reference["opened"])
    if not opened <= received < datetime.fromisoformat(product["deadline"]):
        return ()
    phases = []
    if received <= opened + timedelta(hours=1):
        phases.append("opening")
    if kickoff - timedelta(minutes=35) <= received <= kickoff - timedelta(minutes=30):
        phases.append("t30")
    return phases


def fetch_catalog(year, round_number):
    number = (year % 100) * 10000 + round_number
    payload = {"gmId": "G101", "gmTs": str(number), "gameYear": str(year), "_sbmInfo": {"debugMode": "false"}}
    headers = {"Referer": "https://www.betman.co.kr/main/mainPage/gamebuy/gameSlip.do",
               "X-Requested-With": "XMLHttpRequest"}
    with requests.post(DETAIL, json=payload, headers=headers, timeout=(2, 3), stream=True,
                       allow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError(f"Betman reference status {response.status_code}")
        raw = response.raw.read(MAX_RECEIPT_BODY + 1, decode_content=True)
    if len(raw) > MAX_RECEIPT_BODY:
        raise ValueError("Betman reference exceeds the capture limit")
    return raw, datetime.now(timezone.utc)


class PriceWindowRecorder:
    """Called under the existing live odds worker's single-writer lock."""

    def __init__(self, database, root=None):
        self.database = database
        self.root = root if root is not None else database.path.parent / "research" / "kleague-price-windows-v1"
        self.requested = False

    def _reference(self, directory, year, round_number, received):
        path = directory / "catalog.json"
        if path.exists():
            return read_json(path)
        attempt = directory / "catalog-attempt.json"
        if self.requested or (attempt.exists() and
                received - datetime.fromisoformat(read_json(attempt)["at"]) < timedelta(minutes=2)):
            return None
        self.requested = True
        write_json(attempt, {"at": received.isoformat()})
        raw, observed = fetch_catalog(year, round_number)
        result = catalog(raw, year, round_number)
        if (not result or not result["products"]
                or not datetime.fromisoformat(result["opened"]) <= observed < datetime.fromisoformat(result["closing"])):
            return None
        result.update(body_sha256=store_body(directory, raw), received_at=observed.isoformat())
        write_json(path, result)
        return result

    def observe(self, year, round_number, rows, received, body):
        if (year, round_number) < FIRST_ROUND:
            return 0
        counts = Counter(str(row.game_no) for row in rows)
        candidates = [row for row in rows if market(row) and counts[str(row.game_no)] == 1]
        if not candidates:
            return 0
        directory = self.root / f"{year}-{round_number}"
        reference = self._reference(directory, year, round_number, received)
        if reference is None:
            return 0
        manifest_path = directory / "windows.json"
        manifest = read_json(manifest_path) if manifest_path.exists() else {}
        found = {}
        stamp = received.isoformat(timespec="seconds")
        for row in candidates:
            number = str(row.game_no)
            phases = [phase for phase in eligible_windows(row, reference, received)
                      if phase not in manifest.get(number, {})]
            if phases:
                found[number] = (phases, _snapshot_record(row, stamp))
        if not found:
            return 0
        digest = store_body(directory, body)
        self.database.insert_odds((record for _, record in found.values()), busy_timeout_ms=500)
        for number, (phases, record) in found.items():
            for phase in phases:
                manifest.setdefault(number, {})[phase] = {
                    "received_at": received.isoformat(), "body_sha256": digest, "db_quote": record,
                }
        write_json(manifest_path, manifest)
        return len(found)
