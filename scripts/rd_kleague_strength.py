"""Bounded public source discovery and outcome-blind lagged K-league player features.

Research sidecar only. Never import runtime storage or read scores/ROI/model outputs.
Full HTTP cache is local; committed raw-stats are minimal field projections.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

from rd_soccer_features import TEAM_ALIASES, KST, stamp

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/source-link-kleague-20261030"
API = "https://api-gw.sports.naver.com"
PROTOCOL = {
    "version": "kleague-player-strength-v1", "frozen_before_ROI": True,
    "outcomes_read": False, "fit_models": False, "research_loss_cap": None,
    "selection": "same-season, source calendar-end+24h <= decision; exclude exact target fixture",
    "player_identity": "Naver playerId, same team and same season only; no name joins or transfers inferred",
    "reference_xi": "latest eligible historical XI, never target announced XI",
    "valuation": "player goal/assist per nonmissing observed START, from games strictly before reference XI date",
    "features": {
        "xi_goal_known_mean": "mean historical goal/start over reference XI players with >=1 recorded goal field",
        "xi_assist_known_mean": "mean historical assist/start over reference XI players with >=1 recorded assist field",
        "xi_goal_known_share": "fraction of reference XI with observed historical goal/start",
        "xi_assist_known_share": "fraction of reference XI with observed historical assist/start",
        "attack_goal_known_mean": "same goal/start mean restricted to reference XI FW/MF; absent group omitted",
        "rotation_goal_net": "sum entrant goal/start minus sum leaver goal/start, previous -> reference historical XI",
        "rotation_assist_net": "same assist/start; ALL entrants/leavers must have nonmissing history; no rotation omitted",
    },
    "missing": "missing stats remain null; known-subset means carry explicit coverage; no minutes/per90 inference",
    "freshness": "omit entire strength bundle when reference XI >30 local calendar days old",
    "publication": "unknown historical publication/availability stays retrospective; actual ingestion/fetch time separate",
    "tier": "retrospective_lagged (date-only; eligible only under retrospective_date_lagged gate)",
    "network": {"max_requests": 60, "timeout_seconds": 10, "max_bytes": 4000000,
                "deadline_seconds": 240, "gap_seconds": 0.35, "redirects": False,
                "auth": False, "robots": "respect explicit disallow; no inaccessible-site bypass"},
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def write_gz(path, rows):
    data = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n" for r in rows).encode()
    path.write_bytes(gzip.compress(data, mtime=0))


def read_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Fetcher:
    """One process-wide persisted request cap, no retries, no redirect/auth."""
    def __init__(self, out=OUT):
        self.out = out
        self.cache = out / "raw-cache"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.log = out / "http.jsonl"
        self.records = [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []
        self.started = time.monotonic()

    def get(self, url, payload=None, *, form=False):
        u = urlparse(url)
        if u.scheme != "https" or u.hostname not in {"api-gw.sports.naver.com", "www.kleague.com", "data.kleague.com"} or u.username or u.password or u.port not in (None, 443):
            raise ValueError("public allowlisted HTTPS URL required")
        if u.hostname == "data.kleague.com" and u.path != "/robots.txt":
            raise ValueError("data portal robots disallows collection")
        request_data = ((urlencode(payload) if form else json.dumps(payload, sort_keys=True)).encode()
                        if payload is not None else None)
        request_hash = sha(request_data) if request_data else None
        cached = [x for x in self.records if x["url"] == url and x.get("request_sha256") == request_hash]
        if cached:
            r = cached[-1]
            p = self.out / r["cache_path"] if r.get("cache_path") else None
            body = gzip.decompress(p.read_bytes()) if p and p.exists() else None
            if body is not None and sha(body) != r["source_sha256"]:
                raise ValueError("cache hash mismatch")
            return body, r
        if len(self.records) >= PROTOCOL["network"]["max_requests"] or time.monotonic() - self.started > 240:
            raise RuntimeError("bounded collection budget exhausted")
        time.sleep(PROTOCOL["network"]["gap_seconds"])
        r = {"url": url, "request_started_at": now(), "observed_at": None,
             "publication_at": None, "status": None, "reason": None, "source_sha256": None}
        r.update(method="POST" if request_data else "GET", request_sha256=request_hash,
                 public_query=payload)
        body = None
        try:
            req = Request(url, data=request_data, headers={"User-Agent": "KleagueResearch/1.0 (public bounded research)",
                                       "Accept": "application/json,text/html,text/plain;q=0.8",
                                       "Content-Type": ("application/x-www-form-urlencoded; charset=UTF-8" if form
                                                        else "application/json; charset=utf-8")})
            with build_opener(NoRedirect()).open(req, timeout=10) as response:
                r["status"] = response.status
                body = response.read(4000001)
                if len(body) > 4000000:
                    body = None
                    r["reason"] = "response_size_limit"
                r["content_type"] = response.headers.get("Content-Type")
        except HTTPError as exc:
            r.update(status=exc.code, reason="http_error")
            body = exc.read(4000000)
        except (URLError, TimeoutError, OSError):
            r["reason"] = "transport_or_timeout"
        r["observed_at"] = now()
        if body is not None:
            r["source_sha256"] = sha(body)
            r["cache_path"] = "raw-cache/" + r["source_sha256"] + ".gz"
            (self.out / r["cache_path"]).write_bytes(gzip.compress(body, mtime=0))
            r["bytes"] = len(body)
        with self.log.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        self.records.append(r)
        return body, r


def freeze(out=OUT):
    out.mkdir(parents=True, exist_ok=True)
    (out / ".gitattributes").write_bytes(b"* -text\n")
    (out / ".gitignore").write_bytes(b"/raw-cache/\n")
    path = out / "protocol.json"
    if path.exists() and json.loads(path.read_text(encoding="utf-8")) != PROTOCOL:
        raise ValueError("frozen protocol differs")
    if not path.exists():
        write_json(path, PROTOCOL)


def probe(out=OUT):
    freeze(out)
    f = Fetcher(out)
    for url in [API + "/robots.txt", "https://www.kleague.com/robots.txt", "https://data.kleague.com/robots.txt"]:
        body, meta = f.get(url)
        print(json.dumps({"url": url, "status": meta["status"], "reason": meta["reason"],
                          "robots": body.decode("utf-8", errors="replace")[:2500] if body else None}, ensure_ascii=True))


def number(value, upper=100):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= upper:
        return None
    return value


class GroundParser(HTMLParser):
    """The site CSS 'away' also labels the home team: use image team ID instead."""
    def __init__(self):
        super().__init__()
        self.stack = []
        self.ids = defaultdict(list)
        self.ground = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "div":
            self.stack.append(set(a.get("class", "").split()))
            if "ground" in self.stack[-1]:
                self.ground = {"depth": len(self.stack), "teams": set(), "ids": []}
        if self.ground is not None:
            match = re.search(r"/v1/player/(\d{4})/(K\d+)/player_(\d+)\.png", a.get("style", ""))
            if match:
                self.ground["teams"].add((match[1], match[2]))
            # Missing/default headshots do not imply missing player identity.
            if tag == "ul":
                match = re.fullmatch(r"player_(\d{8})", a.get("class", ""))
                if match:
                    self.ground["ids"].append(match[1])

    def handle_endtag(self, tag):
        if tag == "div" and self.stack:
            if self.ground is not None and len(self.stack) == self.ground["depth"]:
                if len(self.ground["teams"]) == 1:
                    self.ids[next(iter(self.ground["teams"]))].extend(self.ground["ids"])
                self.ground = None
            self.stack.pop()


def schedule_rows(body, *, year, league, month):
    b = json.loads(body)
    if str(b.get("resultCode")) != "200":
        raise ValueError("schedule application failure")
    out = []
    for g in (b.get("data") or {}).get("scheduleList", []):
        d = date.fromisoformat(g["gameDate"].replace(".", "-"))
        if d.year != year or d.month != month or int(g["leagueId"]) != league or int(g["year"]) != year:
            raise ValueError("schedule date/league mismatch")
        out.append({k: g[k] for k in ("year", "leagueId", "gameId", "meetSeq", "roundId", "gameDate", "gameTime",
                                      "homeTeam", "awayTeam", "homeTeamName", "awayTeamName")})
    return out


def official_query(g):
    return {"year": str(g["year"]), "yearTst": str(g["year"]), "meetSeq": str(g["meetSeq"]),
            "gameId": str(g["gameId"]), "gameIdTst": str(g["gameId"]), "homeTeam": g["homeTeam"],
            "awayTeam": g["awayTeam"], "roundId": str(g["roundId"])}


def parse_official(page, body, g, page_meta, meta, schedule_meta):
    """Only the actual schedule -> match page -> published frontend stat request chain."""
    if (int(g["leagueId"]), int(g["meetSeq"])) not in ((1, 1), (2, 2)):
        raise ValueError("unsupported regular league identity")
    if any(m.get("status") != 200 for m in (page_meta, meta, schedule_meta)):
        raise ValueError("unsuccessful source response")
    if sha(page) != page_meta["source_sha256"] or sha(body) != meta["source_sha256"]:
        raise ValueError("source response hash mismatch")
    html = page.decode("utf-8")
    # Verify that the HTML's actual stat request binds to this scheduled game.
    start = html.find("function getMatchRecordAllData()")
    call = html[start:start + 1500] if start >= 0 else ""
    for k, v in official_query(g).items():
        if not re.search(r"['\"]" + re.escape(k) + r"['\"]\s*:\s*['\"]" + re.escape(v) + r"['\"]", call):
            raise ValueError("match page fixture mismatch:" + k)
    parser = GroundParser()
    parser.feed(html)
    b = json.loads(body)
    if str(b.get("resultCode")) != "200":
        raise ValueError("player stats application failure")
    d = date.fromisoformat(g["gameDate"].replace(".", "-")).isoformat()
    kickoff = stamp(d + "T" + g["gameTime"] + ":00+09:00").isoformat()
    if date.fromisoformat(d) > stamp(meta["observed_at"]).astimezone(KST).date():
        raise ValueError("source date after observation")
    out = []
    for side, key in (("home", "listH"), ("away", "listA")):
        team_id = g[side + "Team"]
        xi = parser.ids[(str(g["year"]), team_id)]
        if len(xi) != 11 or len(set(xi)) != 11:
            raise ValueError("not exactly eleven ground starters")
        stats = (b.get("data") or {}).get(key) or []
        if len({str(p.get("playerId")) for p in stats}) != len(stats):
            raise ValueError("duplicate player stats")
        parsed = []
        for p in stats:
            if (str(p.get("meetYear")), str(p.get("meetSeq")), str(p.get("gameId")), p.get("teamId")) != (
                str(g["year"]), str(g["meetSeq"]), str(g["gameId"]), team_id):
                raise ValueError("player stat fixture/team identity mismatch")
            pid = str(p.get("playerId", ""))
            if not re.fullmatch(r"\d{8}", pid):
                raise ValueError("invalid player identity")
            parsed.append({"player_id": pid, "position": p.get("playerPos"), "starter": pid in xi,
                           "goal": number(p.get("goal")), "assists": number(p.get("assist")),
                           "minutes": number(p.get("minutePlay"), 150), "shots": number(p.get("shot")),
                           "shots_on_target": number(p.get("shotOnTarget"))})
        if {p["player_id"] for p in parsed if p["starter"]} != set(xi):
            missing = sorted(set(xi) - {p["player_id"] for p in parsed})
            raise ValueError("XI missing player stats:" + ",".join(missing))
        out.append({"source_id": f"official:{g['year']}:{g['leagueId']}:{g['gameId']}:{side}",
                    "source_kind": "official_match_player_stats", "league": f"K리그{g['leagueId']}",
                    "date": d, "kickoff": kickoff, "home": g["homeTeamName"], "away": g["awayTeamName"],
                    "team": g[side + "TeamName"], "players": [p for p in parsed if p["starter"]],
                    "participants": parsed, "source_sha256": meta["source_sha256"],
                    "source_hashes": [schedule_meta["source_sha256"], page_meta["source_sha256"], meta["source_sha256"]],
                    "source_url": meta["url"], "match_url": page_meta["url"], "observed_at": meta["observed_at"],
                    "publication_at": None, "field_paths": {"player_id": "data." + key + "[].playerId",
                      "goal": "data." + key + "[].goal", "assists": "data." + key + "[].assist",
                      "position": "data." + key + "[].playerPos", "minutes": "data." + key + "[].minutePlay",
                      "shots": "data." + key + "[].shot", "shots_on_target": "data." + key + "[].shotOnTarget",
                      "starter": "match HTML div.ground image path teamId/playerId, exactly 11"}})
    return out


def collect(out=OUT):
    freeze(out)
    f = Fetcher(out)
    # All paths/POST fields were observed on these public pages, not guessed APIs.
    urls = [API + "/robots.txt", "https://www.kleague.com/robots.txt", "https://data.kleague.com/robots.txt",
            API + "/schedule/games/2023022501051/lineup", API + "/schedule/games/2023022501051/record",
            "https://www.kleague.com/index.do", "https://www.kleague.com/player.do",
            "https://www.kleague.com/record/player.do", "https://www.kleague.com/about/terms.do",
            "https://www.kleague.com/record/playerDetail.do?playerId=20130248", "https://www.kleague.com/schedule.do"]
    for url in urls:
        f.get(url)
    # Retain the initial wrong-format empty probe, then the actual frontend month form.
    f.get("https://www.kleague.com/getScheduleList.do",
          {"leagueId": 1, "teamId": None, "year": "2026", "month": "7", "ticketYn": ""})
    schedules = []
    metas = {}
    for league, month in [(1, 7), (2, 3), (1, 9)]:
        body, meta = f.get("https://www.kleague.com/getScheduleList.do",
                          {"leagueId": str(league), "teamId": None, "year": "2026", "month": f"{month:02d}", "ticketYn": ""})
        if meta["status"] != 200 or body is None:
            continue
        games = schedule_rows(body, year=2026, league=league, month=month)
        schedules.extend(games)
        for g in games:
            metas[(g["leagueId"], g["gameId"])] = meta
    write_gz(out / "schedule-identities.jsonl.gz", schedules)
    k2 = sorted([g for g in schedules if g["leagueId"] == 2], key=lambda g: (g["gameDate"], g["gameId"]))[:12]
    k1 = sorted([g for g in schedules if g["leagueId"] == 1 and "2026.09.01" <= g["gameDate"] < "2026.09.19"],
                key=lambda g: (g["gameDate"], g["gameId"]))[-4:]
    # The July fixture is an explicit old/new ID crosscheck and a real adapter sample.
    cross = [g for g in schedules if g["leagueId"] == 1 and g["gameId"] == 91]
    plan = cross + k2 + k1
    write_json(out / "collection-plan.json", {"selection": "fixed 1 July crosscheck + earliest12 March K2 + latest4 Sep1-18 K1; no labels", "games": plan})
    rows, rejected = [], []
    for g in plan:
        url = "https://www.kleague.com/match.do?" + urlencode({"year": g["year"], "leagueId": g["leagueId"],
                    "gameId": g["gameId"], "meetSeq": g["meetSeq"], "startTabNum": 2})
        try:
            page, pm = f.get(url)
            body, m = f.get("https://www.kleague.com/api/ddf/match/getMatchRecordAllDetail.do", official_query(g), form=True)
            rows.extend(parse_official(page, body, g, pm, m, metas[(g["leagueId"], g["gameId"])]))
        except (ValueError, TypeError, KeyError, RuntimeError) as exc:
            rejected.append({"identity": g, "reason": str(exc)})
        print(json.dumps({"league": g["leagueId"], "gameId": g["gameId"], "parsed_team_rows": len(rows),
                          "rejections": len(rejected), "requests": len(f.records)}), flush=True)
    write_gz(out / "official-raw-stats.jsonl.gz", rows)
    # Preserve the actual minimal player stat fields even when the XI join fails.
    # No team totals, match score or article content is copied into this projection.
    fields = ("meetYear", "meetSeq", "gameId", "teamId", "playerId", "playerPos", "goal", "assist",
              "minutePlay", "shot", "shotOnTarget")
    raw_stats = []
    for meta in f.records:
        if "getMatchRecordAllDetail.do" not in meta["url"] or meta["status"] != 200:
            continue
        payload = gzip.decompress((out / meta["cache_path"]).read_bytes())
        if sha(payload) != meta["source_sha256"]:
            raise ValueError("cached stats hash mismatch")
        b = json.loads(payload)
        for key in ("listH", "listA"):
            for player in (b.get("data") or {}).get(key) or []:
                raw_stats.append({"fields": {k: player.get(k) for k in fields}, "side_list": key,
                                  "source_sha256": meta["source_sha256"], "url": meta["url"],
                                  "public_query": meta["public_query"], "observed_at": meta["observed_at"]})
    write_gz(out / "official-stats-fields.jsonl.gz", raw_stats)
    # A literal source projection for portable adapter regressions, not an invented
    # provider contract. Full HTTP bytes and their hashes stay in the local cache.
    sample = cross[0]
    page_url = "https://www.kleague.com/match.do?" + urlencode({"year": sample["year"], "leagueId": sample["leagueId"],
                "gameId": sample["gameId"], "meetSeq": sample["meetSeq"], "startTabNum": 2})
    page, pm = f.get(page_url)
    body, sm = f.get("https://www.kleague.com/api/ddf/match/getMatchRecordAllDetail.do", official_query(sample), form=True)
    parser = GroundParser()
    parser.feed(page.decode("utf-8"))
    html = page.decode("utf-8")
    start = html.index("function getMatchRecordAllData()")
    stop = html.index("success: function", start)
    # Exact original request-binding substring plus exact original identity tokens.
    tokens = [html[start:stop]]
    for team in (sample["homeTeam"], sample["awayTeam"]):
        tokens.append('<div class="ground">')
        ids = parser.ids[(str(sample["year"]), team)]
        for pid in ids:
            image = re.search(r"<div style=\"background-image: url\('[^']*/v1/player/" + str(sample["year"]) + "/" + team + "/player_" + pid + r"\.png'\);\"></div>", html)
            if image:
                tokens.append(image[0])
            tokens.append('<ul class="player_' + pid + '"></ul>')
        tokens.append('</div>')
    b = json.loads(body)
    small_stats = {"resultCode": b["resultCode"], "data": {key: [{k: p.get(k) for k in fields} for p in b["data"][key]] for key in ("listH", "listA")}}
    write_json(out / "adapter-source-projection.json", {"projection_semantics": "minimal actual field/token projection; hashes of original HTTP are separate",
        "identity": sample, "page": "\n".join(tokens), "stats": small_stats, "page_meta": pm, "stats_meta": sm,
        "schedule_meta": metas[(sample["leagueId"], sample["gameId"])]})
    write_json(out / "source-discovery.json", {"http_requests": len(f.records), "official_team_rows": len(rows),
       "rejected": rejected, "negative_probes": ["data.kleague.com robots Disallow:/; no further requests",
       "Naver robots/known lineup/record transport failure; no retries/bypass",
       "official season/career player page is aggregate, never used for past decisions",
       "no authenticated target-XI publication time; no target-XI feature"],
       "route_evidence": {"schedule": "/schedule.do frontend onChangedNew/getScheduleList.do JSON POST",
                          "stats": "match.do frontend getMatchRecordAllData form POST"}})


def local_rows(raw, source_hash, observed):
    rows, rejected = [], []
    for key, g in raw.items():
        try:
            day = date.fromisoformat(g["date"])
            if (not re.fullmatch(r"\d{13,15}", str(key)) or str(g["gameId"]) != str(key)
                    or str(key)[:8] != day.strftime("%Y%m%d") or g["home"] == g["away"]
                    or day > stamp(observed).astimezone(KST).date()):
                raise ValueError("source date/fixture identity invalid")
            for side in ("home", "away"):
                players = ((g.get("data") or {}).get(side) or {}).get("players") or []
                ids = [str(p.get("playerId", "")) for p in players]
                if len(ids) != 11 or len(set(ids)) != 11 or not all(re.fullmatch(r"\d{8}", p) for p in ids):
                    rejected.append({"source_id": str(key), "side": side, "reason": "invalid_eleven_player_identity"})
                    continue
                rows.append({"source_id": "naver:" + str(key) + ":" + side,
                    "source_kind": "local_naver_lineup_player_stats", "league": "K리그1", "date": day.isoformat(),
                    "kickoff": None, "home": g["home"], "away": g["away"], "team": g[side],
                    "players": [{"player_id": str(p["playerId"]), "position": p.get("pos"), "starter": True,
                                 "goal": number(p.get("goal")), "assists": number(p.get("assists")),
                                 "minutes": None, "shots": None, "shots_on_target": None} for p in players],
                    "source_sha256": source_hash, "source_hashes": [source_hash],
                    "observed_at": observed, "publication_at": None,
                    "source_url": API + "/schedule/games/" + str(key) + "/lineup",
                    "source_url_basis": "src/game_detail.py collector route, not authenticated original HTTP log",
                    "field_paths": {"player_id": "data." + side + ".players[].playerId",
                        "position": "data." + side + ".players[].pos", "goal": "data." + side + ".players[].goal",
                        "assists": "data." + side + ".players[].assists", "minutes": "absent"}})
        except (KeyError, TypeError, ValueError):
            rejected.append({"source_id": str(key), "reason": "source_date_or_identity_invalid"})
    return rows, rejected


def norm(team):
    return TEAM_ALIASES.get(team, team)


def combine(local, official):
    """A verified ID crosscheck permits exact numeric K-league playerId reuse."""
    crosschecks = []
    index = defaultdict(list)
    for g in local + official:
        g = dict(g)
        for k in ("team", "home", "away"):
            g[k] = norm(g[k])
        index[(g["league"], g["date"], g["team"])].append(g)
    merged, conflicts = [], []
    for key, group in index.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        same_fixture = len({(g["home"], g["away"]) for g in group}) == 1
        xi = [{p["player_id"] for p in g["players"]} for g in group]
        same_ids = all(x == xi[0] for x in xi)
        maps = [{p["player_id"]: p for p in g["players"]} for g in group]
        same_stats = same_ids and all(len({m[pid][f] for m in maps if m[pid][f] is not None}) <= 1
                                      for pid in xi[0] for f in ("goal", "assists"))
        if same_fixture and same_ids and same_stats:
            preferred = sorted(group, key=lambda g: g["source_kind"] == "official_match_player_stats")[-1]
            merged.append(preferred)
            crosschecks.append({"key": key, "source_ids": [g["source_id"] for g in group],
                                "exact_xi_player_ids": sorted(xi[0]), "nonmissing_goal_assist_agree": True})
        else:
            conflicts.append({"key": key, "reason": "ambiguous_team_date_or_conflicting_player_stats",
                              "source_ids": [g["source_id"] for g in group]})
    if official and local and not crosschecks:
        raise ValueError("no actual old/new player identity crosscheck")
    return merged, crosschecks, conflicts


def bound(day):
    # Calendar end plus a FULL extra 24 hours; this is an assumption, not publication.
    return datetime.combine(date.fromisoformat(day) + timedelta(days=2), datetime.min.time(), KST)


def team_features(history, team, league, decision, target):
    decision = stamp(decision)
    cutoff = decision.astimezone(KST).date()
    h = sorted([g for g in history if g["team"] == norm(team) and g["league"] == league
                and g["date"][:4] == str(cutoff.year) and bound(g["date"]) <= decision
                and not (g["date"], g["home"], g["away"]) == target], key=lambda g: (g["date"], g["source_id"]))
    if not h:
        return {}, {}, [], ["no_eligible_same_season_player_history"]
    if len({g["date"] for g in h}) != len(h):
        return {}, {}, [], ["ambiguous_team_date_history"]
    if (cutoff - date.fromisoformat(h[-1]["date"])).days > 30:
        return {}, {}, [], ["stale_reference_xi_over30days"]
    reference = h[-1]
    valuation = h[:-1]  # Last XI match goals/assists never influence player valuation.
    if not valuation:
        return {}, {}, h, ["no_player_stats_before_reference_xi"]
    sums = {field: Counter() for field in ("goal", "assists")}
    counts = {field: Counter() for field in sums}
    for g in valuation:
        for p in g["players"]:
            for field in sums:
                value = number(p.get(field))
                if value is not None:
                    sums[field][p["player_id"]] += value
                    counts[field][p["player_id"]] += 1
    rates = {f: {pid: sums[f][pid] / n for pid, n in counts[f].items()} for f in sums}
    xi = {p["player_id"] for p in reference["players"]}
    previous = {p["player_id"] for p in h[-2]["players"]}
    entrants, leavers = xi - previous, previous - xi
    features, used, reasons = {}, {}, []
    # Every aggregate records all source games used to select XI / estimate rates.
    for field, short in (("goal", "goal"), ("assists", "assist")):
        known = xi & rates[field].keys()
        features[f"xi_{short}_known_share"] = len(known) / 11
        if known:
            features[f"xi_{short}_known_mean"] = sum(rates[field][p] for p in known) / len(known)
        if len(known) < 11:
            reasons.append(f"xi_{short}_history_missing_players:{11-len(known)}")
        if not entrants:
            reasons.append(f"rotation_{short}_net:no_prior_rotation")
        elif all(p in rates[field] for p in entrants | leavers):
            features[f"rotation_{short}_net"] = sum(rates[field][p] for p in entrants) - sum(rates[field][p] for p in leavers)
        else:
            reasons.append(f"rotation_{short}_net:missing_entrant_or_leaver_stats")
    # Official detailed positions are mapped explicitly; no current-season profile.
    attacking = {p["player_id"] for p in reference["players"]
                 if p.get("position") in {"FW", "MF", "CF", "LF", "RF", "LW", "RW", "AM", "CM", "DM", "LM", "RM"}}
    known_attack = attacking & rates["goal"].keys()
    if known_attack:
        features["attack_goal_known_mean"] = sum(rates["goal"][p] for p in known_attack) / len(known_attack)
    else:
        reasons.append("attack_goal_known_mean:no_known_attacking_player_stats")
    for name in features:
        field = "assists" if "assist" in name else "goal"
        used[name] = {"stat_field": field, "reference_xi_source_id": reference["source_id"],
                      "valuation_source_ids": [g["source_id"] for g in valuation],
                      "valuation_max_date": valuation[-1]["date"],
                      "known_xi_player_ids": sorted(xi & rates[field].keys()),
                      "entrants": sorted(entrants), "leavers": sorted(leavers)}
    return features, used, h, reasons


def extract(cases, history):
    rows, audits, seen = [], [], set()
    for r in cases:
        if r["league"] not in ("K리그1", "K리그2"):
            continue
        identity = (r["cohort"], r["event_id"], r["kickoff"])
        if identity in seen:
            raise ValueError("duplicate exact case identity")
        seen.add(identity)
        # Preserve exact PR424 decision_at, not tipster publication/available_at.
        decision = r["decision_at"]
        base = {k: r[k] for k in ("cohort", "event_id", "league", "kickoff")}
        base.update(decision_at=decision, features={}, feature_evidence={}, source_ids=[],
                    source_meta={"source_game_dates": {}, "fact_dates": []})
        if stamp(decision) >= stamp(r["kickoff"]):
            audits.append({**base, "reasons": ["decision_not_before_kickoff"]})
            continue
        target = (stamp(r["kickoff"]).astimezone(KST).date().isoformat(), norm(r["home"]), norm(r["away"]))
        reasons = []
        for side in ("home", "away"):
            f, used, h, missing = team_features(history, r[side], r["league"], decision, target)
            reasons.extend(side + ":" + reason for reason in missing)
            for name, value in f.items():
                key = "kleague_strength_prior_" + side + "_" + name
                base["features"][key] = value
                base["feature_evidence"][key] = {
                    "max_fact_time": None, "max_fact_date": h[-1]["date"],
                    "fact_date_timezone": "Asia/Seoul", "latest_source_game_date": h[-1]["date"],
                    "selected_fact_dates": sorted({g["date"] for g in h}),
                    "observed_at": max((g["observed_at"] for g in h), key=stamp),
                    "source_sha256": sorted({s for g in h for s in g["source_hashes"]}),
                    "source_urls": sorted({g["source_url"] for g in h}),
                    "raw_field_paths": sorted({g["field_paths"][used[name]["stat_field"]] for g in h}),
                    "availability_tier": "retrospective_lagged", "publication_at": None,
                    "date_bound_assumption": "source calendar-end +24h <= decision, not actual publication",
                    "note": "Partial observed-start archive; known-subset goal/assist rates. Reference is PRIOR XI, not target XI.",
                    **used[name]}
            if f:
                base["source_ids"].extend(g["source_id"] for g in h)
                base["source_meta"]["source_game_dates"].update({g["source_id"]: g["date"] for g in h})
        base["source_ids"] = sorted(set(base["source_ids"]))
        base["source_meta"]["fact_dates"] = sorted(set(base["source_meta"]["source_game_dates"].values()))
        rows.append(base)
        audits.append({**{k: base[k] for k in ("cohort", "event_id", "league", "kickoff", "decision_at")},
                       "feature_count": len(base["features"]), "reasons": reasons,
                       "current_xi": "not_used_no_authenticated_publication"})
    return rows, audits


def build(local_path, out=OUT):
    freeze(out)
    metadata = out / "local-ingestion.json"
    raw_bytes = local_path.read_bytes()
    ingestion = {"path": str(local_path), "source_sha256": sha(raw_bytes), "observed_at": now(),
                 "observation_semantics": "actual local ingestion now, historical publication unknown"}
    if metadata.exists():
        ingestion = json.loads(metadata.read_text(encoding="utf-8"))
        if ingestion["source_sha256"] != sha(raw_bytes):
            raise ValueError("local raw input changed after ingestion")
    else:
        write_json(metadata, ingestion)
    local, rejected = local_rows(json.loads(raw_bytes), ingestion["source_sha256"], ingestion["observed_at"])
    official = read_gz(out / "official-raw-stats.jsonl.gz")
    history, crosschecks, conflicts = combine(local, official)
    write_gz(out / "raw-stats.jsonl.gz", local)
    write_gz(out / "parsed-player-history.jsonl.gz", history)
    # Reuse ONLY the frozen input identities; no model/results files are opened.
    input_path = ROOT / "experiments/pregame-feature-rd-20261030/inputs/cases.jsonl.gz"
    keys = ("cohort", "event_id", "league", "kickoff", "home", "away", "decision_at")
    cases = [{k: r[k] for k in keys} for r in read_gz(input_path)]
    rows, audits = extract(cases, history)
    primary = [r for r in rows if r["cohort"] != "historical_market_proxy"]
    archive = [r for r in rows if r["cohort"] == "historical_market_proxy"]
    write_gz(out / "input-identities.jsonl.gz", [r for r in cases if r["league"] in ("K리그1", "K리그2")])
    write_gz(out / "features.jsonl.gz", primary)
    write_gz(out / "archive-features.jsonl.gz", archive)
    write_gz(out / "audit.jsonl.gz", audits)
    old_xi = ROOT / "experiments/pregame-rd-soccer-20261030/raw-xi.json.gz"
    with gzip.open(old_xi, "rt", encoding="utf-8") as f:
        old = json.load(f)
    old_index = {(str(g["source_id"]), g["team"]): set(g["xi"]) for g in old}
    agreement = sum(old_index.get((g["source_id"].split(":")[1], g["team"])) == {p["player_id"] for p in g["players"]} for g in local)
    summary = {"local_raw_games": len(json.loads(raw_bytes)), "local_team_rows": len(local),
        "local_player_start_rows": sum(len(g["players"]) for g in local),
        "local_missing_assists": sum(p["assists"] is None for g in local for p in g["players"]),
        "official_games_verified_xi_stats": len(official) // 2,
        "official_team_rows": len(official), "official_start_rows": sum(len(g["players"]) for g in official),
        "official_all_stat_rows_including_rejected_xi": len(read_gz(out / "official-stats-fields.jsonl.gz")),
        "combined_team_rows": len(history), "PR424_raw_xi_agree_team_rows": agreement,
        "identity_crosschecks": crosschecks, "conflicts": conflicts, "local_rejected": rejected,
        "rows": {}, "fields": dict(Counter(k for r in rows for k in r["features"])),
        "missing_reasons": dict(Counter(reason for a in audits for reason in a["reasons"])),
        "asof": "retrospective date-only; no captured_pregame claim", "outcomes_or_models_read": False}
    for label, group in (("primary", primary), ("archive", archive)):
        summary["rows"][label] = {"total": len(group), "any_feature": sum(bool(r["features"]) for r in group),
          "both_teams_strength": sum(all(any(k.startswith("kleague_strength_prior_" + side) and k.endswith("known_mean") for k in r["features"]) for side in ("home", "away")) for r in group),
          "actual_stat_strength": sum(any(k.endswith("known_mean") or "rotation_" in k for k in r["features"]) for r in group),
          "by_league": {league: {"total": sum(r["league"] == league for r in group),
                         "actual_stat_strength": sum(r["league"] == league and any(k.endswith("known_mean") or "rotation_" in k for k in r["features"]) for r in group)} for league in ("K리그1", "K리그2")}}
    write_json(out / "summary.json", summary)
    parent_protocol = ROOT.parent / "link-new-source-evidence-20261030/docs/research/2026-10-30-source-link-step2-protocol.json"
    write_json(out / "parent-protocol-reference.json", {"path": str(parent_protocol),
               "sha256": sha(parent_protocol.read_bytes()), "read_scope": "frozen protocol only; no parent results/models/ROI"})
    manifest = {"inputs": [{"path": str(local_path), "sha256": sha(raw_bytes)},
                 {"path": str(input_path.relative_to(ROOT)), "sha256": sha(input_path.read_bytes())},
                 {"path": str(old_xi.relative_to(ROOT)), "sha256": sha(old_xi.read_bytes())}],
                "outputs": [{"path": str(p.relative_to(out)), "sha256": sha(p.read_bytes())}
                            for p in sorted(out.iterdir()) if p.is_file() and p.name != "manifest.json"]}
    write_json(out / "manifest.json", manifest)
    print(json.dumps({k: summary[k] for k in ("local_raw_games", "official_games_verified_xi_stats", "rows")}, ensure_ascii=True))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["probe", "collect", "build"])
    p.add_argument("--local-raw", type=Path)
    args = p.parse_args()
    if args.command == "probe":
        probe()
    elif args.command == "collect":
        collect()
    elif args.command == "build":
        if args.local_raw is None:
            p.error("build requires --local-raw")
        build(args.local_raw)
