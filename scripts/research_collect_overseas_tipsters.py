"""Bounded public historical tipster audit; never an operational betting feed.

Only public pages, no authentication, retries, proxying or challenge bypass.
Evidence is metadata/short recommendation extracts, never entire articles.
Run `probe URL ...` to collect evidence, `build` to reproduce the derived audit,
and `check` to test settlement/conversion and audit invariants offline.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/tipster-roi-expansion-20261028/overseas"
ALLOWED = {"www.tonyspicks.com", "www.covers.com", "www.pickswise.com",
           "npb.jp", "statsapi.mlb.com", "cdn.nba.com", "data.nba.net", "www.nba.com"}
MAX_REQUESTS = 49  # plus 3 seed opens and 8 search queries = 60 bounded operations


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(name, default):
    path = OUT / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def american_decimal(value):
    value = int(value)
    if abs(value) < 100:
        raise ValueError("Not valid American odds")
    return 1 + (value / 100 if value > 0 else 100 / abs(value))


def source_for(url):
    host = urlparse(url).hostname or ""
    return {"www.tonyspicks.com": "TonysPicks", "www.covers.com": "Covers",
            "www.pickswise.com": "Pickswise"}.get(host, "official")


def metadata(soup):
    result = {}
    for tag in soup.select("meta[property], meta[name]"):
        key = tag.get("property", tag.get("name", ""))
        if re.search(r"(publish|modified|author|date|title)", key, re.I):
            result[key] = tag.get("content")
    def scan(obj):
        if isinstance(obj, list):
            for value in obj:
                scan(value)
        elif isinstance(obj, dict):
            for key in ("datePublished", "dateModified", "author", "headline"):
                if key in obj:
                    result.setdefault("jsonld:" + key, obj[key])
            for value in obj.values():
                if isinstance(value, (list, dict)):
                    scan(value)
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            scan(json.loads(tag.string or tag.get_text()))
        except (ValueError, TypeError):
            pass
    return result


def fetch(url, refresh=False):
    if urlparse(url).hostname not in ALLOWED:
        raise ValueError("Host not in explicit public-source allowlist")
    ledger = read_json("requests.json", [])
    existing = next((r for r in ledger if r["url"] == url), None)
    if existing and not refresh:
        return read_json("documents.json", {}).get(url, {"url": url, "error": existing.get("error")})
    if len(ledger) >= MAX_REQUESTS:
        raise RuntimeError("Bounded request budget exhausted")
    entry = {"url": url, "source": source_for(url), "requested_at": now()}
    doc = {"url": url, "source": entry["source"]}
    try:
        response = requests.get(url, timeout=(8, 18), allow_redirects=False,
                                headers={"User-Agent": "Mozilla/5.0 (compatible; PublicResearchAudit/1.0)"})
        entry.update(status=response.status_code, observed_at=now(), bytes=len(response.content),
                     sha256=hashlib.sha256(response.content).hexdigest())
        doc.update(observed_at=entry["observed_at"], sha256=entry["sha256"], status=response.status_code)
        if response.status_code != 200:
            entry["error"] = "http_" + str(response.status_code)
            doc["error"] = entry["error"]
        elif "json" in response.headers.get("Content-Type", "") or url.endswith(".json"):
            payload = response.json()
            doc["official_data"] = compact_official_json(url, payload)
        else:
            soup = BeautifulSoup(response.content, "html.parser")
            doc["metadata"] = metadata(soup)
            doc["title"] = soup.h1.get_text(" ", strip=True) if soup.h1 else (soup.title.get_text(" ", strip=True) if soup.title else None)
            doc["headings"] = [h.get_text(" ", strip=True)[:180] for h in soup.select("h2,h3")][:45]
            # Short extracts for inspectability, not a full article archive.
            rec = []
            for node in soup.select("p,li,h2,h3,strong"):
                text = " ".join(node.get_text(" ", strip=True).split())
                if (re.search(r"(?:[+-]\d{3}|\b\d\.\d{2}\b)", text)
                        and re.search(r"(pick|bet|money.?line|over|under|back|lay|take)", text, re.I)):
                    # Store nearby odds/selection facts only; prose clipped.
                    if len(text) <= 230 and text not in rec:
                        rec.append(text)
            doc["recommendation_extracts"] = rec[:12]
            content = soup.select_one("article") or soup.select_one("main") or soup
            contexts = []
            for node in content.select("p,li,h2,h3"):
                value = " ".join(node.get_text(" ", strip=True).split())
                if re.search(r"(pick|bet|money.?line|over|under|back|lay|take|price)", value, re.I):
                    for match in re.finditer(r"(?<!\d)(?:[+−–-]\d{3,4}|minus[ -]+\d{3}|plus[ -]+\d{3})(?!\d)", value, re.I):
                        contexts.append(value[max(0, match.start()-95):match.end()+70])
            doc["odds_contexts"] = list(dict.fromkeys(contexts))[:15]
            main_text = content.get_text(" ", strip=True)
            if source_for(url) == "Covers":
                flat = " ".join(soup.get_text(" ", strip=True).split())
                title_pos = flat.find(doc.get("title") or "")
                flat = flat[max(0, title_pos):]
                doc["static_pick_labels"] = list(dict.fromkeys(re.findall(
                    r"[^.!?]{0,50}(?:best bet:|pick:|Pick made:)[^.!?]{0,140}", flat, re.I)))[:10]
                doc["static_price_contexts"] = list(dict.fromkeys(
                    flat[max(0,m.start()-85):m.end()+65] for m in re.finditer(r"(?<!\d)[+−–-]\d{3,4}(?!\d)",flat)))[:18]
            doc["body_date_signals"] = list(dict.fromkeys(re.findall(
                r"(?:20\d{2} (?:MLB|NBA|NPB)|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),? (?:September|October) \d{1,2}(?:,? 20\d{2})?)",
                main_text)))[:15]
            if doc.get("title") in {"One moment, please...", "Just a moment..."}:
                doc["error"] = entry["error"] = "access_challenge_no_bypass"
            doc["links"] = list(dict.fromkeys(urljoin(url, a["href"]) for a in soup.select("a[href]")
                if re.search(r"(prediction|best-bets|ron-crawford|/scores/2026/|gm2026)", a["href"], re.I)))[:140]
            if urlparse(url).hostname == "npb.jp":
                # Extract only dated score tables for official result matching.
                doc["official_tables"] = [t.get_text(" ", strip=True)[:500] for t in soup.select("table")][:25]
                dated = re.search(r"gm(\d{4})(\d{4})", url)
                cards = []
                if dated:
                    prefix = f"/scores/{dated[1]}/{dated[2]}/"
                    for a in soup.select("a[href]"):
                        if prefix not in a["href"]:
                            continue
                        parents = []
                        for parent in list(a.parents)[:4]:
                            parents.append({"tag": parent.name, "class": parent.get("class"),
                                            "text": parent.get_text(" ", strip=True)[:450]})
                        cards.append({"url": urljoin(url, a["href"]), "text": a.get_text(" ",strip=True), "parents": parents})
                doc["official_scorecards"] = cards
    except (requests.RequestException, ValueError) as exc:
        entry.update(observed_at=now(), error=type(exc).__name__ + ": " + str(exc)[:220])
        doc.update(observed_at=entry["observed_at"], error=entry["error"])
    ledger.append(entry)
    write_json("requests.json", ledger)
    documents = read_json("documents.json", {})
    documents[url] = doc
    write_json("documents.json", documents)
    return doc


def compact_official_json(url, payload):
    if "statsapi.mlb.com" in url:
        return [{"officialDate": game["officialDate"], "gamePk": game["gamePk"],
                 "gameDate": game["gameDate"], "status": game["status"],
                 "teams": game["teams"], "gameType": game.get("gameType")}
                for day in payload.get("dates", []) for game in day.get("games", [])]
    if "scoreboard" in payload:
        return payload["scoreboard"]
    return payload


NPB_CODES = {
    "Nippon-Ham Fighters": "f", "Hokkaido Nippon-Ham Fighters": "f",
    "Rakuten Golden Eagles": "e", "Tohoku Rakuten Golden Eagles": "e",
    "Rakuten Eagles": "e", "SoftBank Hawks": "h", "Fukuoka SoftBank Hawks": "h",
    "Orix Buffaloes": "b", "Chiba Lotte Marines": "m", "Lotte Marines": "m",
    "Saitama Seibu Lions": "l", "Seibu Lions": "l", "Yomiuri Giants": "g",
    "Hiroshima Carp": "c", "Yokohama BayStars": "db", "Yokohama DeNA BayStars": "db",
}
MLB_NAMES = {
    "Yankees": "New York Yankees", "Twins": "Minnesota Twins",
    "Giants": "San Francisco Giants", "Diamondbacks": "Arizona Diamondbacks",
    "Phillies": "Philadelphia Phillies", "Dodgers": "Los Angeles Dodgers",
    "Rangers": "Texas Rangers", "Astros": "Houston Astros", "Cubs": "Chicago Cubs",
    "Pirates": "Pittsburgh Pirates", "Brewers": "Milwaukee Brewers", "Rays": "Tampa Bay Rays",
}
TONY_SELECTIONS = {
    "nippon-ham-fighters-vs-rakuten-golden-eagles-prediction-ron-crawford-september-5":
        (5, "Hokkaido Nippon-Ham Fighters", -140, "money line at -140", False),
    "softbank-hawks-vs-orix-buffaloes-best-bet-ron-crawford-9-17-26":
        (17, "Fukuoka SoftBank Hawks", -139, "money line at -139", True),
    "softbank-hawks-vs-orix-buffaloes-best-bet-ron-9-17-26":
        (17, "Fukuoka SoftBank Hawks", -139, "moneyline at around minus-139", True),
    "lotte-marines-vs-seibu-lions-best-bet-september-26-ron-crawford":
        (26, "Saitama Seibu Lions", -135, "money line at around minus 135", True),
    "softbank-hawks-vs-rakuten-golden-eagles-best-bet-ron-9-19-26":
        (19, "Fukuoka SoftBank Hawks", -145, "money line at -145", False),
    "hiroshima-carp-vs-yomiuri-giants-best-bet-ron-9-29-26":
        (29, "Yomiuri Giants", -135, "Yomiuri Giants on the money line at -135", False),
    "seibu-lions-vs-orix-buffaloes-best-bet-ron-crawford-9-8-26":
        (8, "Saitama Seibu Lions", -140, "money line, priced around minus-140", True),
    "rakuten-eagles-vs-nippon-ham-fighters-pick-ron-9-23-26":
        (23, "Hokkaido Nippon-Ham Fighters", -135, "money line at -135", False),
    "rakuten-eagles-vs-lotte-marines-ron-crawford-best-bet-9-16-26":
        (16, "Tohoku Rakuten Golden Eagles", -116, "money line at minus 116", False),
    "yomiuri-giants-vs-yokohama-baystars-ron-crawford-best-bet-9-15-26":
        (15, "Yokohama DeNA BayStars", -145, "money line at -145", False),
    "seibu-lions-vs-lotte-marines-best-bet-ron-crawford-9-1-26":
        (1, "Saitama Seibu Lions", -132, "money line at -132", False),
}


def author_name(value):
    if isinstance(value, list):
        return "; ".join(filter(None, (author_name(v) for v in value)))
    if isinstance(value, dict):
        return value.get("name") or author_name(value.get("mainEntity"))
    return value if isinstance(value, str) else None


def base_candidate(doc, league, match_date, teams, quote):
    meta = doc.get("metadata", {})
    published = meta.get("jsonld:datePublished") or meta.get("article:published_time")
    offset = datetime.fromisoformat(published.replace("Z", "+00:00")).strftime("%z") if published else None
    return {
        "source": doc["source"], "author": author_name(meta.get("jsonld:author")) or meta.get("author"),
        "url": doc["url"], "source_reported_published_at": published,
        "source_reported_modified_at": meta.get("jsonld:dateModified"),
        "timezone": "UTC" + offset[:3] + ":" + offset[3:] if offset else None,
        "observed_at": doc["observed_at"], "league": league, "match_date": match_date,
        "match_date_timezone": "Asia/Tokyo" if league == "NPB" else "America/New_York",
        "match_date_basis": "article_target_date_not_current_update_date",
        "teams": teams, "teams_order": "article_order_not_assumed_home_away",
        "market": None, "selected_team": None, "selection": None, "line": None,
        "reported_american_odds": None, "reported_decimal_odds": None,
        "original_odds_text": None, "odds_time_quality": "missing_original_price",
        "cutoff_proven": False, "cutoff_evidence": None,
        "price_is_domestic_proto": False, "independent_first_publication_proven": False,
        "strict_backtest_eligible": False, "settlement_rules_verified": False,
        "market_period": "full_game_unless_player_prop", "author_evidence": "observed_article_metadata_not_independent_original_version",
        "observed_article_version": "current_historical_page_not_independently_archived_first_version",
        "evidence_quote": quote, "source_response_sha256": doc["sha256"],
        "status": "candidate", "exclusion_reasons": [],
    }


def add_price(row, value, text, approximate=False):
    row.update(reported_american_odds=value, reported_decimal_odds=american_decimal(value),
               original_odds_text=text,
               odds_time_quality=("source_reported_approximate_recording_price_unverified" if approximate
                                  else "source_reported_historical_recommendation_price_unverified"))


def parse_pickswise_heading(heading):
    heading = " ".join(heading.split())
    match = re.fullmatch(r"(?:NBA|MLB) best bet:\s*(.*?)\s*\(([+-]\d{3,4})\)", heading, re.I)
    if not match:
        return None
    label, odds = match[1], int(match[2])
    result = {"market": "player_prop", "selected_team": None, "selection": label,
              "line": None, "teams": [], "reported_american_odds": odds,
              "original_odds_text": match[2]}
    if ml := re.fullmatch(r"(.+?) ML over (.+)", label, re.I):
        result.update(market="moneyline", selected_team=ml[1], selection=ml[1], teams=[ml[1], ml[2]])
    elif spread := re.fullmatch(r"(.+?) ([+-]\d+(?:\.\d+)?) over (.+)", label, re.I):
        result.update(market="spread", selected_team=spread[1], selection=spread[1],
                      line=float(spread[2]), teams=[spread[1], spread[3]])
    elif total := re.fullmatch(r"(.+?) vs (.+?) (Over|Under) (\d+(?:\.\d+)?)", label, re.I):
        result.update(market="total", selection=total[3].lower(), line=float(total[4]), teams=[total[1], total[2]])
    else:
        if line := re.search(r"(?:Over|Under|over|under) (\d+(?:\.\d+)?)", label):
            result["line"] = float(line[1])
        # Preserve teams literally present in prop headings; do not infer an opponent.
        if matchup := re.search(r" in (.+?) vs (.+)$", label):
            result["teams"] = [matchup[1], matchup[2]]
        elif matchup := re.search(r"\(([^)]+)\).*?vs (.+)$", label):
            result["teams"] = [matchup[1], matchup[2]]
    return result


def parse_candidates(documents):
    rows = []
    for url, doc in documents.items():
        if doc.get("error") or doc["source"] == "official":
            continue
        if doc["source"] == "TonysPicks":
            slug = url.rstrip("/").split("/")[-1]
            if slug not in TONY_SELECTIONS:
                continue
            day, selected, odds, quote, approx = TONY_SELECTIONS[slug]
            title = doc["title"]
            title = re.split(r" (?:Prediction|Best Bet)|, 9/", title)[0]
            teams = title.split(" vs ")
            row = base_candidate(doc, "NPB", f"2026-09-{day:02d}", teams, quote)
            row.update(market="moneyline", selected_team=selected, selection=selected)
            add_price(row, odds, re.search(r"(?:minus[ -]+\d{3}|-\d{3})", quote)[0], approx)
            if day == 1:
                row["source_identity_warning"] = "Article says Seibu hosts Lotte; official game is Lotte home. Team/date pair matches; venue claim is wrong."
            if day == 26:
                row["timing_warning"] = "Source publication metadata is 19:17 JST; likely post-start. No pregame cutoff claim."
            rows.append(row)
        elif doc["source"] == "Pickswise":
            league = "NBA" if "/nba/" in url else "MLB"
            if "/nba/" not in url and "mlb" not in url:
                continue
            published = doc["metadata"]["jsonld:datePublished"]
            target = published[:10]
            if title_date := re.search(r"October (\d{1,2})", doc.get("title", "")):
                target = published[:4] + f"-10-{int(title_date[1]):02d}"
            for heading in doc.get("headings", []):
                parsed = parse_pickswise_heading(heading)
                if not parsed:
                    continue
                row = base_candidate(doc, league, target, parsed.pop("teams"), heading)
                odds, original = parsed.pop("reported_american_odds"), parsed.pop("original_odds_text")
                row.update(parsed)
                add_price(row, odds, original)
                if row["market"] == "player_prop":
                    row["status"] = "excluded"
                    row["exclusion_reasons"] = ["player_prop_requires_player_stat_result_not_full_game_score"]
                    if len(row["teams"]) != 2:
                        row["exclusion_reasons"].append("incomplete_match_team_identity_in_saved_heading")
                # Stable primary publication year overrides misleading current 'Updated' labels.
                if target.startswith("2025"):
                    row["publication_warning"] = "Current update label can show 2026; first publication and article season identify 2025."
                rows.append(row)
        elif doc["source"] == "Covers":
            slug = url.rstrip("/").split("/")[-1]
            matchup = slug.split("-prediction")[0]
            left, right = matchup.split("-vs-")
            teams = [left.replace("-", " ").title(), right.replace("-", " ").title()]
            date_match = re.search(r"(\d{1,2})-(\d{1,2})-(20\d{2})$", slug)
            if not date_match:
                continue
            date = f"{date_match[3]}-{int(date_match[1]):02d}-{int(date_match[2]):02d}"
            row = base_candidate(doc, "MLB", date, teams, "")
            price_contexts = doc.get("static_price_contexts", [])
            best = next((re.search(r"best bet\s*:\s*(.+?)\s*\(([+-]\d{3,4})\)", x, re.I)
                         for x in price_contexts if re.search(r"best bet\s*:", x, re.I)), None)
            if best:
                label, odds = best[1], int(best[2])
                row["evidence_quote"] = best[0]
                add_price(row, odds, best[2])
                row["selection"] = label
                if total := re.fullmatch(r"(Under|Over) (\d+(?:\.\d+)?)", label, re.I):
                    row.update(market="total", selection=total[1].lower(), line=float(total[2]))
                elif spread := re.fullmatch(r"(.+?) ([+-]\d+(?:\.\d+)?)", label):
                    row.update(market="spread", selected_team=spread[1], line=float(spread[2]))
                elif ml := re.fullmatch(r"(.+?) moneyline", label, re.I):
                    row.update(market="moneyline", selected_team=ml[1])
                else:
                    row.update(market="player_prop", status="excluded", exclusion_reasons=["player_prop_requires_player_stat_result_not_full_game_score"])
            else:
                row.update(market="moneyline", selected_team=teams[0], selection=teams[0], status="excluded")
                if date.startswith("2026"):
                    row.update(evidence_quote="Brewers moneyline", odds_time_quality="prediction_market_price_not_sportsbook_odds",
                               exclusion_reasons=["polymarket_price_not_sportsbook_decimal_odds"])
                else:
                    row.update(evidence_quote="Philly to pull off the win", exclusion_reasons=["original_recommendation_price_not_extracted"])
            rows.append(row)
    seen = {}
    for row in sorted(rows, key=lambda r: (r["source_reported_published_at"] or "", r["url"])):
        selection = NPB_CODES.get(row["selected_team"], row["selected_team"])
        teams = sorted(NPB_CODES.get(t, t) for t in row["teams"])
        key = json.dumps([row["source"], row["author"], row["match_date"],teams,row["market"],selection,row["line"]])
        row["candidate_id"] = hashlib.sha256((row["url"] + row["evidence_quote"]).encode()).hexdigest()[:20]
        if key in seen:
            row.update(status="excluded", duplicate_of=seen[key])
            row["exclusion_reasons"].append("same_source_author_game_market_selection_duplicate")
        else:
            seen[key] = row["candidate_id"]
    return rows


def collect_official_results(documents, local_cache=None):
    results = []
    for url, doc in documents.items():
        if doc.get("error"):
            continue
        if "statsapi.mlb.com" in url:
            for game in doc.get("official_data", []):
                if game["status"]["abstractGameState"] != "Final":
                    continue
                home, away = game["teams"]["home"], game["teams"]["away"]
                results.append({"league":"MLB", "match_date":game["officialDate"], "home":home["team"]["name"],
                    "away":away["team"]["name"], "home_score":home["score"], "away_score":away["score"],
                    "official_id":str(game["gamePk"]), "official_result_url":url,
                    "official_observed_at":doc["observed_at"], "official_kickoff":game["gameDate"],
                    "result_provenance":"fresh_official_public_response", "status":"final"})
        if "npb.jp/bis/2026/games/" in url:
            day = re.search(r"gm(\d{4})(\d{2})(\d{2})",url)
            date = f"{day[1]}-{day[2]}-{day[3]}"
            for card in doc.get("official_scorecards", []):
                text = " ".join(card.get("text", "").split())
                score = re.match(r"(.+?) (\d+) (\d+)回戦 .+? (\d+) (.+)$",text)
                codes = re.search(r"/([a-z]+)-([a-z]+)-\d+/",card["url"])
                if score and codes:
                    results.append({"league":"NPB", "match_date":date, "home":codes[1],"away":codes[2],
                        "home_name_official":score[1], "away_name_official":score[5],
                        "home_score":int(score[2]), "away_score":int(score[4]), "official_id":card["url"],
                        "official_result_url":url, "official_game_url":card["url"],
                        "official_observed_at":doc["observed_at"], "result_provenance":"fresh_official_public_response",
                        "status":"final", "official_kickoff":None})
    if local_cache:
        for game in local_cache:
            if game.get("league") != "MLB":
                continue
            # Official date, not the cache's KST calendar date.
            raw = game["raw_team_names"]
            results.append({"league":"MLB","match_date":game["official_date"],"home":raw["home"],"away":raw["away"],
                "home_score":game["home_score"],"away_score":game["away_score"],
                "official_id":game["official_id"],"official_result_url":game["source_url"],
                "official_kickoff":game["kickoff"],"official_observed_at":None,
                "result_provenance":"existing_read_only_official_cache_original_capture_time_unknown", "status":"final"})
    return results


def match_result(row, results):
    if len(row["teams"]) != 2:
        return None
    convert = NPB_CODES if row["league"] == "NPB" else MLB_NAMES
    names = {convert.get(t,t) for t in row["teams"]}
    matches = [r for r in results if r["league"] == row["league"] and r["match_date"] == row["match_date"]
               and {r["home"],r["away"]} == names]
    return matches[0] if len(matches) == 1 else None


def build():
    documents = read_json("documents.json", {})
    # Migrate the first archive probe's HTTP-200 access challenge classification.
    for doc in documents.values():
        if doc.get("title") in {"One moment, please...", "Just a moment..."}:
            doc["error"] = "access_challenge_no_bypass"
    ledger = read_json("requests.json",[])
    for entry in ledger:
        doc = documents.get(entry["url"],{})
        if doc.get("error") == "access_challenge_no_bypass":
            entry["error"] = doc["error"]
    write_json("requests.json",ledger)
    candidates = parse_candidates(documents)
    cache_path = Path("C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-400-audit-20261026-local/official-international.json")
    local_inputs = []
    cache = None
    for path in [cache_path, Path("C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/accuracy-formula-lab/data/processed/games.csv"),
                 Path("C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-cross-source-20261026/scripts/tipster_audit_aliases.json")]:
        item = {"path":str(path),"exists":path.is_file(),"checked_at":now(),"access":"read_only"}
        if path.is_file():
            blob = path.read_bytes()
            item.update(bytes=len(blob),sha256=hashlib.sha256(blob).hexdigest())
            if path == cache_path:
                cache = json.loads(blob)
                item["league_counts"] = dict(Counter(r["league"] for r in cache))
        local_inputs.append(item)
    write_json("local_input_checks.json", local_inputs)
    results = collect_official_results(documents,cache)
    used_results = {}
    for row in candidates:
        result = match_result(row,results)
        row["official_result"] = result
        row["official_result_match_quality"] = "exact_date_and_both_team_identities_unique" if result else "not_verified"
        row["settlement_exclusion_reasons"] = [] if result else ["official_result_not_verified"]
        if row["market"] == "player_prop":
            row["market_period"] = "player_prop"
            row["settlement_exclusion_reasons"].append("player_stat_not_collected")
        if row["source"] == "Covers" and "yankees-vs-twins" in row["url"]:
            row["author_warning"] = "Observed HTML metadata: Tony Sartori; web index snippet: James Bisson. Original author unresolved."
        if result:
            used_results[(result["league"],result["official_id"])] = result
    with (OUT / "candidates.jsonl").open("w",encoding="utf-8") as stream:
        for row in candidates:
            stream.write(json.dumps(row,ensure_ascii=False) + "\n")
    write_json("official_matched_results.json",list(used_results.values()))
    pages = []
    for url,doc in documents.items():
        if doc["source"] == "official":
            continue
        rows = [r for r in candidates if r["url"] == url]
        pages.append({"source":doc["source"],"url":url,"observed_at":doc.get("observed_at"),
            "status":doc.get("status"),"candidate_rows":len(rows),"failure":doc.get("error"),
            "exclusions":dict(Counter(x for r in rows for x in r["exclusion_reasons"]))})
    write_json("source_coverage.json",pages)
    summary = {"generated_at":now(),"candidate_rows":len(candidates),
        "by_source":dict(Counter(r["source"] for r in candidates)),
        "by_league":dict(Counter(r["league"] for r in candidates)),
        "by_market":dict(Counter(r["market"] for r in candidates)),
        "candidate_status":dict(Counter(r["status"] for r in candidates)),
        "priced_nonduplicate_full_game_candidates":sum(r["status"] == "candidate" and r["reported_decimal_odds"] is not None for r in candidates),
        "full_game_candidates_with_official_result":sum(r["status"] == "candidate" and r["official_result"] is not None for r in candidates),
        "official_matched_all_rows":sum(r["official_result"] is not None for r in candidates),
        "cutoff_proven_rows":sum(r["cutoff_proven"] for r in candidates),
        "python_http_requests":len(read_json("requests.json",[])),"external_web_operations":11,
        "exclusions":dict(Counter(x for r in candidates for x in r["exclusion_reasons"])),
        "aggregation_owner":"parent task; no Proto odds join or final ROI claim in this collector"}
    write_json("summary.json",summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


def check():
    assert abs(american_decimal(-140)-1.7142857142857144) < 1e-12
    assert american_decimal(300) == 4
    assert american_decimal(100) == american_decimal(-100) == 2
    for invalid in [0,12,-50]:
        try:
            american_decimal(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid odds accepted")
    spread = parse_pickswise_heading("NBA Best Bet: Los Angeles Lakers -3.5 over Portland Trail Blazers (-104)")
    assert spread["market"] == "spread" and spread["line"] == -3.5 and spread["reported_american_odds"] == -104
    total = parse_pickswise_heading("NBA Best Bet: Kings vs Spurs Over 229.5 (-115)")
    assert total["market"] == "total" and total["line"] == 229.5 and total["selected_team"] is None
    assert parse_pickswise_heading("MLB Best Bet: Chicago White Sox ML over Cleveland Guardians (+132)")["market"] == "moneyline"
    assert parse_pickswise_heading("NBA Best Bet: Michael Porter Jr. Over 21.5 points (-112)")["market"] == "player_prop"
    assert parse_pickswise_heading("Odds available at time of publishing. Playable to -125.") is None
    candidates = [json.loads(s) for s in (OUT/"candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    ids = set()
    for row in candidates:
        assert row["candidate_id"] not in ids
        ids.add(row["candidate_id"])
        assert row["cutoff_proven"] is False
        assert datetime.fromisoformat(row["observed_at"]) >= datetime.fromisoformat(row["source_reported_published_at"].replace("Z","+00:00"))
        assert row["source_reported_published_at"] and row["author"]
        assert row["source_reported_published_at"] != row["source_reported_modified_at"] or row["source"] in {"Covers","TonysPicks"}
        if row["reported_decimal_odds"]:
            assert abs(row["reported_decimal_odds"]-american_decimal(row["reported_american_odds"])) < 1e-12
        if row["source"] == "TonysPicks":
            doc = read_json("documents.json",{})[row["url"]]
            evidence = " ".join(doc.get("recommendation_extracts",[]) + doc.get("odds_contexts",[]) + doc.get("headings",[]))
            assert row["evidence_quote"] in evidence, (row["url"],"quote not in saved extract")
        if row["status"] == "candidate":
            assert len(row["teams"]) == 2 and row["market"] in {"moneyline","spread","total"}
            assert row["reported_decimal_odds"] > 1
        result = row["official_result"]
        if result:
            assert result["match_date"] == row["match_date"]
            assert result["official_result_url"].startswith(("https://npb.jp/","https://statsapi.mlb.com/"))
            assert match_result(row,[result]) == result
            wrong_day = dict(result,match_date="2000-01-01")
            assert match_result(row,[wrong_day]) is None
            assert match_result(row,[result,result]) is None  # ambiguous/doubleheader not silently chosen
    under = next(r for r in candidates if r["source"] == "Covers" and r["market"] == "total")
    assert under["line"] == 8 and under["reported_american_odds"] == -105  # not current O/U 8.5/-120
    historic = next(r for r in candidates if "wild-card-wednesday-predictions" in r["url"])
    assert historic["match_date"] == "2025-10-01"  # not update label's 2026 year
    duplicate = [r for r in candidates if "duplicate_of" in r]
    assert len(duplicate) == 1 and duplicate[0]["duplicate_of"] in ids
    assert len(read_json("requests.json",[])) + 11 <= 60
    result = {"checked_at":now(),"result":"passed","candidate_rows_validated":len(candidates),
        "checks":["American plus/minus conversion and rejection", "spread/total/prop preserved", "playable-to odds rejected",
            "original Covers Under8/-105 preserved vs odds-table 8.5/-120", "first-publication year 2025 preserved",
            "source-author-game deduplication", "unique exact official dates/teams; ambiguity rejected",
            "no independent cutoff claims", "request cap", "required fields and odds arithmetic"]}
    write_json("verification.json",result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["probe", "build", "check"])
    parser.add_argument("urls", nargs="*")
    args = parser.parse_args()
    if args.command == "probe":
        for url in args.urls:
            doc = fetch(url)
            print(json.dumps({k: v for k, v in doc.items() if k not in {"official_data", "official_tables"}}, ensure_ascii=False)[:15500])
    elif args.command == "build":
        build()
    else:
        check()


if __name__ == "__main__":
    main()
