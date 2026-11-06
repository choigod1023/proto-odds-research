"""Keep full public article bytes; link only explicit pregame fixture/product matches."""
import argparse
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.public_tipster_votes import safe_url, parse_page, match_vote
from scripts.run_pregame_multisport_study import read_records

LISTINGS = tuple(f"https://pick79.com/posts/{s}/" for s in ("soccer", "baseball", "basketball")) + ("https://www.goli.tv/board/analysis",)
MAX_LISTING_BYTES = 6_000_000
MAX_ARTICLE_BYTES = 2_000_000


def now():
    return datetime.now(timezone.utc).isoformat()


def capture(session, url, root, capture_id, budget):
    if url not in LISTINGS and not safe_url(url):
        raise ValueError("unregistered_public_source")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", capture_id):
        raise ValueError("unsafe_capture_id")
    name = hashlib.sha256(url.encode()).hexdigest()[:24] + "-" + capture_id
    path = root / (name + ".json")
    if path.exists():
        meta = json.loads(path.read_bytes())
        if meta["url"] != url:
            raise ValueError("cached_source_identity_changed")
        if not meta.get("body_sha256"):
            return meta, None
        raw = gzip.decompress((root / meta["body_file"]).read_bytes())
        if hashlib.sha256(raw).hexdigest() != meta["body_sha256"]:
            raise ValueError("cached_article_bytes_changed")
        return meta, raw.decode(meta["encoding"], errors="replace")
    if budget["used"] >= budget["limit"] or budget.get("blocked"):
        raise StopIteration
    budget["used"] += 1
    requested = now()
    meta = dict(url=url, requested_at=requested, received_at=None, body_sha256=None, status=None)
    raw, text = None, None
    try:
        began = time.monotonic()
        with session.get(url, timeout=(5, 12), allow_redirects=False, stream=True) as response:
            meta["status"] = response.status_code
            # Do not follow arbitrary redirects. Only same-origin trailing-slash
            # canonicalization is a separate permitted, budgeted request.
            parts = urlsplit(url)
            location = urljoin(url, response.headers.get("Location", ""))
            if response.status_code in (301, 308) and urlsplit(location).netloc == parts.netloc and location == url.rstrip("/") + "/":
                meta["canonical_url"] = location
            if response.status_code in (403, 429):
                budget["blocked"] = True
                stop_file = budget.get("stop_file")
                if stop_file is not None:
                    stop_file.write_text(json.dumps(meta) + "\n", encoding="utf-8")
            if response.status_code != 200:
                raise ValueError("http_" + str(response.status_code))
            body = bytearray()
            limit = MAX_LISTING_BYTES if url in LISTINGS else MAX_ARTICLE_BYTES
            for chunk in response.iter_content(16384):
                body.extend(chunk)
                if len(body) > limit or time.monotonic() - began > 25:
                    raise ValueError("article_body_or_total_time_cap")
            raw = bytes(body)
            meta.update(received_at=now(), body_sha256=hashlib.sha256(raw).hexdigest(),
                        timestamp_basis="http_response_body_completed", encoding=response.encoding or "utf-8",
                        body_file=name + ".body.gz", bytes=len(raw))
            text = raw.decode(meta["encoding"], errors="replace")
    except (requests.RequestException, ValueError) as exc:
        meta.update(error=type(exc).__name__ + ":" + str(exc)[:160], failed_at=now())
    root.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (root / meta["body_file"]).write_bytes(gzip.compress(raw, mtime=0))
    path.write_bytes((json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode())
    return meta, text


def link(article, meta, prices):
    lookup = {}
    for price in prices:
        p = price.get("payload") or {}
        if price.get("kind") != "price" or p.get("settlement_rule") in (None, "unknown"):
            continue
        start = datetime.fromisoformat(price["kickoff"].replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=9)))
        game = dict(sport=price["sport"], league=price["league"], home=price["home"], away=price["away"],
                    year=start.year, date=start.strftime("%m.%d") + "(" + "월화수목금토일"[start.weekday()] + ") " + start.strftime("%H:%M"))
        key = json.dumps(game, sort_keys=True)
        lookup.setdefault(key, {}).setdefault(p["product_id"], price)
    event = match_vote(article, [json.loads(key) for key in lookup])
    matches = [p for products in lookup.values() for p in products.values() if
               [p["sport"], p["league"], p["home"], p["away"]] == event["fixture"][:4]
               and datetime.fromisoformat(p["kickoff"].replace("Z", "+00:00")) == datetime.fromisoformat(event["fixture"][4])]
    if len(matches) != 1:
        raise ValueError("ambiguous_proto_product_for_article")
    price = matches[0]
    product = price["payload"]
    observation = hashlib.sha256(json.dumps([event["pick_id"], meta["body_sha256"], meta["received_at"]], separators=(",", ":")).encode()).hexdigest()[:24]
    return dict(record_id="tipster:" + observation, kind="tipster", sport=price["sport"], league=price["league"],
                home=price["home"], away=price["away"], kickoff=price["kickoff"], source_url=meta["url"],
                body_sha256=meta["body_sha256"], received_at=meta["received_at"],
                timestamp_basis=meta["timestamp_basis"], timing_basis="actual_receipt", published_at=article.get("published_at"),
                payload=dict(product_id=product["product_id"], period=product["period"], market=product["market"], line=product.get("line"),
                             settlement_rule=product["settlement_rule"], author=event["voter"],
                             raw_text=article["selection_text"], selection={"홈":"home","무":"draw","원정":"away"}[event["side"]],
                             source_parser="public_fullgame_vote_v1", source_pick_id=event["pick_id"], source_html_utf8_sha256=article["content_hash"]))


def run(args, *, listings=LISTINGS, articles_per_listing=1, stop_file=None):
    if not 1 <= args.max_requests <= 12:
        raise ValueError("request_budget_must_be_1_to_12")
    args.output.mkdir(parents=True, exist_ok=False)
    budget = dict(used=0, limit=args.max_requests, stop_file=stop_file)
    attempts, articles, records, links = [], [], [], []
    prices = [r for p in args.prices for r in read_records(p)]
    with requests.Session() as session:
        session.headers["User-Agent"] = "proto-odds-research/1.0 public-article-preservation"
        for url in listings:
            try:
                meta, text = capture(session, url, args.evidence, args.capture_id, budget)
                attempts.append(meta)
                if text:
                    soup = BeautifulSoup(text, "html.parser")
                    found = list(dict.fromkeys(urljoin(url, a["href"]) for a in soup.select("a[href]") if safe_url(urljoin(url,a["href"]))))
                    links.extend(found[:articles_per_listing])
            except StopIteration:
                break
        for url in dict.fromkeys(links):
            try:
                meta, text = capture(session, url, args.evidence, args.capture_id, budget)
                attempts.append(meta)
                if text is None and meta.get("canonical_url"):
                    meta, text = capture(session, meta["canonical_url"], args.evidence, args.capture_id, budget)
                    attempts.append(meta)
                if text is None:
                    continue
                article = parse_page(meta["url"], text, meta["received_at"])
                item = dict(article, raw_body_sha256=meta["body_sha256"], status="captured_unlinked")
                try:
                    records.append(link(article, meta, prices)); item["status"] = "matched_pregame"
                except (ValueError, KeyError, StopIteration) as exc:
                    item["hold_reason"] = str(exc)
                articles.append(item)
            except StopIteration:
                break
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                articles.append(dict(url=url, status="unsupported_source_article", reason=str(exc)))
    for name, value in (("requests", attempts), ("articles", articles), ("records", records)):
        (args.output / (name + ".json")).write_bytes((json.dumps(value, ensure_ascii=False, indent=2)+"\n").encode())
    summary = dict(http_requests=budget["used"], parsed_articles=sum("author" in a for a in articles),
                   linked_pregame_votes=len(records), access_blocked=budget.get("blocked", False),
                   whole_raw_html_preserved=True, timestamp_basis="http_response_body_completed", schedules_activated=False)
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--capture-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    p.add_argument("--max-requests", type=int, default=8)
    p.add_argument("--prices", type=Path, action="append", default=[])
    print(json.dumps(run(p.parse_args()), ensure_ascii=False))
