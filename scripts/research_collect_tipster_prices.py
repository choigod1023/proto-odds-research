"""Bounded, read-only public HTTP collection; never loads env files or runtime DB.

Archive quotes are retrospective, NOT certified pregame observations.
Run `round YEAR ROUND`, `get URL`, `kbo-september`, `kbo-day`, `build`, or `selftest`.
All source bytes and HTTP metadata live under the explicit research directory.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/tipster-roi-expansion-20261028/prices"
BASE = "https://www.wisetoto.com"
KST = timezone(timedelta(hours=9))
LIMIT = 25
ROUND_LIMIT = 10
INTERVAL = 2.5
SEQ = re.compile(r"get_gameinfo_body\(\s*'proto'\s*,\s*'pt1'\s*,\s*'?(\d{4})'?\s*,\s*'(\d+)'\s*,\s*''\s*,\s*''\s*,\s*'(\d+)'")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class Collector:
    def __init__(self):
        OUT.mkdir(parents=True, exist_ok=True)
        self.path = OUT / "manifest.json"
        self.manifest = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {
            "schema": "tipster-primary-prices-v1", "started_at_utc": utcnow(),
            "request_limit": LIMIT, "round_limit": ROUND_LIMIT, "minimum_interval_seconds": INTERVAL,
            "retries": 0, "redirects": False, "database_access": False, "env_file_access": False,
            "quote_quality": "retrospective_archive_not_pregame_certified",
            "requests": [], "rounds": [],
        }
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": "PublicHistoricalOddsResearch/1.0", "Referer": BASE + "/index.htm",
                                     "X-Requested-With": "XMLHttpRequest"})

    def save(self):
        self.manifest["request_count"] = len(self.manifest["requests"])
        write_json(self.path, self.manifest)

    def get(self, url, purpose):
        return self.request("GET", url, purpose)

    def request(self, method, url, purpose, data=None):
        if urlparse(url).hostname not in {"www.wisetoto.com", "www.koreabaseball.com"}:
            raise ValueError("Only the task's public source hosts are allowed")
        if urlparse(url).scheme != "https" or method not in {"GET", "POST"}:
            raise ValueError("Only HTTPS GET or public read-only POST is allowed")
        if method == "POST" and url not in {"https://www.koreabaseball.com/ws/Schedule.asmx/GetScheduleList", "https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList"}:
            raise ValueError("POST must be a known public read-only results endpoint")
        requests_log = self.manifest["requests"]
        if any(r["url"] == url and r["method"] == method and r.get("form_data") == data for r in requests_log):
            raise RuntimeError("Request already attempted; no retries")
        if urlparse(url).hostname == "www.wisetoto.com" and any(r.get("status") == 403 and urlparse(r["url"]).hostname == "www.wisetoto.com" for r in requests_log):
            raise RuntimeError("Wisetoto blocked; collection stopped without bypass")
        if len(requests_log) >= LIMIT:
            raise RuntimeError("Hard HTTP request budget exhausted")
        if requests_log:
            elapsed = time.time() - requests_log[-1]["started_unix"]
            time.sleep(max(0, INTERVAL - elapsed))
        record = {"request_id": len(requests_log) + 1, "url": url, "purpose": purpose,
                  "method": method, "form_data": data, "started_at_utc": utcnow(), "started_unix": time.time()}
        requests_log.append(record)
        self.save()  # count even transport failures, with no implicit retry
        try:
            headers = {"Referer": "https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx"} if urlparse(url).hostname == "www.koreabaseball.com" else {}
            record["request_headers"] = {**dict(self.session.headers), **headers}
            response = self.session.request(method, url, data=data, headers=headers, timeout=(15, 40), allow_redirects=False)
            raw = response.content
            name = f"source-{record['request_id']:02d}.bin"
            (OUT / name).write_bytes(raw)
            record.update(status=response.status_code, response_headers=dict(response.headers),
                          sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), body_file=name,
                          finished_at_utc=utcnow())
            self.save()
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError(f"Non-200 response: {response.status_code}; no redirect followed")
            html, encoding = decode(raw)
            record["decoded_encoding"] = encoding
            self.save()
            return html, record
        except Exception as exc:
            record["error"] = str(exc)
            self.save()
            raise

    def collect_round(self, year, rnd):
        if any(r.get("status") == 403 and urlparse(r["url"]).hostname == "www.wisetoto.com" for r in self.manifest["requests"]):
            raise RuntimeError("Wisetoto stopped after 403; no corrected or alternative-round request allowed")
        if len(self.manifest["rounds"]) >= ROUND_LIMIT:
            raise RuntimeError("Hard round budget exhausted")
        if any(r["year"] == year and r["round"] == rnd for r in self.manifest["rounds"]):
            raise ValueError("Round already attempted; retries disabled")
        item = {"year": year, "round": rnd}
        self.manifest["rounds"].append(item)
        self.save()
        try:
            url = BASE + "/index.htm?" + urlencode(dict(tab_type="proto", game_type="pt", game_category="pt1", game_year=year, game_round=rnd))
            html, source = self.get(url, f"resolve {year}/{rnd} master sequence")
            matches = [m for m in SEQ.finditer(html) if (int(m[1]), int(m[2])) == (year, rnd) and m[3] != "0"]
            sequences = {m[3] for m in matches}
            if len(sequences) != 1:
                raise ValueError(f"Expected one exact nonzero sequence, got {sorted(sequences)}")
            seq = next(iter(sequences))
            item.update(master_seq=seq, index_request_id=source["request_id"])
            url = BASE + "/util/gameinfo/get_proto_list.htm?" + urlencode(dict(game_category="pt1", game_year=year, game_round=rnd, game_month="", game_day="", game_info_master_seq=seq, sports="", sort="", tab_type="proto"))
            html, source = self.get(url, f"archive {year}/{rnd} full market rows")
            item["list_request_id"] = source["request_id"]
            soup = BeautifulSoup(html, "html.parser")
            dates = [el.get_text(" ", strip=True) for el in soup.select("div.gameinfo li.a2")]
            item.update(raw_game_rows=len(dates), date_text_counts=dict(Counter(dates)))
            print(json.dumps({"year": year, "round": rnd, "seq": seq, "rows": len(dates), "dates": list(dict.fromkeys(dates))[:14]}, ensure_ascii=False))
        except Exception as exc:
            item["error"] = str(exc)
            print(json.dumps(item, ensure_ascii=False))
        self.save()


def decode(raw):
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("No lossless supported HTML encoding")


def jsonl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def source_fields(source):
    return {"source_request_id": source["request_id"], "source_sha256": source["sha256"],
            "source_file": source["body_file"], "collected_at_utc": source["finished_at_utc"]}


def official_results(payload, source):
    """Home is B(bottom), away is T(top). Require final, uncancelled results."""
    if not isinstance(payload, dict) or str(payload.get("code")) != "100" or not isinstance(payload.get("game"), list):
        raise ValueError("Not a successful official game-list envelope")
    rows, rejected = [], []
    for game in payload["game"]:
        try:
            if game["G_DT"] != "20260902" or str(game["LE_ID"]) != "1" or str(game["SEASON_ID"]) != "2026":
                raise ValueError("Requested date/league/season mismatch")
            if (str(game["GAME_STATE_SC"]), str(game["CANCEL_SC_ID"]), str(game["GAME_RESULT_CK"])) != ("3", "0", "1"):
                raise ValueError("Not a final, uncancelled result")
            if not str(game["B_SCORE_CN"]).isdigit() or not str(game["T_SCORE_CN"]).isdigit():
                raise ValueError("Missing integer scores")
            if not game["G_ID"].startswith(game["G_DT"] + game["AWAY_ID"] + game["HOME_ID"]):
                raise ValueError("Official game ID disagrees with date/home/away codes")
            home_score, away_score = int(game["B_SCORE_CN"]), int(game["T_SCORE_CN"])
            home, away = game["HOME_NM"].strip(), game["AWAY_NM"].strip()
            if not home or not away or home == away:
                raise ValueError("Invalid team names")
            kickoff = datetime.strptime(game["G_DT"] + " " + game["G_TM"], "%Y%m%d %H:%M").replace(tzinfo=KST).isoformat()
            winner = "home" if home_score > away_score else "away" if away_score > home_score else "draw"
            rows.append({"record_type": "official_result_only", "game_id": game["G_ID"],
                         "kickoffKST": kickoff, "league": "KBO", "home": home, "away": away,
                         "home_code": game["HOME_ID"], "away_code": game["AWAY_ID"],
                         "raw_scores": {"B_SCORE_CN": game["B_SCORE_CN"], "T_SCORE_CN": game["T_SCORE_CN"]},
                         "home_score": home_score, "away_score": away_score, "winner": winner,
                         "winner_team": home if winner == "home" else away if winner == "away" else None,
                         "market": None, "odds": None,
                         "result_source_url": "https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx?" + urlencode(dict(gameDate=game["G_DT"], gameId=game["G_ID"])),
                         "result_endpoint_url": source["url"], "result_source_quality": "official_KBO_final_result_retrospective",
                         "final_state_evidence": {k: game[k] for k in ("GAME_STATE_SC", "CANCEL_SC_ID", "GAME_RESULT_CK")},
                         **source_fields(source)})
        except (KeyError, TypeError, ValueError) as exc:
            rejected.append({"game_id": game.get("G_ID"), "reason": str(exc)})
    return rows, rejected


def parse_primary_offers(html, year, rnd, source):
    """Strict full-game normal market only; keep exact source team names.

    No live target market rows were obtained in this run. This parser is checked
    on synthetic fixtures only; those fixtures are never written to offers.jsonl.
    """
    rows, rejected = [], []
    soup = BeautifulSoup(html, "html.parser")
    for ul in soup.select("div.gameinfo ul"):
        def txt(selector):
            el = ul.select_one(selector)
            return " ".join(el.get_text(" ", strip=True).split()) if el else ""
        number = txt("li.a1")
        if not number.isdigit():
            continue
        league = txt("li.a4")
        if league not in {"K리그1", "K리그2", "KBO", "NPB"}:
            continue
        try:
            normal = ul.select_one("li.hm")
            if normal is None or normal.get_text(strip=True) not in {"", "일반"}:
                continue
            date_text = txt("li.a2")
            match = re.fullmatch(r"(\d{2})\.(\d{2})\([^)]*\)\s*(\d{2}):(\d{2})", date_text)
            if not match:
                raise ValueError("Unrecognized source kickoff format")
            month, day, hour, minute = map(int, match.groups())
            if not ((year == 2025 and month == 7 and league.startswith("K리그")) or
                    (year == 2026 and month in {9, 10} and league in {"KBO", "NPB"})):
                continue
            kickoff = datetime(year, month, day, hour, minute, tzinfo=KST).isoformat()
            home_raw, away_raw = txt("li.a6"), txt("li.a8")
            hm, am = re.fullmatch(r"(.+?)\s+(\d+)", home_raw), re.fullmatch(r"(\d+)\s+(.+)", away_raw)
            if not hm or not am or txt("li.a7") != ":":
                raise ValueError("Cannot separate exact team names and final scores")
            home, hs = hm[1], int(hm[2])
            aws, away = int(am[1]), am[2]
            cells = [" ".join(x.get_text(" ", strip=True).split()) for x in ul.select("li.a9")]
            prices = []
            for cell in cells:
                price = re.fullmatch(r"(\d{1,3}\.\d{2})(?:\s*[↑↓])?", cell)
                if price:
                    prices.append(float(price[1]))
                elif cell not in {"-", ""}:
                    raise ValueError("Unrecognized odds cell")
            nway = 3 if league.startswith("K리그") else 2
            if len(cells) != 3 or len(prices) != nway or any(p <= 1 for p in prices):
                raise ValueError("Incomplete or invalid primary-market price vector")
            if nway == 2 and cells[1] != "-":
                raise ValueError("Two-way selection positions not verified")
            tail = [li.get_text(strip=True) for li in ul.find_all("li", recursive=False) if not li.get("class")]
            label = tail[-1] if tail else ""
            winner = "home" if hs > aws else "away" if aws > hs else "draw"
            if {"home": "승", "away": "패", "draw": "무"}[winner] != label:
                raise ValueError("Result label missing or inconsistent with full-game scores")
            rows.append({"record_type": "proto_primary_offer", "kickoffKST": kickoff,
                         "league": league, "home": home, "away": away, "raw_scores": {"home": home_raw, "away": away_raw},
                         "home_score": hs, "away_score": aws, "market": "1X2" if nway == 3 else "moneyline_2way",
                         "market_tag": "hm", "market_label": normal.get_text(strip=True),
                         "odds": dict(zip(["home", "draw", "away"] if nway == 3 else ["home", "away"], prices)),
                         "raw_odds_cells": cells, "winner": winner, "raw_result": label,
                         "year": year, "round": rnd, "game_no": number,
                         "result_source_url": source["url"], "result_source_quality": "Proto_archive_not_independently_official_verified",
                         "quote_quality": "retrospective_archive_not_pregame_certified", **source_fields(source)})
        except (TypeError, ValueError) as exc:
            rejected.append({"year": year, "round": rnd, "game_no": number, "reason": str(exc)})
    return rows, rejected


def reject_conflicts(rows):
    grouped = defaultdict(list)
    for row in rows:
        # Same-day same-team reissues with differing time/vector/score are unsafe.
        grouped[(row["kickoffKST"][:10], row["league"], row["home"], row["away"], row["market"])].append(row)
    accepted, rejected = [], []
    for key, group in grouped.items():
        signatures = {json.dumps([r["kickoffKST"], r["odds"], r["home_score"], r["away_score"], r["winner"]], sort_keys=True) for r in group}
        if len(signatures) != 1:
            rejected.append({"event_market_key": key, "reason": "conflicting_reissue_or_ambiguous_doubleheader", "records": group})
        else:
            row = dict(group[0])
            row["duplicate_source_evidence"] = [{k: r.get(k) for k in ("year", "round", "game_no", "source_request_id", "source_sha256")} for r in group]
            accepted.append(row)
    return accepted, rejected


def build():
    """Offline replay only: verifies all source hashes before deriving records."""
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    manifest["initial_collector_limitations"] = [
        "Requests 1-6 preceded request-header logging and omitted X-Requested-With: XMLHttpRequest.",
        "Wisetoto requests 2 and 4 returned 403 with exact UTF-8 body: 잘못된 접근입니다.[code:gproto_xrw]",
        "The existing src/wisetoto.py client includes X-Requested-With; its omission is the likely cause, not confirmed by a corrected request.",
        "Two different round attempts were issued before the persistent host-stop guard was added; no identical request was repeated.",
        "Request 6 inherited the Wisetoto index Referer and returned an HTTP-200 HTML KBO error page; cause unverified.",
        "Request 7 used the KBO GameCenter Referer and succeeded on a distinct public endpoint.",
        "Final collector restores standard AJAX header but refuses any further Wisetoto call in this run; no corrected request was made."]
    by_id = {r["request_id"]: r for r in manifest["requests"]}
    for source in by_id.values():
        if source.get("body_file"):
            raw = (OUT / source["body_file"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != source["sha256"]:
                raise ValueError(f"Source hash mismatch: {source['body_file']}")
    offers, results, rejected, failures = [], [], [], []
    for rnd in manifest["rounds"]:
        index = by_id.get(rnd.get("index_request_id"))
        if index:
            text, _ = decode((OUT / index["body_file"]).read_bytes())
            periods = re.findall(r"발매기간\s*:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", text)
            rnd["index_sales_startKST"] = sorted(set(periods))
            rnd["game_dates_verified"] = False
        if "list_request_id" in rnd:
            source = by_id[rnd["list_request_id"]]
            text, _ = decode((OUT / source["body_file"]).read_bytes())
            parsed, bad = parse_primary_offers(text, rnd["year"], rnd["round"], source)
            offers.extend(parsed)
            rejected.extend(bad)
            rnd["game_dates_verified"] = bool(parsed)
    for source in by_id.values():
        if source.get("status") != 200 or source.get("error"):
            failure = {"source_request_id": source["request_id"], "reason": source.get("error", "HTTP failure")}
            if source.get("status") == 403 and source.get("body_file"):
                failure["exact_response_body"], source["decoded_encoding"] = decode((OUT / source["body_file"]).read_bytes())
                failure["likely_cause"] = "Initial collector omitted standard X-Requested-With AJAX header; corrected call not attempted"
            failures.append(failure)
        elif source["url"].endswith("/GetScheduleList"):
            raw = (OUT / source["body_file"]).read_bytes()
            try:
                json.loads(raw.decode("utf-8-sig"))
            except ValueError:
                failures.append({"source_request_id": source["request_id"], "reason": "HTTP 200 but HTML KBO error page, not expected JSON; not retried"})
        elif source["url"].endswith("/GetKboGameList"):
            payload = json.loads((OUT / source["body_file"]).read_text(encoding="utf-8-sig"))
            parsed, bad = official_results(payload, source)
            results.extend(parsed)
            rejected.extend(bad)
    offers, conflicts = reject_conflicts(offers)
    rejected.extend(conflicts)
    jsonl(OUT / "offers.jsonl", offers)
    jsonl(OUT / "official_results.jsonl", results)
    jsonl(OUT / "rejected.jsonl", rejected)
    summary = {"request_count": len(by_id), "request_limit": LIMIT, "rounds_attempted": len(manifest["rounds"]),
               "round_limit": ROUND_LIMIT, "actual_offer_count": len(offers), "official_result_count": len(results),
               "offer_counts_by_league": dict(Counter(r["league"] for r in offers)), "rejected_count": len(rejected),
               "failures": failures, "rounds": manifest["rounds"],
               "unavailable": ["2025 July K-league primary odds", "2026 September KBO/NPB primary odds"],
               "not_attempted": ["2026 October expansion after archive endpoint 403"],
               "initial_collector_limitations": manifest["initial_collector_limitations"],
               "notes": ["No retries, authentication, bypass, env-file reads, or DB reads/writes.",
                         "Five official result rows are not priced offers and cannot independently support ROI.",
                         "Round 85 sales start is July 21; actual target kickoff dates were not recovered.",
                         "Primary HTML parser has synthetic-fixture checks only; no live market row parsed in this run."]}
    write_json(OUT / "summary.json", summary)
    write_json(OUT / "manifest.json", manifest)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def selftest():
    from copy import deepcopy
    manifest = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
    source = next(r for r in manifest["requests"] if r["url"].endswith("/GetKboGameList"))
    payload = json.loads((OUT / source["body_file"]).read_text(encoding="utf-8-sig"))
    rows, bad = official_results(payload, source)
    assert len(rows) == 5 and not bad
    expected = {"20260902LGOB0": (1, 5, "away"), "20260902LTSS0": (8, 5, "home"),
                "20260902HHKT0": (9, 6, "home"), "20260902HTNC0": (3, 2, "home"), "20260902SKWO0": (6, 9, "away")}
    assert {r["game_id"]: (r["home_score"], r["away_score"], r["winner"]) for r in rows} == expected
    for field, value in [("G_DT", "20260903"), ("GAME_STATE_SC", "1"), ("CANCEL_SC_ID", "1"),
                         ("GAME_RESULT_CK", 0), ("B_SCORE_CN", ""), ("G_ID", "wrong_id")]:
        modified = deepcopy(payload)
        modified["game"][0][field] = value
        good, failures = official_results(modified, source)
        assert len(good) == 4 and len(failures) == 1, field
    test_offer = {**rows[0], "market": "moneyline_2way", "odds": {"home": 2.0, "away": 1.7}}
    accepted, conflicts = reject_conflicts([test_offer, deepcopy(test_offer)])
    assert len(accepted) == 1 and not conflicts
    changed = deepcopy(test_offer)
    changed["odds"]["home"] = 2.1
    accepted, conflicts = reject_conflicts([test_offer, changed])
    assert not accepted and len(conflicts) == 1
    fixture = '<div class="gameinfo"><ul><li class="a1">1</li><li class="a2">09.02(수) 18:30</li><li class="a4">KBO</li><li class="hm">일반</li><li class="a6">두산 1</li><li class="a7">:</li><li class="a8">5 LG</li><li class="a9">2.00</li><li class="a9">-</li><li class="a9">1.70</li><li>패</li></ul></div>'
    parsed, failures = parse_primary_offers(fixture, 2026, 104, source)
    assert len(parsed) == 1 and not failures and parsed[0]["home"] == "두산" and parsed[0]["odds"]["away"] == 1.7
    parsed, failures = parse_primary_offers(fixture.replace('>1.70<', '>-<'), 2026, 104, source)
    assert not parsed and len(failures) == 1
    parsed, failures = parse_primary_offers(fixture.replace('>패<', '>승<'), 2026, 104, source)
    assert not parsed and len(failures) == 1
    parsed, failures = parse_primary_offers(fixture.replace('>일반<', '>h(전반)<'), 2026, 104, source)
    assert not parsed and not failures
    starts = [r["started_unix"] for r in manifest["requests"]]
    assert len(starts) <= LIMIT and len(manifest["rounds"]) <= ROUND_LIMIT
    assert all(b - a >= INTERVAL - 0.02 for a, b in zip(starts, starts[1:]))
    write_json(OUT / "validation.json", {
        "passed": True, "official_results_checked": len(rows), "invalid_result_cases_checked": 6,
        "checks": ["actual_official_scores_and_winners", "wrong_date_rejected", "nonfinal_rejected", "cancelled_rejected",
                   "missing_result_flag_rejected", "missing_score_rejected", "wrong_id_rejected",
                   "identical_reissue_deduplicated", "conflicting_reissue_rejected",
                   "synthetic_full_primary_vector", "synthetic_missing_price_rejected", "synthetic_wrong_result_rejected",
                   "synthetic_first_half_excluded", "request_and_round_budgets", "minimum_interval"],
        "request_count": len(starts), "observed_request_intervals_seconds": [round(b-a, 6) for a, b in zip(starts, starts[1:])],
        "limitations": ["Primary market parser tested on synthetic fixture only; no actual offer recovered."]})
    print("PASS: 5 actual KBO final results, 6 invalid-result cases, duplicate/conflict gates, synthetic primary-vector gates, request limits/interval")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    rnd = sub.add_parser("round")
    rnd.add_argument("year", type=int)
    rnd.add_argument("round", type=int)
    get = sub.add_parser("get")
    get.add_argument("url")
    sub.add_parser("build")
    sub.add_parser("selftest")
    sub.add_parser("kbo-september")
    sub.add_parser("kbo-day")
    args = parser.parse_args()
    if args.command == "round":
        Collector().collect_round(args.year, args.round)
    elif args.command == "get":
        html, record = Collector().get(args.url, "official KBO result discovery")
        print(json.dumps({"request_id": record["request_id"], "bytes": record["bytes"], "encoding": record["decoded_encoding"]}))
    elif args.command == "build":
        build()
    elif args.command == "kbo-september":
        _, record = Collector().request("POST", "https://www.koreabaseball.com/ws/Schedule.asmx/GetScheduleList",
                                      "official September 2026 regular-season results; parameters from source-05 inline JS",
                                      dict(leId="1", srIdList="0,9,6", seasonId="2026", gameMonth="09", teamId=""))
        print(json.dumps({"request_id": record["request_id"], "bytes": record["bytes"]}))
    elif args.command == "kbo-day":
        _, record = Collector().request("POST", "https://www.koreabaseball.com/ws/Main.asmx/GetKboGameList",
                                      "official 2026-09-02 results; public GameCenter endpoint from src/kbo_official.py",
                                      dict(leId="1", srId="0,1,3,4,5,6,7,8,9", date="20260902"))
        print(json.dumps({"request_id": record["request_id"], "bytes": record["bytes"]}))
    else:
        selftest()


if __name__ == "__main__":
    main()
