"""Public retrospective basketball archive. Research only; no production imports.

Collect indexed months and linked author/league pagination, never 365 guessed days.
All picks/outcomes/markets survive; only NBA/KBL and the explicit date window pass.
HTTP bodies are gzip snapshots, with hashes of original bytes and no authentication.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/tipster-year-20261028/basketball"
START, END = "2025-10-28", "2026-10-27"
MIKY = "https://miky938-oddsparser.com"
BETBLOG = "https://www.betblog.com"
LEAGUES = {"/league/usa-nba/": "NBA", "/league/south-korea-kbl/": "KBL",
           "/basketball/nba/": "NBA", "/basketball/south-korea-kbl/": "KBL"}
EXTRA_AUTHOR_PATHS = {
    "/tipster/kentukyo/basketball/nba/": "initial search seed",
    "/tipster/kentukyo/basketball/south-korea-kbl/": "initial search seed; old-only records retained in audit",
    "/tipster/faster/basketball/south-korea-kbl/": "initial search seed; old-only records retained in audit",
    "/tipster/nelaime/basketball/south-korea-kbl/": "KBL all-time listing; old-only records retained in audit",
    "/tipster/str1ke/basketball/south-korea-kbl/": "all remaining handles in KBL current-year listing, including negative records",
    "/tipster/thunder/basketball/south-korea-kbl/": "all remaining handles in KBL current-year listing, including negative records",
    "/tipster/haris333/basketball/south-korea-kbl/": "all remaining handles in KBL current-year listing, including negative records",
}
PUBLIC_KBL_HEADERS = {"Channel": "WEB", "TeamCode": "XX", "lang": "ko", "X-Requested-With": "XMLHttpRequest"}
KBL_CODES = {"Goyang Sono": "66", "Anyang JungKwanJang": "70", "Wonju DB": "16",
             "KCC Egis": "60", "Suwon KT": "06", "Seoul Knights": "55", "Mobis Phoebus": "10",
             "KoGas": "64", "LG Sakers": "50", "Seoul Thunders": "35",
             "Goyang Sono Skygunners": "66", "Anyang Red Boosters": "70", "Dongbu Promy": "16",
             "Busan Sonic Boom KT": "06", "KT Sonicboom": "06", "Daegu KOGAS Pegasus": "64",
             "Changwon LG Sakers": "50", "Seoul Samsung Thunders": "35",
             "Goyang Orions": "66", "Goyang": "66", "Seoul SK Knights": "55", "Kogas": "64",
             "Anyang": "70", "Sonic Boom": "06", "Daegu Pegasus": "64"}
NBA_NAMES = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets", "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers", "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons", "GS": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "Los Angeles Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies", "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves", "NO": "New Orleans Pelicans", "NY": "New York Knicks",
    "OKC": "Oklahoma City Thunder", "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SA": "San Antonio Spurs", "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}
NBA_ALIASES = {name.lower(): code for code, name in NBA_NAMES.items()}
NBA_ALIASES.update({(code + " " + name.split(" ", 1)[1] if code not in {"GS", "NO", "NY", "OKC", "SA", "LAC", "LAL", "POR"}
                    else code + " " + {"GS": "Warriors", "NO": "Pelicans", "NY": "Knicks", "OKC": "Thunder", "SA": "Spurs", "LAC": "Clippers", "LAL": "Lakers", "POR": "Trail Blazers"}[code]).lower(): code
                   for code, name in NBA_NAMES.items()})
NBA_ALIASES.update({"gsw warriors": "GS", "nop pelicans": "NO", "nyk knicks": "NY", "sas spurs": "SA",
                    "la lakers": "LAL", "la clippers": "LAC"})


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")


def ledger():
    return json.loads((OUT / "requests.json").read_text(encoding="utf-8")) if (OUT / "requests.json").exists() else []


def body(meta):
    data = (ROOT / meta["raw_path"]).read_bytes()
    return gzip.decompress(data) if meta.get("raw_encoding") == "gzip" else data


def fetch(url, headers=None):
    entries = ledger()
    prior = next((x for x in entries if x["url"] == url), None)
    if prior:
        return prior
    if len(entries) >= 160:
        raise RuntimeError("160-request task cap reached")
    meta = {"url": url, "observed_at": now(), "automatic_retries": 0}
    if headers:
        meta["public_request_headers"] = headers
    entries.append(meta)
    dump(OUT / "requests.json", entries)
    try:
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=False)
        data = response.content
        path = OUT / "raw" / (hashlib.sha256(url.encode()).hexdigest()[:20] + ".gz")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(data, mtime=0))
        meta.update(status=response.status_code, raw_path=path.relative_to(ROOT).as_posix(),
                    raw_encoding="gzip", sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        if response.status_code != 200:
            meta["error"] = f"HTTP {response.status_code}; not retried or bypassed"
    except requests.RequestException as exc:
        meta["error"] = f"{type(exc).__name__}: {exc}"
    dump(OUT / "requests.json", entries)
    print(json.dumps({"request": len(entries), "url": url, "status": meta.get("status"), "error": meta.get("error")}))
    return meta


def text(node, selector):
    found = node.select_one(selector)
    return found.get_text(" ", strip=True) if found else ""


def prop(node, selector):
    found = node.select_one(selector)
    return found.get("content") if found else None


def timestamp(raw):
    return datetime.fromisoformat(raw.replace("GMT", "T")).isoformat() if raw else None


def evidence(meta, ordinal):
    return {k: meta[k] for k in ("url", "observed_at", "raw_path", "sha256")} | {"row_ordinal": ordinal}


def normalize_market(raw, home, away, default_scope="unverified"):
    """Conservative normalization; preserve unsupported/player/period claims verbatim."""
    market, side, line, scope = "unparsed", None, None, default_scope
    s = re.sub(r"\s+", " ", raw).strip()
    if s == "H/A, OT":
        return "moneyline", None, None, "full_game_including_overtime"
    if s.startswith("O/U "):
        m = re.match(r"O/U ([+-]?\d+(?:\.\d+)?)(, OT)?$", s)
        if m:
            return "total", None, float(m[1]), "full_game_including_overtime" if m[2] else "unverified"
    clean = s.strip(" ()")
    scope = "full_game_including_overtime" if "+OT" in clean else "unverified_overtime_rule"
    if re.search(r"serijos|series", clean, re.I):
        return "series_winner", None, None, "series_not_single_game"
    period = re.search(r"1st half|1HF|1 rungt\. pusė", clean, re.I)
    if period:
        direction = re.search(r"\b(over|under)\b", clean, re.I)
        remaining = clean[:period.start()] + clean[period.end():]
        numbers = re.findall(r"[+-]?\d+(?:[.,]\d+)?", remaining)
        return ("period_total" if direction else "period_spread", direction[1].lower() if direction else None,
                float(numbers[-1].replace(",", ".")) if numbers else None, "first_half")
    for name, team_side in ((home, "home"), (away, "away")):
        canonical = NBA_ALIASES.get(name.lower())
        names = [name] + [alias for alias, code in NBA_ALIASES.items() if canonical and code == canonical]
        for alias in names:
            escaped = re.escape(alias)
            if re.fullmatch(r"(?:Winner\s+)?" + escaped, clean, re.I):
                return "moneyline", team_side, None, scope
            spread = re.fullmatch(r"([+-]\d+(?:\.\d+)?)\s+" + escaped, clean, re.I)
            trailing_spread = re.fullmatch(escaped + r"\s*\(([+-]\d+(?:\.\d+)?)", clean, re.I)
            if spread or trailing_spread:
                return "spread", team_side, float((spread or trailing_spread)[1]), scope
            patterns = [r"(Over|Under)\s*\(?" + escaped + r"\s+(\d+(?:\.\d+)?)",
                        r"(Over|Under)\s+(\d+(?:\.\d+)?)\s+" + escaped]
            for pattern in patterns:
                total = re.fullmatch(pattern, clean, re.I)
                if total:
                    return "team_total", team_side + "_" + total[1].lower(), float(total[2]), scope
            total = re.fullmatch(escaped + r"\s*\((Over|Under)\s+(\d+(?:\.\d+)?)\s*(?:pts)?", clean, re.I)
            if total:
                return "team_total", team_side + "_" + total[1].lower(), float(total[2]), scope
    # Plain team totals only; do not mistake player props or first-half totals for game totals.
    m = re.fullmatch(r"(Over|Under)\s*\(?\s*(?:Total\s*)?(?:\(\+OT\)\s*)?([0-9]+(?:[.,][0-9]+)?)\s*\)?", clean, re.I)
    if m:
        market, side, line = "total", m[1].lower(), float(m[2].replace(",", "."))
        scope = "full_game_including_overtime" if "+OT" in clean else "unverified_overtime_rule"
    return market, side, line, scope


def parse_miky(data, meta):
    soup = BeautifulSoup(data, "html.parser")
    rows, exclusions = [], Counter()
    for ordinal, tr in enumerate(soup.select("tbody tr"), 1):
        league_node = tr.select_one("td.game .league")
        league = LEAGUES.get(league_node.get("href")) if league_node else None
        if not league:
            exclusions["other_league_or_summary_row"] += 1
            continue
        a = tr.select_one("td.when a[href]")
        date_match = re.search(r"/tips/(\d{4}-\d{2}-\d{2})/", a.get("href", "") if a else meta["url"])
        if not date_match:
            raise ValueError("Miky row missing date URL")
        day = date_match[1]
        if not START <= day <= END:
            exclusions["outside_date_window"] += 1
            continue
        fixture = " ".join(str(t).strip() for t in tr.select_one("td.game").find_all(string=True, recursive=False)).lstrip("🏀• \t\n")
        if fixture.count(" - ") != 1:
            raise ValueError(f"Ambiguous fixture {fixture}")
        home, away = fixture.split(" - ")
        raw_market, side = text(tr, "td.market"), text(tr, "td.pick").lower()
        market, _, line, scope = normalize_market(raw_market, home, away)
        score = re.search(r"(\d+)\s*:\s*(\d+)", text(tr, "td.res"))
        row = dict(source="Miky938 Tips", author="Miky938", author_id="miky938", author_identity_status="publisher_handle_person_unknown",
                   league=league, match_date=day, match_date_basis="source_daily_url_WAT_UTC+01", teams={"home": home, "away": away},
                   source_url=urljoin(MIKY, a["href"]) if a else meta["url"], observed_at=meta["observed_at"],
                   published_at_claim=None, source_reported_published_at=None, cutoff_proven=False,
                   raw_fixture_text=fixture, market=market, market_raw=raw_market, side=side, side_raw=text(tr, "td.pick"), line=line, market_scope=scope,
                   reported_decimal_odds=float(text(tr, "td.odds")), reported_bookmaker=text(tr, "td.book"),
                   source_reported_result=text(tr, "td.res .badge"), source_reported_result_raw=text(tr, "td.res"),
                   source_reported_score={"home": int(score[1]), "away": int(score[2])} if score else None,
                   source_reported_profit=text(tr, "td.profit"), evidence=[evidence(meta, ordinal)])
        identity = [row[k] for k in ("source", "author_id", "league", "match_date", "teams", "market_raw", "side", "reported_decimal_odds", "reported_bookmaker")]
        row["candidate_id"] = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        rows.append(row)
    return rows, dict(exclusions)


def parse_betblog(data, meta):
    soup = BeautifulSoup(data, "html.parser")
    rows, exclusions = [], Counter()
    for ordinal, li in enumerate(soup.select('li[itemtype="http://schema.org/SportsEvent"]'), 1):
        league_url = prop(li, '[itemprop="location"] [itemprop="url"]')
        league = next((v for k, v in LEAGUES.items() if league_url == BETBLOG + k), None)
        if not league:
            exclusions["other_league"] += 1
            continue
        start_raw = prop(li, '[itemprop="startDate"]')
        started = timestamp(start_raw)
        if not started or not START <= started[:10] <= END:
            exclusions["outside_date_window_or_missing_date"] += 1
            continue
        fixture = prop(li, ':scope > [itemprop="name"]') or text(li, ".match > a")
        if fixture.count(" - ") != 1 or any(x in fixture.lower() for x in ["winner", "player", "playoffs", "champion", "conference"]):
            exclusions["not_single_fixture_futures"] += 1
            continue
        home, away = fixture.split(" - ")
        hover = text(li, ".hover span")
        odds = re.search(r"Pick @ ([0-9.]+)", hover)
        pick = re.search(r"Pick:\s*(.*?)\s*Units:", hover)
        if not odds or not pick:
            raise ValueError("Betblog missing odds or selection")
        author = prop(li, '[itemprop="performer"] [itemprop="name"]')
        author_url = prop(li, '[itemprop="performer"] [itemprop="url"]')
        published_raw = prop(li, '[itemprop="validFrom"]')
        published = timestamp(published_raw)
        market_raw = re.sub(r"\s+", " ", pick[1]).strip()
        market, side, line, scope = normalize_market(market_raw, home, away)
        a = li.select_one('.match > a[href^="/picks/"]')
        profit_raw = text(li, ".units")
        try:
            profit = float(profit_raw)
            result = "Won" if profit > 0 else "Lost" if profit < 0 else "Void_or_zero_profit"
        except ValueError:
            result = "Unknown"
        row = dict(source="Betblog", author=author, author_id=author_url, author_identity_status="public_handle_person_unknown" if author else "unknown",
                   league=league, match_date=started[:10], match_date_basis="source_schema_startDate_UTC_claim", source_reported_game_time_utc=started,
                   source_reported_game_time_raw=start_raw, teams={"home": home, "away": away}, raw_fixture_text=fixture,
                   source_url=urljoin(BETBLOG, a["href"]), observed_at=meta["observed_at"],
                   published_at_claim=published, source_reported_published_at=published, published_at_raw=published_raw,
                   published_before_game_claim=published < started if published else None, cutoff_proven=False,
                   market=market, market_raw=market_raw, side=side, side_raw=market_raw, line=line, market_scope=scope,
                   reported_decimal_odds=float(odds[1]), reported_bookmaker=prop(li, '[itemprop="offers"] [itemprop="url"]'),
                   source_reported_result=result, source_reported_result_raw=profit_raw,
                   source_reported_score=None, source_reported_profit=profit_raw, evidence=[evidence(meta, ordinal)])
        row["candidate_id"] = "betblog-" + re.search(r"/picks/(\d+)", row["source_url"])[1]
        rows.append(row)
    return rows, dict(exclusions)


def pagination(soup, base):
    return sorted({urljoin(base, a["href"]) for a in soup.select(".pagination a[href]")})


def collect():
    index = fetch(MIKY + "/results/")
    soup = BeautifulSoup(body(index), "html.parser")
    months = sorted({urljoin(MIKY, a["href"]) for a in soup.select('a[href]')
                     if re.fullmatch(r"/results/202[56]-\d{2}/", a["href"])})
    for url in months:
        fetch(url)
    build()  # first useful checkpoint before author expansion and official calls
    for path in ("/league/usa-nba/", "/league/south-korea-kbl/"):
        fetch(MIKY + path)
    # Use observed pick authors, never leaderboard/performance ranking, for expansion.
    author_roots = set()
    for path in ("/basketball/nba/", "/basketball/south-korea-kbl/"):
        meta = fetch(BETBLOG + path)
        if meta.get("status") != 200:
            continue
        s = BeautifulSoup(body(meta), "html.parser")
        todo = pagination(s, BETBLOG)
        for url in todo:
            fetch(url)
        for m in ledger():
            if m.get("status") == 200 and m["url"].startswith(BETBLOG + path):
                for li in BeautifulSoup(body(m), "html.parser").select('li[itemtype="http://schema.org/SportsEvent"]'):
                    author_url = prop(li, '[itemprop="performer"] [itemprop="url"]')
                    if author_url:
                        author_roots.add(author_url.rstrip("/") + path)
    # Known public profile discovered in the initial search, regardless of performance.
    author_roots.update(BETBLOG + path for path in EXTRA_AUTHOR_PATHS)
    dump(OUT / "discovery.json", {"miky_indexed_month_urls": months, "betblog_author_league_roots": sorted(author_roots),
                                  "selection_rule": "all authors present on captured NBA/KBL league cards; no leaderboard filtering; Kentukyo search seed",
                                  "additional_discovered_author_seeds": EXTRA_AUTHOR_PATHS,
                                  "selection_bias": "KBL current-year and one all-time listing used for additional discovery; this is not a random sample of all tipsters",
                                  "limits": "160 requests total; stop author branch when page has no in-window single fixtures"})
    for root in sorted(author_roots):
        if len(ledger()) >= 150:
            break
        queue, seen = [root], set()
        while queue and len(ledger()) < 150:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            meta = fetch(url)
            if meta.get("status") != 200:
                continue
            s = BeautifulSoup(body(meta), "html.parser")
            rows, _ = parse_betblog(body(meta), meta)
            if not rows:
                break
            # Next only: preserve traversal and stop at the old boundary, no arbitrary deep jumps.
            next_link = s.select_one('.pagination li[data-page="next"] a[href]')
            if next_link:
                queue.append(urljoin(BETBLOG, next_link["href"]))
        build()  # every completed author is a consumable checkpoint
    fetch("https://api.kbl.or.kr/match/list?fromDate=20251028&toDate=20261027&tcodeList=all", PUBLIC_KBL_HEADERS)
    fetch("https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json")
    fetch("https://stats.nba.com/stats/leaguegamefinder?LeagueID=00&Season=2025-26&SeasonType=Regular%20Season")


def official_kbl():
    output = []
    for meta in ledger():
        if meta.get("status") != 200 or not meta["url"].startswith("https://api.kbl.or.kr/match/list"):
            continue
        games = json.loads(body(meta))
        if not isinstance(games, list):
            raise ValueError("Official KBL did not return game array")
        for game in games:
            day = datetime.strptime(game["gameDate"], "%Y%m%d").date().isoformat()
            if not START <= day <= END:
                raise ValueError("Official API date outside requested window")
            ended = game["isEnded"] == 1
            if ended and any(type(game[x]) is not int or game[x] < 0 for x in ("scoreH", "scoreA")):
                raise ValueError("Invalid official score")
            output.append(dict(league="KBL", official_id=game["gmkey"], match_date=day,
                               home_id=game["tcodeH"], away_id=game["tcodeA"], is_ended=ended,
                               teams={"home": game["tnameFH"], "away": game["tnameFA"]},
                               home_score=game["scoreH"] if ended else None, away_score=game["scoreA"] if ended else None,
                               source_url=meta["url"], observed_at=meta["observed_at"], evidence=evidence(meta, None)))
    return output


def attach_official(rows, games):
    index = defaultdict(list)
    for g in games:
        index[(g["match_date"], g["home_id"], g["away_id"])].append(g)
    for r in rows:
        r.update(official_result=None, official_result_provenance=None)
        if r["league"] != "KBL":
            r["official_validation_status"] = "nba_official_unavailable"
            continue
        key = (r["match_date"], KBL_CODES.get(r["teams"]["home"]), KBL_CODES.get(r["teams"]["away"]))
        matches = index.get(key, [])
        status = "unknown_team_alias" if None in key else "no_exact_date_home_away_match"
        if len(matches) > 1:
            status = "ambiguous_official_match"
        elif matches:
            g = matches[0]
            r["official_result_provenance"] = {k: g[k] for k in ("official_id", "source_url", "observed_at", "evidence")}
            r["official_result_provenance"]["join_rule"] = "exact_date_and_explicit_home_away_codes_no_score_matching"
            status = "official_not_ended"
            if g["is_ended"]:
                h, a = g["home_score"], g["away_score"]
                r["official_result"] = {"home_score": h, "away_score": a, "pick_result": None}
                status = "official_score_joined_market_not_settled"
                if r["market_scope"] == "full_game_including_overtime":
                    margin = None
                    if r["market"] == "moneyline" and r["side"] in {"home", "away"}:
                        margin = (h - a) * (1 if r["side"] == "home" else -1)
                    elif r["market"] == "total" and r["side"] in {"over", "under"} and r["line"] is not None and (r["line"] * 2).is_integer():
                        margin = (h + a - r["line"]) * (1 if r["side"] == "over" else -1)
                    if margin is not None:
                        result = "Won" if margin > 0 else "Lost" if margin < 0 else "Push"
                        r["official_result"]["pick_result"] = result
                        status = "official_score_and_pick_settled"
                if r["source_reported_score"] is not None:
                    r["publisher_official_score_matches"] = r["source_reported_score"] == {"home": h, "away": a}
                if r["official_result"]["pick_result"] is not None:
                    r["publisher_official_outcome_matches"] = r["source_reported_result"] == r["official_result"]["pick_result"]
        r["official_validation_status"] = status


def fixture_key(row):
    # Verified official IDs supersede aliases; unknown aliases stay separate, never fuzzy-merge.
    if row.get("official_result_provenance"):
        return (row["league"], row["official_result_provenance"]["official_id"])
    names = [NBA_ALIASES.get(row["teams"][s].lower(), re.sub(r"[^a-z0-9]", "", row["teams"][s].lower())) for s in ("home", "away")]
    return (row["league"], row["match_date"], *names)


def coverage(rows):
    return dict(picks=len(rows), unique_fixtures=len({fixture_key(r) for r in rows}),
                canonical_single_game_fixtures=len({fixture_key(r) for r in rows if all(r.get("canonical_teams", {}).values()) and r["market"] != "series_winner"}),
                unique_fixtures_basis="official KBL ids else UTC/source date and explicit NBA aliases or exact normalized home/away; unresolved aliases may overcount",
                author_handles=len({(r["source"], r["author_id"]) for r in rows}),
                date_min=min((r["match_date"] for r in rows), default=None), date_max=max((r["match_date"] for r in rows), default=None),
                results=dict(Counter(r["source_reported_result"] for r in rows)),
                markets=dict(Counter(r["market"] for r in rows)),
                official_score_joined_picks=sum(r.get("official_result") is not None for r in rows),
                official_score_joined_unique_fixtures=len({fixture_key(r) for r in rows if r.get("official_result") is not None}),
                publisher_official_score_disagreements=sum(r.get("publisher_official_score_matches") is False for r in rows),
                publisher_official_outcome_disagreements=sum(r.get("publisher_official_outcome_matches") is False for r in rows),
                official_validation=dict(Counter(r["official_validation_status"] for r in rows)))


def build():
    observations, audits = [], []
    for meta in ledger():
        if meta.get("status") != 200:
            continue
        if meta["url"].startswith(MIKY):
            rows, excluded = parse_miky(body(meta), meta)
        elif meta["url"].startswith(BETBLOG):
            rows, excluded = parse_betblog(body(meta), meta)
        else:
            continue
        observations.extend(rows)
        audits.append(dict(url=meta["url"], picks=len(rows), exclusions=excluded))
    jsonl(OUT / "source-observations.jsonl", observations)
    selected, conflicts = {}, []
    for row in observations:
        prior = selected.get(row["candidate_id"])
        if prior:
            differences = {k: [prior[k], row[k]] for k in ("source_reported_result", "source_reported_score", "reported_decimal_odds", "market_raw", "published_at_claim") if prior[k] != row[k]}
            if differences:
                conflicts.append(dict(candidate_id=row["candidate_id"], differences=differences))
            prior["evidence"].extend(row["evidence"])
        else:
            selected[row["candidate_id"]] = row
    rows = sorted(selected.values(), key=lambda r: (r["match_date"], r["league"], r["candidate_id"]))
    games = official_kbl()
    attach_official(rows, games)
    for row in rows:
        if row["reported_decimal_odds"] <= 1:
            raise ValueError("Invalid decimal odds")
        row["canonical_teams"] = {side: (NBA_ALIASES.get(name.lower()) if row["league"] == "NBA" else KBL_CODES.get(name))
                                  for side, name in row["teams"].items()}
        row["team_side"] = row["side"] if row["side"] in {"home", "away"} else None
        if row["market"] == "team_total" and row["side"]:
            row["team_side"], row["side"] = row["side"].split("_", 1)
        row["team_identity_status"] = "explicit_alias_mapping" if all(row["canonical_teams"].values()) else "unresolved_source_team_label"
        row["fixture_group_key"] = list(fixture_key(row))
    jsonl(OUT / "candidates.jsonl", rows)
    for league in ("KBL", "NBA"):
        jsonl(OUT / f"{league.lower()}-candidates.jsonl", [r for r in rows if r["league"] == league])
    jsonl(OUT / "official-kbl-results.jsonl", games)
    dump(OUT / "disagreements.json", [r for r in rows if r.get("publisher_official_score_matches") is False
                                       or r.get("publisher_official_outcome_matches") is False])
    months = ["2025-10", "2025-11", "2025-12"] + [f"2026-{m:02}" for m in range(1, 11)]
    summary = dict(built_at=now(), target_start=START, target_end=END, **coverage(rows),
                   observations=len(observations), duplicates_removed=len(observations)-len(rows), conflicts=conflicts,
                   request_attempts=len(ledger()), official_kbl_games=len(games),
                   months={m: coverage([r for r in rows if r["match_date"].startswith(m)]) for m in months},
                   leagues={l: coverage([r for r in rows if r["league"] == l]) for l in ("KBL", "NBA")},
                   sources={s: coverage([r for r in rows if r["source"] == s]) for s in sorted({r["source"] for r in rows})},
                   authors=[dict(source=s, author_id=a, **coverage([r for r in rows if (r["source"], r["author_id"]) == (s,a)])) for s,a in sorted({(r["source"], r["author_id"]) for r in rows})],
                   page_audits=audits, failures=[m for m in ledger() if m.get("status") != 200],
                   limitations=["Retrospective capture; publication claims do not prove historical pregame availability",
                                "Person identities unknown behind public author handles; no independence assumed",
                                "Same fixture multiple authors/markets is one game when identity resolves",
                                "Unresolved source team aliases and source WAT versus UTC date boundaries may inflate unique fixture count; NBA is not officially confirmed",
                                "NBA official sources unavailable; publisher scores never substituted",
                                "Betblog broad league archive says last 90 days; older expansion uses linked author league records",
                                "No model/hybrid comparison, ROI adoption, production merge or deployment"])
    dump(OUT / "summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("picks", "unique_fixtures", "date_min", "date_max", "author_handles", "leagues")}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true")
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    if args.collect:
        collect()
    if args.build:
        build()


if __name__ == "__main__":
    main()
