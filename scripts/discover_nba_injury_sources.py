"""Discover bounded official injury PDF URLs; no model, ledger, or scheduler writes.

Reuse the existing HTTP capture and immutable cache receipts. An index link is
not a captured PDF or a verified pregame fact. In particular, a historical
season URL redirecting to today's season must never become historical data.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect_basketball_comparison_sources import capture

SEASON = re.compile(r"(20\d{2})-(\d{2})")
REPORT = re.compile(r"/referee/injury/Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})_(\d{2})(AM|PM)\.pdf")


def validate_season(value):
    match = SEASON.fullmatch(value)
    if not match or (int(match[1])+1) % 100 != int(match[2]):
        raise ValueError("invalid_season")
    return value


def source_clock(url):
    parts = urlsplit(url)
    if (parts.scheme, parts.netloc) != ("https", "ak-static.cms.nba.com") or parts.query or parts.fragment:
        raise ValueError("not_official_injury_pdf")
    match = REPORT.fullmatch(parts.path)
    if not match:
        raise ValueError("not_official_injury_pdf")
    day, hour, minute, half = match.groups()
    if not 1 <= int(hour) <= 12 or not 0 <= int(minute) < 60:
        raise ValueError("invalid_source_clock")
    actual_hour = int(hour) % 12 + (12 if half == "PM" else 0)
    naive = datetime.combine(date.fromisoformat(day), datetime.min.time()).replace(hour=actual_hour, minute=int(minute))
    eastern = ZoneInfo("America/New_York")
    first, second = naive.replace(tzinfo=eastern, fold=0), naive.replace(tzinfo=eastern, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError("ambiguous_or_nonexistent_source_clock")
    return first.astimezone(timezone.utc)


def discover(meta, body, *, season, target_date=None, max_reports=32):
    validate_season(season)
    if type(max_reports) is not int or not 1 <= max_reports <= 64:
        raise ValueError("invalid_report_limit")
    if target_date is not None:
        date.fromisoformat(target_date)
    if meta.get("status") != 200:
        raise ValueError("index_http_not_success")
    if not body or hashlib.sha256(body).hexdigest() != meta.get("body_sha256"):
        raise ValueError("index_receipt_hash_mismatch")
    received = datetime.fromisoformat(meta["received_at"].replace("Z", "+00:00"))
    if received.utcoffset() is None:
        raise ValueError("receipt_timezone_required")
    expected_path = f"/nba-injury-report-{season}-season"
    for field in ("url", "final_url"):
        parts = urlsplit(meta.get(field) or "")
        if (parts.scheme, parts.netloc) != ("https", "official.nba.com") or parts.path.rstrip("/") != expected_path:
            raise ValueError("season_or_index_redirect_mismatch")
    soup = BeautifulSoup(body, "html.parser")
    seasons = set()
    for heading in soup.find_all(["h1", "h2"]):
        match = re.search(r"NBA Injury Report:\s*(20\d{2}-\d{2})\s+Season", heading.get_text(" ", strip=True))
        if match:
            seasons.add(match[1])
    if seasons != {season}:
        raise ValueError("index_content_season_mismatch")
    reports = {}
    rejected = []
    for link in soup.find_all("a", href=True):
        url = urljoin(meta["final_url"], link["href"])
        if "/referee/injury/" not in url:
            continue
        try:
            clock = source_clock(url)
        except ValueError as exc:
            rejected.append(str(exc))
            continue
        document_day = REPORT.fullmatch(urlsplit(url).path)[1]
        if target_date is not None and document_day != target_date:
            continue
        if int(document_day[:4]) not in {int(season[:4]), int(season[:4])+1}:
            rejected.append("document_year_outside_season")
            continue
        # An index may expose a malformed/future filename. Do not queue it as available.
        if clock > received:
            rejected.append("source_clock_after_index_receipt")
            continue
        reports[url] = dict(url=url, report_date_eastern_claim=document_day,
                            published_at_claim=clock.isoformat(), source_clock_basis="official_pdf_filename_claim",
                            pdf_body_captured=False, player_ids_verified=False, training_eligible=False)
    ordered = sorted(reports.values(), key=lambda r: (r["published_at_claim"], r["url"]), reverse=True)
    return dict(schema="nba-injury-source-discovery-v1", season=season, target_date=target_date,
                index_receipt={k: meta.get(k) for k in ("url", "final_url", "status", "received_at", "body_sha256", "bytes")},
                status="links_discovered" if ordered else "no_matching_report_links",
                discovered=len(ordered), returned=min(len(ordered), max_reports), reports=ordered[:max_reports],
                rejected=rejected, historical_coverage_proven=False, affects_probability=False, training_eligible=False,
                limits=["Index receipt is not the PDF receipt or independent proof of its publication time",
                        "Missing historical links do not prove historical reports do not exist",
                        "Use explicit PDF body capture, player/game identity and actual T-30 receipts before pregame eligibility"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", required=True, type=validate_season)
    parser.add_argument("--date", help="Eastern report date YYYY-MM-DD; not KST game date")
    parser.add_argument("--max-reports", type=int, default=32)
    parser.add_argument("--receipt", type=Path, help="Offline existing collector JSON receipt; sibling .body.gz required")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--capture-id", default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.receipt:
        meta = json.loads(args.receipt.read_text(encoding="utf-8-sig"))
        body = gzip.decompress(args.receipt.with_suffix(".body.gz").read_bytes())
    else:
        if args.evidence is None:
            parser.error("--evidence is required for an HTTP capture")
        capture_id = args.capture_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", capture_id):
            parser.error("invalid capture ID")
        url = f"https://official.nba.com/nba-injury-report-{args.season}-season/"
        meta, body = capture(f"nba-index-{args.season}-{capture_id}", url, args.evidence)
    try:
        result = discover(meta, body, season=args.season, target_date=args.date, max_reports=args.max_reports)
    except ValueError as exc:
        result = dict(schema="nba-injury-source-discovery-v1", status="blocked", reason=str(exc),
                      requested_season=args.season, index_receipt=meta, reports=[], training_eligible=False)
        # Raw response bodies remain in the collector's local cache, not the public result.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf8")
    print(json.dumps({k: result[k] for k in ("status", "reason", "discovered", "returned") if k in result}))
    return 2 if result["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
