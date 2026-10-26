"""Bounded public PickDawgz MLB corpus; local artifacts only, no paid endpoints.

Requires beautifulsoup4. Example:
 python scripts/collect_pickdawgz_corpus.py --out LOCAL_DIR --official RESULTS_JSON \
   --games-csv GAMES_CSV --comparison WSTV_ARTICLES_JSON

Discovery: advertised free-pick sitemaps 300..320 (July/June 2025 at discovery).
Process in deterministic URL order per sitemap; stop at target unique games.
Never select on outcomes. Only author-attributed final Pick heading + full-game
moneyline qualifies. Numeric spread/total/inning markets and ambiguity fail closed.
Declared publish AND modified timestamps must precede source and official kickoff.
These are retrospective live pages, NOT independently archived pregame versions.
No proxy, login, challenge solving, alternate-host retries, or premium content.
403/429, access challenges and connection failures stop the entire network run.
--offline replays cached pages; existing cache preserves exact original bytes.
Output files belong exclusively to this collector and are refreshed on each run.
"""

import argparse
import csv
import hashlib
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
BASE = "https://pickdawgz.com"
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class Excluded(ValueError):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Fetcher:
    def __init__(self, out, offline=False):
        self.cache = out / "cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.sources = {}

    def get(self, url):
        if not url.startswith(BASE + "/"):
            raise ValueError("Unexpected external URL")
        path = self.cache / (sha(url.encode()) + ".html")
        meta_path = path.with_suffix(".json")
        if path.exists():
            raw = path.read_bytes()
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta["sha256"] != sha(raw) or meta["url"] != url:
                raise ValueError("Cache integrity failure")
        elif self.offline:
            raise FileNotFoundError(url)
        else:
            time.sleep(0.4)
            request = urllib.request.Request(url, headers={"User-Agent": "public-article-research/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                if not response.url.startswith(BASE + "/"):
                    raise RuntimeError("Unexpected redirect")
                raw = response.read()
            if any(x in raw.lower() for x in [b"cf-chl-", b"verify you are human", b"access denied"]):
                raise RuntimeError("Access challenge: stop without bypass")
            meta = {"url": url, "observed_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": sha(raw), "bytes": len(raw), "cache_file": path.name}
            path.write_bytes(raw)
            save(meta_path, meta)
        self.sources[url] = meta
        return raw, meta


def clean_team(value):
    value = re.sub(r"^\d+\s+", "", value.strip())
    return re.sub(r"\s+[+-]?\d+(?:\.\d+)?$", "", value)


def load_odds(path, year):
    index = defaultdict(list)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["year"] != str(year) or row["league"] != "MLB" or row["market_family"] != "승패" or row["n_way"] != "2":
                continue
            if row["is_void"].lower() == "true":
                continue
            date = re.fullmatch(r"(\d{2})\.(\d{2})\([^)]*\) (\d{2}):(\d{2})", row["date_text"])
            if not date:
                continue
            month, day, hour, minute = map(int, date.groups())
            kickoff = datetime(year, month, day, hour, minute, tzinfo=KST)
            home, away = clean_team(row["home"]), clean_team(row["away"])
            odds = [float(x) for x in row["odds"].split(",")]
            if len(odds) != 2 or min(odds) <= 1:
                continue
            index[(kickoff.date().isoformat(), home, away)].append({
                "year": year, "round": row["round"], "game_no": row["game_no"],
                "kickoff": kickoff.isoformat(), "odds": odds,
                "market_family": row["market_family"], "result_native": row["result"],
            })
    return index


def team_key(team):
    return "athletics" if team in ("Oakland Athletics", "Athletics") else team.lower()


def parse_pick(text, teams):
    match = re.fullmatch(r"(?P<author>.+?)['’]s Pick:\s*(?P<team>[A-Za-z .'-]+?)(?:\s+(?P<odds>[+-]\d{3,4}))?", text)
    if not match:
        raise Excluded("not_explicit_full_game_moneyline")
    name = match["team"].strip().lower()
    matches = [team for team in teams if name == team.lower() or name == team_key(team)
               or team.lower().endswith(" " + name) or team.lower().startswith(name + " ")]
    if len(matches) != 1:
        raise Excluded("ambiguous_pick_team")
    return match["author"], matches[0], int(match["odds"]) if match["odds"] else None


def parse_article(raw, url):
    soup = BeautifulSoup(raw, "html.parser")
    content = soup.select_one("article .entry-content")
    title = soup.select_one("article h1")
    if content is None or title is None:
        raise Excluded("missing_full_article")
    author_node = soup.select_one("article header .wp-block-post-author__name")
    if author_node is None:
        raise Excluded("missing_author")
    author = author_node.get_text(" ", strip=True)
    picks = [h for h in content.select("h2,h3,h4") if re.match(r".+['’]s Pick:", h.get_text(" ", strip=True))]
    if len(picks) != 1:
        raise Excluded("missing_or_multiple_pick_heading")
    table = content.select_one(".free-pick-table")
    if table is None:
        raise Excluded("missing_match_table")
    teams = [h.get_text(" ", strip=True) for h in table.select("thead th")][1:]
    if len(teams) != 2:
        raise Excluded("missing_match_teams")
    native_pick = picks[0].get_text(" ", strip=True)
    pick_author, team, odds = parse_pick(native_pick, teams)
    if pick_author != author:
        raise Excluded("author_mismatch")
    info = {tr.th.get_text(" ", strip=True): tr.td.get_text(" ", strip=True)
            for tr in table.select("tbody tr") if tr.th and tr.td}
    clock = re.fullmatch(r"(\d{2}:\d{2} [AP]M) (EDT|EST)", info.get("Time", ""))
    if not clock:
        raise Excluded("missing_explicit_game_timezone")
    zone = timezone(timedelta(hours=-4 if clock[2] == "EDT" else -5))
    kickoff = datetime.strptime(info["When"] + " " + clock[1], "%A, %B %d, %Y %I:%M %p").replace(tzinfo=zone)
    schemas = []
    for script in soup.select('script[type="application/ld+json"]'):
        schemas.extend(json.loads(script.string).get("@graph", []))
    pages = [x for x in schemas if x.get("@type") == "WebPage" and x.get("url") == url]
    if len(pages) != 1:
        raise Excluded("missing_page_timestamps")
    page = pages[0]
    published = datetime.fromisoformat(page["datePublished"])
    modified = datetime.fromisoformat(page["dateModified"])
    if not published.tzinfo or not modified.tzinfo:
        raise Excluded("naive_publication_time")
    if modified < published or max(published, modified) >= kickoff:
        raise Excluded("published_or_modified_after_source_kickoff")
    displayed = soup.select_one("article time[datetime]")
    if displayed is None or datetime.fromisoformat(displayed["datetime"]) != published:
        raise Excluded("displayed_timestamp_mismatch")
    body = content.get_text(" ", strip=True)
    return {"native_title": title.get_text(" ", strip=True), "body": body,
            "body_sha256": sha(body.encode()), "author": author, "url": url,
            "native_pick": native_pick, "selected_native_team": team, "native_moneyline": odds,
            "native_matchup": teams, "native_match_table": info,
            "native_displayed_published": displayed.get_text(" ", strip=True),
            "native_date_published": page["datePublished"], "native_date_modified": page["dateModified"],
            "published_at": published.astimezone(KST).isoformat(),
            "modified_at": modified.astimezone(KST).isoformat(),
            "displayed_published": published.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
            "native_kickoff": kickoff.isoformat(),
            "pick_extraction": {"selector": "article .entry-content h2,h3,h4",
                                "text": native_pick, "rule": "author's Pick: unique team, optional American moneyline; no spread/total"},
            "pregame_evidence": "live-page declared published/modified times only; no independent archived snapshot",
            "full_article": True, "source_site": "pickdawgz.com", "category": "MLB"}


def join_article(row, official, odds_index, year):
    kickoff = datetime.fromisoformat(row["native_kickoff"]).astimezone(KST)
    if kickoff.year != year:
        raise Excluded("outside_target_year")
    teamset = {team_key(t) for t in row["native_matchup"]}
    matches = [r for r in official if r["league"] == "MLB" and r["date"] == kickoff.date().isoformat()
               and {team_key(t) for t in r["raw_team_names"].values()} == teamset]
    if len(matches) > 1:
        matches = [r for r in matches if not r.get("kickoff_is_tbd") and datetime.fromisoformat(r["kickoff"]) == kickoff]
    if len(matches) != 1 or matches[0].get("kickoff_is_tbd"):
        raise Excluded("ambiguous_or_missing_official_game")
    game = matches[0]
    official_time = datetime.fromisoformat(game["kickoff"])
    delta = abs((kickoff - official_time).total_seconds())
    if delta > 3600:
        raise Excluded("official_kickoff_diff_exceeds_one_hour")
    if max(datetime.fromisoformat(row[k]) for k in ("published_at", "modified_at")) >= official_time:
        raise Excluded("published_or_modified_after_official_kickoff")
    odds = odds_index.get((game["date"], game["home"], game["away"]), [])
    odds = [r for r in odds if datetime.fromisoformat(r["kickoff"]) == official_time.replace(second=0, microsecond=0)]
    if len(odds) != 1:
        raise Excluded("ambiguous_or_missing_exact_proto_odds")
    selected = team_key(row["selected_native_team"])
    sides = [s for s in ("home", "away") if team_key(game["raw_team_names"][s]) == selected]
    if len(sides) != 1:
        raise Excluded("pick_team_not_official_matchup")
    side = sides[0]
    day = official_time.date()
    row.update(title=f"[MLB] {year}년 {day.month}월 {day.day}일 {game['home']} vs {game['away']}",
               pick=game[side] + " 승", selection=side, sport="bs", league="MLB",
               date=game["date"], kickoff=game["kickoff"], home=game["home"], away=game["away"],
               official_id=game["official_id"], official_source_url=game["source_url"],
               official_game_number=game.get("game_number"), kickoff_difference_seconds=delta,
               home_score=game["home_score"], away_score=game["away_score"], proto_odds=odds[0])
    return row


def shingles(text):
    text = re.sub(r"[^a-z0-9가-힣]", "", text.lower())
    return {text[i:i + 16] for i in range(max(0, len(text) - 15))}


def copy_check(row, comparisons):
    current = shingles(row["body"])
    best, best_url = 0.0, None
    for other, grams in comparisons:
        score = len(current & grams) / max(1, min(len(current), len(grams)))
        if score > best:
            best, best_url = score, other.get("url")
    row["copy_check"] = {"reference": "supplied WSTV corpus", "max_16char_shingle_containment": best,
                         "closest_url": best_url, "translation_copy_checked": False}
    if best >= 0.65:
        raise Excluded("possible_wstv_copy")


def run(args):
    args.out.mkdir(parents=True, exist_ok=True)
    fetch = Fetcher(args.out, args.offline)
    official = json.loads(args.official.read_bytes())
    odds = load_odds(args.games_csv, args.year)
    reference = json.loads(args.comparison.read_bytes())
    comparisons = [(r, shingles(r.get("body", ""))) for r in reference]
    accepted, excluded, discovered = [], [], []
    seen, games, bodies = set(), set(), set()
    fatal = None
    try:
        # Preserve public discovery provenance; no fabricated/unadvertised endpoints.
        fetch.get(BASE + "/robots.txt")
        index, _ = fetch.get(BASE + "/sitemap_index.xml")
        advertised = {x.text for x in ET.fromstring(index).findall("s:sitemap/s:loc", NS)}
        for number in range(args.start_sitemap, args.end_sitemap + 1):
            sitemap = BASE + f"/free-pick-sitemap{number}.xml"
            if sitemap not in advertised:
                raise RuntimeError("Sitemap not advertised")
            raw, _ = fetch.get(sitemap)
            urls = sorted({x.text for x in ET.fromstring(raw).findall("s:url/s:loc", NS)
                           if "/mlb-picks/" in x.text and str(args.year) in x.text})
            for url in urls:
                if url in seen:
                    continue
                seen.add(url)
                discovered.append({"url": url, "sitemap": sitemap})
                raw, meta = fetch.get(url)
                try:
                    row = join_article(parse_article(raw, url), official, odds, args.year)
                    if row["official_id"] in games or row["body_sha256"] in bodies:
                        raise Excluded("duplicate_game_or_body")
                    copy_check(row, comparisons)
                    row.update(observed_at=meta["observed_at"], raw_html_sha256=meta["sha256"],
                               raw_cache_file=meta["cache_file"], listing=sitemap)
                    accepted.append(row)
                    games.add(row["official_id"])
                    bodies.add(row["body_sha256"])
                except (Excluded, KeyError, ValueError) as error:
                    excluded.append({"url": url, "reason": str(error), "raw_html_sha256": meta["sha256"]})
                if len(seen) % 20 == 0:
                    save(args.out / "articles.json", accepted)
                    print(f"pages={len(seen)} accepted={len(accepted)} excluded={len(excluded)}", flush=True)
                if len(accepted) >= args.target:
                    break
            if len(accepted) >= args.target:
                break
    except Exception as error:
        fatal = f"{type(error).__name__}: {error}"
    save(args.out / "articles.json", accepted)
    save(args.out / "excluded.json", excluded)
    save(args.out / "discovered.json", discovered)
    save(args.out / "sources.json", list(fetch.sources.values()))
    summary = {"accepted": len(accepted), "distinct_official_games": len(games), "target": args.target,
               "discovered": len(discovered), "excluded": dict(Counter(r["reason"] for r in excluded)),
               "authors": dict(Counter(r["author"] for r in accepted)), "fatal": fatal,
               "date_range": [min((r["date"] for r in accepted), default=None), max((r["date"] for r in accepted), default=None)],
               "wstv_reference_articles": len(reference), "wstv_reference_sha256": sha(args.comparison.read_bytes()),
               "official_sha256": sha(args.official.read_bytes()), "games_csv_sha256": sha(args.games_csv.read_bytes()),
               "max_wstv_shingle_containment": max((r["copy_check"]["max_16char_shingle_containment"] for r in accepted), default=None),
               "limitations": ["Retrospective live pages: declared pregame dates are not independent archival proof.",
                               "Text comparison cannot rule out translated copying or shared ownership.",
                               "PickDawgz and Sports Chat Place must not be presumed independent of each other.",
                               "No strategy performance calculated by this collection sidecar."]}
    save(args.out / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if fatal:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("out", "official", "games-csv", "comparison"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--target", type=int, default=120)
    parser.add_argument("--start-sitemap", type=int, default=300)
    parser.add_argument("--end-sitemap", type=int, default=320)
    parser.add_argument("--offline", action="store_true")
    run(parser.parse_args())
