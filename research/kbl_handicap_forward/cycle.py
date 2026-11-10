"""Single-shot public-source capture and paired KBL handicap paper settlement."""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time

import requests

from research.kbo_forward.locking import single_writer
from research.kbo_forward.prices import _moment
from . import sources

MODELS = ("frozen", "static", "phase_rest")
MAX_BODY = 4 * 1024 * 1024
MAX_REQUESTS = 9


class TransientSourceError(ValueError):
    """A temporary upstream HTTP failure."""


RECOVERABLE_SOURCE_ERRORS = (
    TransientSourceError, requests.ConnectionError, requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def now():
    return datetime.now(timezone.utc)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def json_body(obj):
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode()


def fetcher(state, session, clock):
    calls, last = 0, 0.0

    def fetch(url, *, query=None, payload=None):
        nonlocal calls, last
        if calls >= MAX_REQUESTS:
            raise ValueError("KBL research request cap")
        if (state / "STOP_403_429.json").exists():
            raise ValueError("KBL research source access is paused")
        time.sleep(max(0.0, 1.1 - (time.monotonic() - last)))
        last = time.monotonic()
        calls += 1
        started = clock()
        headers = sources.KBL_HEADERS if query is not None else sources.BETMAN_HEADERS
        deadline = last + 14
        with session.request("GET" if query is not None else "POST", url, params=query, json=payload,
                             headers=headers, timeout=(4, 6), stream=True, allow_redirects=False) as response:
            if response.status_code in (403, 429):
                write(state / "STOP_403_429.json", json_body({
                    "url": url, "status": response.status_code, "at": clock().isoformat(),
                    "retry_after": response.headers.get("Retry-After"),
                }))
                raise ValueError(f"KBL research source HTTP {response.status_code}; paused")
            if response.status_code == 408 or 500 <= response.status_code < 600:
                raise TransientSourceError(f"KBL research source HTTP {response.status_code}")
            if response.status_code != 200:
                raise ValueError(f"KBL research source HTTP {response.status_code}")
            data = bytearray()
            for chunk in response.iter_content(65536):
                data.extend(chunk)
                if len(data) > MAX_BODY:
                    raise ValueError("KBL research response exceeds size limit")
                if time.monotonic() > deadline:
                    raise requests.Timeout("KBL research response exceeds time limit")
        received = clock()
        if received < started:
            raise ValueError("Clock moved backwards during source capture")
        body = bytes(data)
        sha = digest(body)
        return body, {"url": url, "query": query, "payload": payload, "status": 200, "sha256": sha,
                      "requested_at": started.isoformat(), "received_at": received.isoformat(),
                      "body_file": f"evidence/{sha}.body"}

    return fetch


def preserve(state, record):
    body, receipt = record
    path = state / receipt["body_file"]
    if digest(body) != receipt["sha256"]:
        raise ValueError("KBL response body failed its receipt check")
    if path.exists():
        if digest(path.read_bytes()) != receipt["sha256"]:
            raise ValueError("KBL evidence file has changed")
    else:
        write(path, body)


def records(state, name):
    return {path.stem: json.loads(path.read_bytes()) for path in sorted((state / name).glob("*.json"))}


def counts(predictions, settlements):
    if not settlements.keys() <= predictions.keys():
        raise ValueError("KBL settlement lacks a paired prediction")
    models = {}
    for name in MODELS:
        rows = [x["models"][name] for x in settlements.values()]
        decided = sum(r["hit"] is not None for r in rows)
        hits = sum(r["hit"] is True for r in rows)
        net = round(math.fsum(r["net"] for r in rows), 10)
        models[name] = {"hits": hits, "decided": decided, "net_units": net,
                        "accuracy": hits / decided if decided else None,
                        "roi": net / len(rows) if rows else None}
    return {"predictions": len(predictions), "settled": len(settlements),
            "pending": len(predictions) - len(settlements), "models": models}


def eligible(game, at):
    return game["state"] == "scheduled" and timedelta(minutes=30) < _moment(game["kickoff"]) - at <= timedelta(hours=24)


def freeze(state, game, quote, evidence, clock, predict):
    path = state / "predictions" / (game["id"] + ".json")
    if path.exists():
        return None
    started = clock()
    observed = _moment(quote["received_at"])
    cutoff = min(_moment(game["kickoff"]) - timedelta(minutes=30), _moment(quote["close_at"]),
                 _moment(quote["round_close_at"]))
    if not observed <= started < cutoff or started - observed > timedelta(minutes=10):
        return None
    models = predict(game, quote)
    if set(models) != set(MODELS) or any(
        p["selection"] not in ("home", "away") or p["odds"] != quote["odds"][p["selection"] == "away"]
        for p in models.values()
    ):
        raise ValueError("KBL forecast terms differ across models")
    for record in evidence.values():
        preserve(state, record)
    frozen = clock()
    if not started <= frozen < cutoff - timedelta(seconds=1):
        return None
    out = {"protocol": "kbl-handicap-forward-v1", "game": game, "quote": quote, "models": models,
           "frozen_at": frozen.isoformat(), "evidence": {k: v[1] for k, v in evidence.items()}}
    write(path, json_body(out))
    return out


def cycle(state, session, clock=now):
    from .models import forecasts
    from .settlement import settle

    predictions, settled = records(state, "predictions"), records(state, "settlements")
    pending = [r for gid, r in predictions.items() if gid not in settled]
    at = clock()
    today = at.astimezone(sources.KST).date()
    # A full current season supplies the true prior game even across league breaks.
    season_year = today.year if today.month >= 10 else today.year - 1
    first = today.replace(year=season_year, month=10, day=1)
    if pending:
        first = min(first, min(_moment(r["game"]["kickoff"]).astimezone(sources.KST).date() for r in pending))
    fetch = fetcher(state, session, clock)
    schedule = fetch(sources.KBL_URL, query={"fromDate": first.strftime("%Y%m%d"),
                     "toDate": (today + timedelta(days=1)).strftime("%Y%m%d"), "tcodeList": "all"})
    games = sources.schedule(schedule[0])
    candidates = [g for g in games.values() if g["id"] not in predictions and eligible(g, clock())]
    need_result = [
        r for r in pending
        if r["game"]["id"] in games and (
            games[r["game"]["id"]]["state"] == "final" or at >= _moment(r["game"]["kickoff"]) + timedelta(hours=2)
        )
    ]
    detail_cache = {}

    def detail(round_id):
        if round_id not in detail_cache:
            try:
                detail_cache[round_id] = fetch(sources.DETAIL_URL, payload={
                    **sources.ROUND, "gmId": "G101", "gmTs": round_id, "gameYear": "20" + round_id[:2],
                })
            except RECOVERABLE_SOURCE_ERRORS:
                # A failed attempt consumes the budget and is retried next cycle.
                detail_cache[round_id] = None
                raise
        return detail_cache[round_id]

    report = {"candidates": len(candidates), "captured": [], "settled_now": [], "ambiguous_offers": []}
    if candidates:
        matched = []
        sale_round = None
        try:
            listing = fetch(sources.LIST_URL, payload=sources.ROUND)
            active = sources.rounds(listing[0], _moment(listing[1]["received_at"]))
            for sale_round, round_info in sorted(active.items()):
                price = detail(sale_round)
                matched.extend((g, q, price) for g, q in sources.quotes(
                    price[0], sale_round, round_info, candidates, price[1]))
        except RECOVERABLE_SOURCE_ERRORS as exc:
            # An incomplete scan cannot prove that an offer is unique.
            matched.clear()
            report["capture_error"] = {
                "stage": "listing" if sale_round is None else "detail",
                "round": sale_round, "reason": f"{type(exc).__name__}: {exc}",
            }
        by_fixture = Counter(g["id"] for g, _, _ in matched)
        for g, q, price in matched:
            if by_fixture[g["id"]] != 1:
                report["ambiguous_offers"].append(q["offer_id"])
                continue
            record = freeze(state, g, q, {"schedule": schedule, "listing": listing, "price": price}, clock, forecasts)
            if record:
                predictions[g["id"]] = record
                report["captured"].append({"id": g["id"], "offer_id": q["offer_id"]})
    for record in sorted(need_result, key=lambda r: r["game"]["kickoff"], reverse=True):
        gid = record["game"]["id"]
        number = record["quote"]["offer_id"].split(":")[1]
        if number not in detail_cache and len(detail_cache) >= MAX_REQUESTS - 2:
            continue
        try:
            result = detail(number)
        except RECOVERABLE_SOURCE_ERRORS as exc:
            report.setdefault("settlement_errors", {})[number] = f"{type(exc).__name__}: {exc}"
            continue
        if result is None:
            continue
        grade = settle(record, games[gid], result[0], result[1])
        if grade is not None:
            preserve(state, schedule)
            preserve(state, result)
            grade["official_kbl_receipt"] = schedule[1]
            write(state / "settlements" / (gid + ".json"), json_body(grade))
            settled[gid] = grade
            report["settled_now"].append(gid)
    return {**report, **counts(predictions, settled)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--status-only", action="store_true")
    parser.add_argument("--scheduled", action="store_true")
    args = parser.parse_args()
    if args.scheduled and not 9 <= now().astimezone(sources.KST).hour <= 23:
        return
    state = args.state.resolve()
    with single_writer(state / "cycle.lock") as owned:
        if not owned:
            return
        if args.status_only:
            out = counts(records(state, "predictions"), records(state, "settlements"))
        else:
            with requests.Session() as session:
                out = cycle(state, session)
            write(state / "last-run.json", json_body(dict(out, ran_at=now().isoformat())))
        print(json.dumps(out, ensure_ascii=False, allow_nan=False))
        if out.get("capture_error") or out.get("settlement_errors"):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
