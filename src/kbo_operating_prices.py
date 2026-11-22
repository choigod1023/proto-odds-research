"""Replay the operating Wisetoto price observation without calling Betman.

The live collector writes rolling receipts. A successful research capture
references an immutable copy. These receipts prove locally observed quotes,
not that the source accepted wagers.
"""
from collections import Counter
from datetime import timedelta
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

import requests

from public_tipster_votes import KST, stamp, team
from wisetoto import BASE, _decode, clean_market_team, parse_rows

SOURCE = BASE + "/util/gameinfo/get_proto_list.htm"
SLOTS = 8
MAX_RAW = 4 * 1024 * 1024
MAX_COMPRESSED = 256 * 1024
FRESHNESS = timedelta(minutes=10)
DATE = re.compile(r"(\d{2}\.\d{2})\([^)]*\)\s+(\d{2}:\d{2})")
PRODUCTS = {
    "핸디캡": (r"H\s*([-+]?\d+(?:\.\d+)?)", "handicap_half", ("핸디홈", "핸디원정")),
    "언더오버": (r"(?:U/O|[UO])\s*([-+]?\d+(?:\.\d+)?)", "total_half", ("언더", "오버")),
}


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _slot(root, number):
    return root / f"round-{number % SLOTS}.gz"


def _atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write(raw)
            handle.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def _line(row):
    product = PRODUCTS.get(row.market_family)
    if not product or row.is_first_half:
        return None
    match = re.fullmatch(product[0], row.market_label, re.IGNORECASE)
    if not match:
        return None
    line = float(match[1])
    if line % 1 != .5 or (row.market_family == "언더오버" and line <= 0):
        return None
    return line, product[1], dict(zip(product[2], row.odds))


def _eligible(row):
    return (row.sport == "bs" and row.league == "KBO" and row.result == "경기전"
            and not row.is_void and row.n_way == 2
            and all(1 < value < 1000 for value in row.odds) and _line(row) is not None)


def observe(root, year, number, rows, received, body):
    """Retain only receipts that contain a supported pregame KBO product."""
    if not any(_eligible(row) for row in rows):
        return
    if len(body) > MAX_RAW:
        raise ValueError("operating price source body too large")
    header = dict(uri=SOURCE, year=year, round=number,
                  received_at=received.isoformat(), raw_sha256=_digest(body))
    packet = gzip.compress(json.dumps(header, sort_keys=True).encode() + b"\n" + body,
                           compresslevel=3, mtime=0)
    if len(packet) > MAX_COMPRESSED:
        raise ValueError("operating price source packet too large")
    _atomic(_slot(root, number), packet)


def _read(path):
    with path.open("rb") as stream:
        packet = stream.read(MAX_COMPRESSED + 1)
    if len(packet) > MAX_COMPRESSED:
        raise ValueError("operating price receipt too large")
    return packet


def _replay(packet, event, at):
    with gzip.GzipFile(fileobj=io.BytesIO(packet)) as stream:
        header = json.loads(stream.readline(1024))
        if (header["uri"] != SOURCE or type(header["round"]) is not int
                or not 1 <= header["round"] <= 9999 or header["year"] != event["season"]):
            raise ValueError("operating price receipt identity differs")
        received = stamp(header["received_at"])
        start = stamp(event["kickoff_at"]).astimezone(KST)
        if (start.year != event["season"] or not at - FRESHNESS <= received <= at
                or at >= start - timedelta(minutes=30)):
            raise ValueError("operating price receipt stale, future or past T-30")
        body = stream.read(MAX_RAW + 1)
    if len(body) > MAX_RAW or _digest(body) != header["raw_sha256"]:
        raise ValueError("operating price source bytes differ")
    response = requests.Response()
    response._content = body
    rows = parse_rows(_decode(response), header["year"], header["round"])
    counts = Counter(row.game_no for row in rows)
    clock = (start.strftime("%m.%d"), start.strftime("%H:%M"))
    products = {}
    for row in rows:
        date = DATE.fullmatch(row.date_text)
        if (not _eligible(row) or counts[row.game_no] != 1 or not date
                or date.groups() != clock
                or team(clean_market_team(row.home), "KBO") != team(event["home_name"], "KBO")
                or team(clean_market_team(row.away), "KBO") != team(event["away_name"], "KBO")):
            continue
        line, rule, odds = _line(row)
        products[row.game_no] = dict(market=row.market_family, line=line, rule=rule, odds=odds)
    if not products:
        raise ValueError("no unique exact KBO product in operating price receipt")
    return dict(header, products=products, packet_sha256=_digest(packet))


def capture(root, archive, event, at):
    matches = []
    for number in range(SLOTS):
        path = _slot(root, number)
        try:
            packet = _read(path)
            evidence = _replay(packet, event, at)
            if _slot(root, evidence["round"]) == path:
                matches.append((packet, evidence))
        except (OSError, EOFError, KeyError, ValueError, TypeError):
            continue
    if len(matches) != 1:
        return None
    packet, evidence = matches[0]
    path = archive / (evidence["packet_sha256"] + ".gz")
    if not path.exists():
        _atomic(path, packet)
    if _read(path) != packet:
        raise ValueError("immutable operating price receipt differs")
    return evidence


def verify(archive, evidence, event, at):
    digest = evidence["packet_sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid operating price receipt digest")
    packet = _read(archive / (digest + ".gz"))
    if _replay(packet, event, at) != evidence:
        raise ValueError("operating price evidence differs from source")


def matches_offer(evidence, game, terms, offers, price_time):
    if str(evidence["round"]) != str(game.get("round")) or price_time < stamp(evidence["received_at"]).replace(microsecond=0):
        return False
    saved = evidence["products"].get(str(terms["game_no"]))
    return (terms["period"] == "full_including_extra" and terms["unit"] == "runs"
            and terms["n_way"] == 2 and saved == {
                "market": next(iter(offers.values()))["market"], "line": float(terms["line"]),
                "rule": terms["settlement_rule"], "odds": {side: float(offer["배당"]) for side, offer in offers.items()},
            })
