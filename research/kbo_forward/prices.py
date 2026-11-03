"""Fail-closed Betman public-price probe for the forward research ledger.

collect_prices(state_dir, games, client=None) returns {quotes, excluded, requests}.
Only directly received official response bytes can ever support a quote. The
observed endpoint currently returns an HTML error, not a documented price
envelope, so no production price parser or product inference is enabled.
This module never falls back to the service artifact or to third-party odds.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://www.betman.co.kr"
ENDPOINT = BASE + "/buyPsblGame/inqScheduleSltList.do"
REFERER = BASE + "/main/mainPage/gamebuy/gameSlipIFR.do"
DELAY_SECONDS = 1.5
MAX_REQUESTS = 80
MAX_PAGES = 20


def _utc():
    return datetime.now(timezone.utc)


def _stamp(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _write(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def collect_prices(state_dir, games, client=None):
    """Probe once and persist raw response; unknown/error envelopes never price.

    games is the parent runner's iterable of official fixtures; it is not
    trusted as evidence of a market. client may provide requests.Session-like
    post() for tests. No matching is attempted until the official schema,
    product, settlement, market close and full identity have been verified.
    State must be private to a single scheduler process (no concurrent runs).
    """
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    evidence = state / "price-evidence"
    evidence.mkdir(exist_ok=True)
    throttle = state / "price-last-request.json"
    if throttle.exists():
        try:
            previous = datetime.fromisoformat(json.loads(throttle.read_text(encoding="utf-8"))["requested_at"])
            if previous.tzinfo is None:
                raise ValueError("missing timezone")
            elapsed = (_utc() - previous).total_seconds()
            if elapsed < -5:
                raise ValueError("future throttle timestamp")
            wait = DELAY_SECONDS - elapsed
            if wait > 0:
                time.sleep(wait)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return {"quotes": [], "excluded": [{"reason": "invalid_throttle_state"}], "requests": []}
    session = client or requests.Session()
    headers = {"User-Agent": "Mozilla/5.0", "Referer": REFERER,
               "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
    records = []
    reason = "official_unverified_envelope"
    # At most two requests; both respect spacing. No redirect or hidden page crawl.
    for attempt in range(2):
        start = _utc()
        _write(throttle, json.dumps({"requested_at": _stamp(start)}).encode())
        record = {"url": ENDPOINT, "method": "POST", "params": {"code": "P_ALL"},
                  "requested_at": _stamp(start), "attempt": attempt + 1}
        try:
            response = session.post(ENDPOINT, json={"code": "P_ALL"}, headers=headers,
                                    timeout=(5, 15), allow_redirects=False)
            raw = response.content
            digest = hashlib.sha256(raw).hexdigest()
            target = evidence / (digest + ".body")
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError("evidence_hash_mismatch")
            if not target.exists():
                _write(target, raw)
            record.update(received_at=_stamp(_utc()), status=response.status_code,
                          content_type=response.headers.get("Content-Type", ""),
                          sha256=digest, body_file=str(target))
            if response.status_code != 200:
                reason = "official_http_" + str(response.status_code)
            elif "json" not in record["content_type"].lower():
                reason = "official_non_json_response"
            else:
                # Never accept an undocumented response merely because it is JSON.
                reason = "official_unverified_envelope"
            records.append(record)
            if response.status_code != 429:
                break
        except (requests.RequestException, ValueError) as exc:
            record.update(failed_at=_stamp(_utc()), error_type=type(exc).__name__)
            records.append(record)
            reason = "official_request_failed"
        if attempt == 0:
            time.sleep(15)
    excluded = [{"reason": reason, "official_game_id": g.get("official_game_id")}
                for g in games]
    if not excluded:
        excluded = [{"reason": reason, "official_game_id": None}]
    return {"quotes": [], "excluded": excluded, "requests": records}
