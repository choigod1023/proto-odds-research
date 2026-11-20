"""Bounded Miky938 research capture; never imports production inference code.

Run from the repository root. Raw public responses and the request ledger live
under the explicitly scoped research directory. No retries, auth or bypasses.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/tipster-roi-expansion-20261028/kbl"
BASE = "https://miky938-oddsparser.com"
LEAGUES = {"KBL": "/league/south-korea-kbl/", "NBA": "/league/usa-nba/"}
MAX_REQUESTS = 28  # plus two initial web-tool reads = 30 total task requests
OFFICIAL_URL = "https://api.kbl.or.kr/match/list?fromDate=20260305&toDate=20261028&tcodeList=all"
PUBLIC_KBL_HEADERS = {"Channel": "WEB", "TeamCode": "XX", "X-Requested-With": "XMLHttpRequest", "lang": "ko"}
KBL_CODES = {
    "Goyang Sono": "66", "Anyang JungKwanJang": "70", "Wonju DB": "16",
    "KCC Egis": "60", "Suwon KT": "06", "Seoul Knights": "55",
    "Mobis Phoebus": "10", "KoGas": "64", "LG Sakers": "50",
    "Seoul Thunders": "35",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch(url, name, headers=None):
    ledger_path = OUT / "requests.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else []
    if len(ledger) >= MAX_REQUESTS:
        raise RuntimeError("HTTP request budget exhausted")
    if any(item["url"] == url for item in ledger):
        raise RuntimeError("Already attempted URL; reuse its saved response")
    item = {"url": url, "observed_at": now(), "automatic_retries": 0}
    if headers:
        item["public_request_headers"] = headers
    ledger.append(item)
    dump(ledger_path, ledger)  # failed attempts count, too
    try:
        response = requests.get(url, timeout=12, allow_redirects=False, headers=headers)
        item.update(status=response.status_code, content_type=response.headers.get("Content-Type"))
        raw = OUT / "raw" / name
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(response.content)
        item.update(raw_path=str(raw.relative_to(ROOT)).replace("\\", "/"),
                    sha256=hashlib.sha256(response.content).hexdigest(), bytes=len(response.content))
        response.raise_for_status()
        if 300 <= response.status_code < 400:
            raise RuntimeError("Redirect recorded, not automatically followed")
    except Exception as exc:
        item["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        dump(ledger_path, ledger)
    return item


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def text_at(row, selector):
    node = row.select_one(selector)
    if node is None:
        raise ValueError(f"Missing column {selector}")
    return node.get_text(" ", strip=True)


def parse_page(content, meta):
    """Keep EVERY KBL/NBA row, including losses and unresolved observations."""
    soup = BeautifulSoup(content, "html.parser")
    records = []
    excluded = Counter()
    for ordinal, row in enumerate(soup.select("tbody tr"), 1):
        league_node = row.select_one("td.game .league")
        label = league_node.get_text(" ", strip=True) if league_node else ""
        league = next((key for key, path in LEAGUES.items()
                       if league_node and league_node.get("href") == path), None)
        if league is None:
            excluded[f"other_league:{label or 'missing_label'}"] += 1
            continue
        game = row.select_one("td.game")
        # The direct text is the fixture; child nodes contain league/analysis.
        fixture = " ".join(str(t).strip() for t in game.find_all(string=True, recursive=False)).strip()
        fixture = fixture.lstrip("🏀• \t\n")
        if fixture.count(" - ") != 1:
            raise ValueError(f"Ambiguous fixture: {fixture}")
        home, away = fixture.split(" - ")
        date_node = row.select_one("td.when a[href]")
        if date_node:
            daily_url = urljoin(BASE, date_node["href"])
        else:
            daily_url = meta["url"]
        date_match = re.search(r"/tips/(\d{4}-\d{2}-\d{2})/", daily_url)
        if not date_match:
            raise ValueError("Missing date-specific URL")
        market = text_at(row, "td.market")
        line_match = re.match(r"(?:O/U|AH)\s+([+-]?\d+(?:\.\d+)?)", market)
        side = text_at(row, "td.pick")
        odds_text = text_at(row, "td.odds")
        odds = float(odds_text)
        if odds <= 1:
            raise ValueError("Invalid decimal odds")
        result = text_at(row, "td.res .badge")
        result_raw = text_at(row, "td.res")
        score_match = re.search(r"(\d+)\s*:\s*(\d+)", result_raw)
        score = dict(zip(("home", "away"), map(int, score_match.groups()))) if score_match else None
        profit_text = text_at(row, "td.profit")
        profit = float(profit_text) if profit_text else None
        time_node = row.select_one("td.when time[data-ts], td.when a[data-day-ts]")
        game_ts = int(time_node.get("data-ts", time_node.get("data-day-ts"))) if time_node else None
        record = {
            "source": "Miky938 Tips", "author": "Miky938",
            "author_basis": "publisher_handle_not_independently_identified_human",
            "source_url": daily_url, "league": league, "sport": "basketball",
            "match_date": date_match.group(1),
            "match_date_basis": "source_date_specific_page_url_not_publication_date",
            "teams": {"home": home, "away": away},
            "raw_fixture_text": fixture, "source_league_label": label,
            "market": market, "line": float(line_match.group(1)) if line_match else None,
            "side": side.lower(), "side_raw": side,
            "market_scope": "full_game_including_overtime" if market == "H/A, OT" else "unverified",
            "reported_decimal_odds": odds, "reported_decimal_odds_raw": odds_text,
            "price_evidence_tier": "retrospective_listed_price_claim",
            "reported_bookmaker": text_at(row, "td.book"),
            "source_reported_result": result, "source_reported_result_raw": result_raw,
            "source_reported_score": score,
            "source_reported_profit": profit, "source_reported_profit_raw": profit_text or None,
            "source_reported_profit_unit": "units_per_one_unit_stake_unverified",
            "official_result": None, "official_result_provenance": None,
            "source_reported_published_at": None, "cutoff_proven": False,
            "observed_at": meta["observed_at"],
            "source_reported_game_timestamp": game_ts,
            "source_reported_game_time_utc": datetime.fromtimestamp(game_ts, timezone.utc).isoformat() if game_ts else None,
            "timestamp_role": "game_time_not_publication_time",
            "source_display_timezone": "WAT (UTC+01:00)",
            "page_url_may_change_after_observation": True,
            "evidence": [{"source_url": meta["url"], "raw_path": meta["raw_path"],
                          "fact_table_path": str((OUT / "source-observations.jsonl").relative_to(ROOT)).replace("\\", "/"),
                          "sha256": meta["sha256"], "table_row_ordinal": ordinal,
                          "observed_at": meta["observed_at"]}],
        }
        identity = [league, record["match_date"], home, away, market, record["line"], side.lower(), odds_text]
        record["candidate_id"] = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
        records.append(record)
    return records, {"url": meta["url"], "raw_rows": len(soup.select("tbody tr")),
                     "matching_rows": len(records), "exclusions": dict(excluded)}


def deduplicate(records):
    selected = {}
    conflicts = []
    fields = ("source_reported_result", "source_reported_score", "source_reported_profit")
    for row in records:
        key = row["candidate_id"]
        if key not in selected:
            selected[key] = row
            continue
        prior = selected[key]
        differences = {f: [prior[f], row[f]] for f in fields if prior[f] != row[f]}
        if differences:
            conflicts.append({"candidate_id": key, "differences": differences,
                              "evidence": prior["evidence"] + row["evidence"]})
        # Prefer detailed day-page observation; preserve every raw observation separately.
        if "/tips/" in row["evidence"][0]["source_url"]:
            row["evidence"] = prior["evidence"] + row["evidence"]
            row["observed_at"] = min(row["observed_at"], prior["observed_at"])
            selected[key] = row
        else:
            prior["evidence"].extend(row["evidence"])
    for row in selected.values():
        row["source_observation_conflict"] = any(c["candidate_id"] == row["candidate_id"] for c in conflicts)
    return sorted(selected.values(), key=lambda r: (r["league"], r["match_date"], r["candidate_id"])), conflicts


def official_sidecar(meta):
    raw = json.loads((ROOT / meta["raw_path"]).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Official KBL response is not a match array")
    result = []
    seen = set()
    for row in raw:
        day = datetime.strptime(row["gameDate"], "%Y%m%d").date().isoformat()
        if not "2026-03-05" <= day <= "2026-10-28":
            raise ValueError("Official match outside requested bounds")
        if row["gmkey"] in seen:
            raise ValueError("Duplicate official match id")
        seen.add(row["gmkey"])
        ended = row["isEnded"] == 1
        if ended and any(type(row[k]) is not int or row[k] < 0 for k in ("scoreH", "scoreA")):
            raise ValueError("Invalid official final score")
        result.append({
            "provider": "KBL official public API", "league": "KBL", "match_date": day,
            "official_id": row["gmkey"], "home_id": row["tcodeH"], "away_id": row["tcodeA"],
            "teams": {"home": row["tnameFH"], "away": row["tnameFA"]},
            "short_team_names": {"home": row["tnameH"], "away": row["tnameA"]},
            "is_ended": ended, "is_started": row["isStarted"],
            "home_score": row["scoreH"] if ended else None,
            "away_score": row["scoreA"] if ended else None,
            "game_start_raw": row["gameStart"], "season_code": row["seasonCode"],
            "source_url": meta["url"], "observed_at": meta["observed_at"],
            "raw_path": meta["raw_path"], "sha256": meta["sha256"],
        })
    return result


def attach_official(rows, official):
    index = {}
    for game in official:
        key = (game["match_date"], game["home_id"], game["away_id"])
        index.setdefault(key, []).append(game)
    counts = Counter()
    for row in rows:
        if row["league"] != "KBL":
            row["official_validation_status"] = "not_attempted_nba_out_of_priority_scope"
            continue
        key = (row["match_date"], KBL_CODES.get(row["teams"]["home"]), KBL_CODES.get(row["teams"]["away"]))
        candidates = index.get(key, [])
        status = "no_exact_date_home_away_match"
        if None in key:
            status = "unknown_team_alias"
        elif len(candidates) > 1:
            status = "ambiguous_official_match"
        elif candidates:
            game = candidates[0]
            row["official_result_provenance"] = {k: game[k] for k in (
                "provider", "official_id", "source_url", "observed_at", "raw_path", "sha256")}
            row["official_result_provenance"].update({
                "join_rule": "exact_source_date_and_explicit_home_away_team_codes_no_score_matching",
                "home_id": key[1], "away_id": key[2], "official_team_names": game["teams"],
            })
            if not game["is_ended"]:
                status = "official_not_ended"
            elif row["market"] != "H/A, OT" or row["side"] not in {"home", "away"}:
                status = "unsupported_market_or_side"
            else:
                h, a = game["home_score"], game["away_score"]
                if h == a:
                    raise ValueError("Ended full-game basketball has tied score")
                winner = "home" if h > a else "away"
                outcome = "Won" if row["side"] == winner else "Lost"
                row["official_result"] = {"home_score": h, "away_score": a, "winner": winner,
                                          "pick_result": outcome}
                score_matches = row["source_reported_score"] == {"home": h, "away": a}
                row["publisher_official_score_matches"] = score_matches
                row["publisher_official_outcome_matches"] = row["source_reported_result"] == outcome
                status = "verified_score_and_outcome" if score_matches and row["source_reported_result"] == outcome else "publisher_official_disagreement"
        row["official_validation_status"] = status
        counts[status] += 1
    return dict(counts)


def publisher_stats(rows):
    """Descriptive publisher-only arithmetic; not validated historical ROI."""
    settled = [r for r in rows if r["source_reported_result"] in {"Won", "Lost", "Void"}]
    profits = [r["source_reported_profit"] for r in settled]
    arithmetic_errors = []
    for row in settled:
        expected = {"Won": row["reported_decimal_odds"] - 1, "Lost": -1, "Void": 0}[row["source_reported_result"]]
        if row["source_reported_profit"] is None or abs(row["source_reported_profit"] - expected) > 1e-8:
            arithmetic_errors.append(row["candidate_id"])
    total = round(sum(x for x in profits if x is not None), 8)
    return {"label": "publisher_only_unverified_flat_one_unit_descriptive_not_validated_ROI",
            "rows": len(rows), "settled_rows": len(settled),
            "distinct_fixture_count": len({(r["match_date"], r["teams"]["home"], r["teams"]["away"]) for r in rows}),
            "market_family_counts": dict(Counter("totals" if r["market"].startswith("O/U ") else "moneyline" if r["market"] == "H/A, OT" else "other" for r in rows)),
            "results": dict(Counter(r["source_reported_result"] for r in rows)),
            "match_date_min": min((r["match_date"] for r in rows), default=None),
            "match_date_max": max((r["match_date"] for r in rows), default=None),
            "settled_match_date_max": max((r["match_date"] for r in settled), default=None),
            "source_reported_profit_sum_units": total,
            "publisher_only_profit_per_settled_stake_pct": round(100 * total / len(settled), 6) if settled else None,
            "internal_profit_arithmetic_errors": arithmetic_errors,
            "pregame_cutoff_proven_rows": sum(r["cutoff_proven"] for r in rows)}


def build():
    ledger = json.loads((OUT / "requests.json").read_text(encoding="utf-8"))
    observations = []
    pages = []
    for meta in ledger:
        if meta.get("status") == 200 and meta["url"].startswith(BASE + "/"):
            if not (ROOT / meta["raw_path"]).exists():
                continue
            rows, audit = parse_page((ROOT / meta["raw_path"]).read_bytes(), meta)
            observations.extend(rows)
            pages.append(audit)
    if not observations:
        # Git carries factual rows and audits, not third-party page bodies.
        observations = [json.loads(line) for line in (OUT / "source-observations.jsonl").read_text(encoding="utf-8").splitlines()]
        pages = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))["page_audits"]
    elif len(pages) != sum(m.get("status") == 200 and m["url"].startswith(BASE + "/") for m in ledger):
        raise ValueError("Partial raw HTML available; use either all captured pages or factual replay only")
    # Write unmerged observations before dedup modifies evidence lists.
    write_jsonl(OUT / "source-observations.jsonl", observations)
    rows, conflicts = deduplicate(observations)
    official_meta = next((m for m in ledger if m.get("status") == 200
                         and m["url"].startswith("https://api.kbl.or.kr/match/list")), None)
    official = official_sidecar(official_meta) if official_meta else []
    status = attach_official(rows, official)
    write_jsonl(OUT / "official-kbl-results.jsonl", official)
    for league in LEAGUES:
        write_jsonl(OUT / f"{league.lower()}-candidates.jsonl", [r for r in rows if r["league"] == league])
    summary = {
        "built_at": now(), "request_attempts_python": len(ledger), "initial_web_tool_reads": 2,
        "total_task_request_attempts": len(ledger) + 2, "request_cap": 30,
        "network_errors": [r for r in ledger if "error" in r],
        "source_observations": len(observations), "deduplicated_candidates": len(rows),
        "duplicate_observations_removed": len(observations) - len(rows),
        "source_observation_conflicts": conflicts,
        "page_audits": pages, "official_kbl_match_rows": len(official),
        "official_kbl_ended_rows": sum(r["is_ended"] for r in official),
        "kbl_official_validation": status,
        "leagues": {league: publisher_stats([r for r in rows if r["league"] == league]) for league in LEAGUES},
        "omissions": ["No publication timestamps or historical pregame cutoff proof",
                      "No proto odds join, operational model replay, hybrid evaluation, or adoption decision",
                      "NBA official scores not independently collected in this subtask",
                      "NBA league page renders 100 rows although publisher summary says 162; extra day-page rows are opportunistic",
                      "KBL league page renders only 18 settled rows; 100+ KBL target unavailable on exposed archive",
                      "Other leagues excluded by explicit league link, irrespective of outcome"],
    }
    dump(OUT / "summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("total_task_request_attempts", "source_observations",
                     "deduplicated_candidates", "kbl_official_validation", "leagues")}, ensure_ascii=False, indent=2))


def collect():
    """Deterministic bounded source expansion; never chase winning rows."""
    ledger = json.loads((OUT / "requests.json").read_text(encoding="utf-8")) if (OUT / "requests.json").exists() else []
    attempted = {m["url"] for m in ledger}
    for league, path in LEAGUES.items():
        if BASE + path not in attempted:
            fetch(BASE + path, f"{league.lower()}-league.html")
    soup = BeautifulSoup((OUT / "raw/kbl-league.html").read_bytes(), "html.parser")
    for path in sorted({a["href"] for a in soup.select("td.when a[href]")}):
        url = urljoin(BASE, path)
        if url not in attempted:
            fetch(url, f"tips-{path.rstrip('/').split('/')[-1]}.html")


def archive_public_client():
    """Keep the exact public API header evidence and compress the large client."""
    path = OUT / "raw/official-kbl-app.js"
    if not path.exists():
        return
    body = path.read_bytes()
    client = body.decode("utf-8")
    excerpts = {}
    for term, length in (("const init=async()=>", 1100), ("getTeamCodeByDomain=", 650),
                         ("getMatchList=async", 130)):
        start = client.index(term)
        excerpts[term] = {"character_offset": start, "inspected_characters": length}
    dump(OUT / "official-public-client-evidence.json", {
        "source_url": "https://www.kbl.or.kr/assets/index-7BTiI5o7.js",
        "uncompressed_sha256": hashlib.sha256(body).hexdigest(),
        "inspected_public_client_locations": excerpts, "used_public_headers": PUBLIC_KBL_HEADERS,
        "authentication": "none; no access token, cookies or login used",
    })
    compressed = path.with_suffix(".js.gz")
    compressed.write_bytes(gzip.compress(body, mtime=0))
    assert gzip.decompress(compressed.read_bytes()) == body
    ledger_path = OUT / "requests.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    for meta in ledger:
        if meta.get("raw_path") == str(path.relative_to(ROOT)).replace("\\", "/"):
            meta["raw_path"] = str(compressed.relative_to(ROOT)).replace("\\", "/")
            meta["raw_encoding"] = "gzip; sha256 and bytes describe uncompressed HTTP body"
    dump(ledger_path, ledger)
    path.unlink()  # Only this task's exact captured file, after lossless verification.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", nargs=2, metavar=("URL", "FILENAME"))
    parser.add_argument("--collect", action="store_true", help="Capture league pages and all KBL-linked days")
    parser.add_argument("--build", action="store_true", help="Offline parsing and independent score matching")
    parser.add_argument("--collect-official", action="store_true", help="Use documented public WEB headers, without auth")
    parser.add_argument("--archive-public-client", action="store_true", help="Losslessly compress captured public JS and save header evidence")
    args = parser.parse_args()
    if args.fetch:
        print(json.dumps(fetch(*args.fetch), ensure_ascii=False))
    if args.collect:
        collect()
    if args.collect_official:
        ledger = json.loads((OUT / "requests.json").read_text(encoding="utf-8")) if (OUT / "requests.json").exists() else []
        if not any(m["url"] == OFFICIAL_URL for m in ledger):
            print(json.dumps(fetch(OFFICIAL_URL, "official-kbl-2026-public-headers.json", PUBLIC_KBL_HEADERS)))
    if args.archive_public_client:
        archive_public_client()
    if args.build:
        build()


if __name__ == "__main__":
    main()
