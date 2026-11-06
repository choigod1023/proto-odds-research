"""Direct Betman KBO price capture. No third-party or retrospective fallback.

collect_prices(state_dir, games, client=None) returns quotes/excluded/requests.
games contain official_game_id, home_id, away_id, kickoff_at and season. The
caller must serialize invocations for a given state_dir. Response bytes are
stored in price-evidence; request records expose their paths and byte hashes.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

BASE = "https://www.betman.co.kr"
LIST_URL = BASE + "/buyPsblGame/inqBuyAbleGameInfoList.do"
DETAIL_URL = BASE + "/buyPsblGame/gameInfoInq.do"
REFERER = BASE + "/main/mainPage/gamebuy/buyableNClosedIFR.do?isIFR=Y"
DELAY_SECONDS = 1.5
MAX_REQUESTS = 80
MAX_PAGES = 20
KST = timezone(timedelta(hours=9))
TEAMS = {"HH", "KT", "SS", "OB", "LG", "WO", "HT", "LT", "NC", "SK"}
WINNER = ("2", "2", "야구 승패", "일반 승패")
TOTAL = ("79", "7", "야구 언더오버", "일반 언더오버")
HANDICAP = ("7", "5", "야구 핸디캡", "일반 소수핸디캡")


def _utc():
    return datetime.now(timezone.utc)


def _moment(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timezone_required")
    return result.astimezone(timezone.utc)


def _millis(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("invalid_source_time")
    return datetime.fromtimestamp(value / 1000, timezone.utc)


def _write(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _price(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 1 < value < 1000:
        raise ValueError("invalid_odds")
    return value


def _product_terms(row, product):
    if row.get("drawAllot") != 0 or row.get("gameSubject") not in (None, "") or row.get("drawHandi") != 0:
        raise ValueError("product_terms_mismatch")
    line = row.get("winHandi")
    other = row.get("loseHandi")
    labels = (row.get("winTxt"), row.get("drawTxt"), row.get("loseTxt"))
    if product == WINNER:
        if labels != ("승", "-", "패") or line != 0 or other != 0:
            raise ValueError("product_terms_mismatch")
        return "full_game_2way", "draw_void_refund", None, ("home", "away")
    if type(line) not in (int, float) or not math.isfinite(line) or line % 1 != 0.5:
        raise ValueError("unsupported_line")
    if product == TOTAL and labels == ("언더", "-", "오버") and line > 0 and other == line:
        return "full_game_total", "total_half", line, ("under", "over")
    if product == HANDICAP and labels == ("승", "-", "패") and other == -line:
        return "full_game_handicap", "handicap_half", line, ("home", "away")
    raise ValueError("product_terms_mismatch")


def parse_detail(raw, round_info, games, receipt, evidence, *, research_exact_lines=False):
    """Normalize full-game 2-way rows, with half-lines opt-in for research.

    Historical responses can be used for parser tests, but their actual receipt
    time cannot be moved backwards; receipt after T-30 is always rejected.
    Three-selection win-by-one and first-half markets are not draw markets.
    """
    result = json.loads(raw)
    gmts = str(round_info["gmTs"])
    if str(result.get("gmTs")) != gmts:
        raise ValueError("round_mismatch")
    data = result["compSchedules"]
    keys, rows = data["keys"], data["datas"]
    if not isinstance(keys, list) or len(keys) != len(set(keys)) or not isinstance(rows, list):
        raise ValueError("invalid_detail_envelope")
    if any(not isinstance(row, list) or len(row) != len(keys) for row in rows):
        raise ValueError("invalid_detail_row")
    receipt = _moment(receipt)
    end_round = _millis(round_info["saleEndDate"])
    fixtures = list(games)
    identities = Counter((g.get("home_id"), g.get("away_id"), g.get("kickoff_at")) for g in fixtures)
    decoded = [dict(zip(keys, row)) for row in rows]
    sequences = Counter(r["matchSeq"] for r in decoded if type(r.get("matchSeq")) is int) if research_exact_lines else {}
    supported = (WINNER, TOTAL, HANDICAP) if research_exact_lines else (WINNER,)
    quotes, excluded = [], []
    for row in decoded:
        if row.get("leagueCode") != "BS001":
            continue
        market_key = f"G101:{gmts}:{row.get('matchSeq')}"
        # These exact codes and textual labels were confirmed in actual Betman
        # responses. 108/3 is win-by-one, 111/1 is first-half W/D/L.
        product = tuple(row.get(k) for k in ("betId", "betTypId", "betNm", "betTypNm"))
        if product not in supported:
            excluded.append({"market_id": market_key, "reason": "unsupported_product"})
            continue
        try:
            kind, rule, line, sides = _product_terms(row, product)
        except ValueError as exc:
            excluded.append({"market_id": market_key, "reason": str(exc)})
            continue
        reason = None
        if type(row.get("matchSeq")) is not int or row["matchSeq"] <= 0 or not all(isinstance(row.get(k), str) and row[k].strip() for k in ("homeName", "awayName")):
            reason = "invalid_market_identity"
        elif research_exact_lines and sequences[row["matchSeq"]] != 1:
            reason = "duplicate_market_identity"
        elif row.get("gameReject") != "0" or row.get("buyReject") != "0" or row.get("protoStatus") != "2" or row.get("live") != "N" or row.get("unsetSchedule") != "N" or row.get("unsetEndDate") != "N":
            reason = "not_confirmed_on_sale"
        elif row.get("homeId") not in TEAMS or row.get("awayId") not in TEAMS or row.get("homeId") == row.get("awayId"):
            reason = "unknown_teams"
        try:
            start, close = _millis(row["gameDate"]), _millis(row["endDate"])
            if not reason and (receipt > start - timedelta(minutes=30) or receipt >= close or receipt >= end_round or close > start):
                reason = "late_or_closed"
            if not reason:
                home, away = _price(row["winAllot"]), _price(row["loseAllot"])
            matches = [g for g in fixtures if g.get("home_id") == row.get("homeId") and g.get("away_id") == row.get("awayId") and _moment(g["kickoff_at"]) == start and g.get("season") == start.astimezone(KST).year and g.get("official_game_id")]
            if not reason and (len(matches) != 1 or identities[(matches[0].get("home_id"), matches[0].get("away_id"), matches[0].get("kickoff_at"))] != 1):
                reason = "fixture_unmatched_or_ambiguous"
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            reason = reason or "invalid_field"
        if reason:
            excluded.append({"market_id": market_key, "reason": reason})
            continue
        quotes.append({"official_game_id": matches[0]["official_game_id"], "home_id": row["homeId"], "away_id": row["awayId"],
                       "home_name": row["homeName"], "away_name": row["awayName"], "kickoff_at": start.astimezone(KST).isoformat(),
                       "close_at": close.isoformat(), "round_close_at": end_round.isoformat(), "market_id": market_key,
                       "kind": kind, "settlement_rule": rule, "odds": dict(zip(sides, (home, away))),
                       "line": line, "received_at": receipt.isoformat(), "evidence": evidence,
                       "source_product": {"betId": row["betId"], "betTypId": row["betTypId"], "betNm": row["betNm"], "betTypNm": row["betTypNm"]}})
    if research_exact_lines:
        return quotes, excluded
    counts = Counter(q["official_game_id"] for q in quotes)
    for q in quotes[:]:
        if counts[q["official_game_id"]] > 1:
            quotes.remove(q); excluded.append({"market_id": q["market_id"], "reason": "multiple_market_candidates"})
    return quotes, excluded


def collect_prices(state_dir, games, client=None):
    games = list(games)
    state = Path(state_dir); state.mkdir(parents=True, exist_ok=True)
    evidence_dir = state / "price-evidence"; evidence_dir.mkdir(exist_ok=True)
    throttle = state / "price-last-request.json"
    session = client or requests.Session(); records = []; excluded = []; quotes = []
    headers = {"User-Agent": "Mozilla/5.0", "Referer": REFERER, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}

    def fetch(url, payload):
        for attempt in range(2):
            if len(records) >= MAX_REQUESTS:
                raise ValueError("request_cap")
            if throttle.exists():
                try:
                    elapsed = (_utc() - _moment(json.loads(throttle.read_text(encoding="utf-8"))["requested_at"])).total_seconds()
                    if elapsed < -5: raise ValueError("future throttle timestamp")
                    if elapsed < DELAY_SECONDS: time.sleep(DELAY_SECONDS - elapsed)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    raise ValueError("invalid_throttle_state")
            start = _utc(); _write(throttle, json.dumps({"requested_at": start.isoformat()}).encode())
            record = {"url": url, "method": "POST", "params": payload, "requested_at": start.isoformat(), "attempt": attempt + 1}
            try:
                response = session.post(url, json=payload, headers=headers, timeout=(5, 15), allow_redirects=False)
                raw = response.content; digest = hashlib.sha256(raw).hexdigest(); target = evidence_dir / (digest + ".body")
                if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest: raise ValueError("evidence_hash_mismatch")
                if not target.exists(): _write(target, raw)
                record.update(received_at=_utc().isoformat(), status=response.status_code, content_type=response.headers.get("Content-Type", ""), sha256=digest, body_file=str(target)); records.append(record)
                if response.status_code == 200 and "json" in record["content_type"].lower(): return raw, record
                reason = "official_non_json_response" if response.status_code == 200 else "official_http_" + str(response.status_code)
                if response.status_code != 429: raise ValueError(reason)
            except requests.RequestException as exc:
                record.update(failed_at=_utc().isoformat(), error_type=type(exc).__name__); records.append(record); reason = "official_request_failed"
            if attempt == 0: time.sleep(15)
        raise ValueError(reason)

    try:
        raw, listing = fetch(LIST_URL, {"_sbmInfo": {"debugMode": "false"}})
        listing_data = json.loads(raw); rounds = listing_data["protoGames"]
        if not isinstance(rounds, list): raise ValueError("invalid_rounds")
        rounds = [r for r in rounds if r.get("gmId") == "G101"]
        if len(rounds) > MAX_PAGES: raise ValueError("page_cap")
        if len({str(r.get("gmTs")) for r in rounds}) != len(rounds): raise ValueError("duplicate_round")
        for r in rounds:
            gmts = str(r["gmTs"]); year = r["gmOsidTsYear"]
            if not gmts.isdigit() or len(gmts) != 6 or type(year) is not int or int(gmts[:2]) != year % 100: raise ValueError("invalid_round_identity")
            if _moment(listing["received_at"]) >= _millis(r["saleEndDate"]): continue
            raw, rec = fetch(DETAIL_URL, {"gmId": "G101", "gmTs": gmts, "gameYear": str(year), "_sbmInfo": {"debugMode": "false"}})
            q, x = parse_detail(raw, r, games, rec["received_at"], {"uri": DETAIL_URL, "body_file": rec["body_file"], "sha256": rec["sha256"], "received_at": rec["received_at"], "round_listing_sha256": listing["sha256"]})
            quotes.extend(q); excluded.extend(x)
        counts = Counter(q["official_game_id"] for q in quotes)
        for q in quotes[:]:
            if counts[q["official_game_id"]] > 1:
                quotes.remove(q); excluded.append({"market_id": q["market_id"], "reason": "multiple_round_candidates"})
    except (KeyError, TypeError, ValueError, OverflowError, OSError, json.JSONDecodeError) as exc:
        excluded.append({"reason": str(exc)[:100]}); quotes = []
    return {"quotes": quotes, "excluded": excluded, "requests": records}
