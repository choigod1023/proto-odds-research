"""Conservative, offline candidate extraction; never changes operating votes.

Input is the already isolated recommendation section for an already verified
fixture. Callers must enforce source identity, timing and fixture matching.
"""
import math
import re
from decimal import Decimal, InvalidOperation

_NUMBER = r"[+-]?\d+(?:\.\d+)?"
_TOTAL = re.compile(rf"(?:\[언더오버\]|언더오버)\s*[:：]?\s*({_NUMBER})\s*(언더|오버)\s*$")
_HANDICAP = re.compile(rf"(?:\[핸디(?:캡)?\s*(2way|3way)\]|핸디(?:캡)?\s*(2way|3way))\s*[:：]?\s*(홈|원정|무)\s*(홈\s*기준\s*)?({_NUMBER})\s*$", re.I)


def _line(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number * 2 != (number * 2).to_integral_value():
            return None
        return number
    except (InvalidOperation, ValueError, TypeError):
        return None


def extract_candidates(section):
    """Return explicit full-game selections; malformed/ambiguous families omitted.

    The input must be only the full-game recommendation section, not article
    analysis. Any first-half marker terminates it. Evidence is the source line.
    A handicap requires an explicit 2way/3way settlement and HOME line.
    """
    section = re.split(r"전반전|전반\s*(?:예측|추천)|글로벌 주요", section, maxsplit=1)[0]
    found = {"total": [], "handicap": []}
    for raw in section.splitlines():
        text = raw.strip()
        if text in {"언더","오버"}:
            found["total"].append(None)
            continue
        if re.fullmatch(r"핸디\s*(?:승|패|무|홈|원정)",text):
            found["handicap"].append(None)
            continue
        if re.search(r"전반|후반|쿼터|[1-4]\s*쿼터", text):
            continue
        family = "total" if "언더오버" in text else "handicap" if "핸디" in text else None
        if not family:
            continue
        m = (_TOTAL if family == "total" else _HANDICAP).fullmatch(text)
        if not m:
            found[family].append(None)
            continue
        if family == "total":
            line, side = _line(m[1]), {"언더": "under", "오버": "over"}[m[2]]
            settlement = "2way"
            if line is not None and line < 0:
                line = None
        else:
            settlement = (m[1] or m[2]).lower()
            side = {"홈": "home", "원정": "away", "무": "draw"}[m[3]]
            line = _line(m[5])
            if side != "home" and not m[4]:line=None
            if side == "draw" and settlement != "3way":
                line = None
            if settlement == "3way" and line is not None and line != line.to_integral_value():
                line = None
        found[family].append(None if line is None else dict(
            family=family, side=side, line=float(line), period="full_game",
            settlement=settlement, evidence=text))
    return [items[0] for items in found.values() if len(items) == 1 and items[0] is not None]


def match_candidate(candidate, game):
    """Match exactly one complete option market; return its unmodified option.

    None means withheld. This does not validate the game identity or timestamp.
    Handicap label is a signed HOME line; opposing line is never inferred from
    an article's team-relative handicap.
    """
    family, side, settlement = (candidate.get(k) for k in ("family", "side", "settlement"))
    line = _line(candidate.get("line"))
    if candidate.get("period") != "full_game" or line is None:
        return None
    if family == "total" and settlement == "2way" and side in {"under", "over"} and line >= 0:
        market, names = "언더오버", {"언더", "오버"}
        selection = {"under": "언더", "over": "오버"}[side]
    elif family == "handicap" and settlement in {"2way", "3way"} and side in {"home", "away", "draw"}:
        if side == "draw" and settlement != "3way":
            return None
        market = "핸디캡"
        names = {"핸디홈", "핸디원정"} | ({"핸디무"} if settlement == "3way" else set())
        selection = {"home": "핸디홈", "away": "핸디원정", "draw": "핸디무"}[side]
    else:
        return None
    groups = {}
    for option in game.get("options", []):
        if option.get("market") != market:
            continue
        label = str(option.get("label", "")).strip()
        pattern = rf"H\s*({_NUMBER})" if family == "handicap" else rf"(?:[UO]\s*)?({_NUMBER})"
        label_match = re.fullmatch(pattern, label, re.I)
        if not label_match or _line(label_match[1]) != line:
            continue
        if option.get("line") is not None and _line(option["line"]) != line:
            return None
        groups.setdefault(str(option.get("게임번호", "")), []).append(option)
    matches = []
    for options in groups.values():
        if len(options) != len(names) or {o.get("선택") for o in options} != names:
            return None
        periods = {o.get("period") or "" for o in options}
        scopes = {o.get("settlement_scope") or "" for o in options}
        if len(periods) != 1 or not periods <= {"", "full", "full_game"} or len(scopes) != 1:
            return None
        if any(o.get("n_way") is not None and o["n_way"] != len(names) for o in options):
            return None
        if family == "handicap" and settlement == "3way" and line != line.to_integral_value():
            return None
        for option in options:
            try:
                if isinstance(option["배당"], bool):return None
                price = float(option["배당"])
                if not math.isfinite(price) or price <= 1:
                    return None
            except (KeyError, TypeError, ValueError):
                return None
        matches.extend(o for o in options if o["선택"] == selection)
    return dict(matches[0]) if len(matches) == 1 else None


def recommendation_section(url, html):
    """Isolate supported full-game recommendation markup, not narrative text."""
    from bs4 import BeautifulSoup
    from urllib.parse import urlsplit
    from public_tipster_votes import safe_url
    if not safe_url(url):raise ValueError("untrusted_url")
    soup=BeautifulSoup(html,"lxml")
    if urlsplit(url).netloc == "pick79.com":
        blocks=[b for b in soup.select(".section-widget") if b.select_one("h2") and b.select_one("h2").get_text(strip=True)=="추천 픽"]
        if len(blocks)!=1:raise ValueError("missing_explicit_pick")
        return blocks[0].get_text("\n",strip=True)
    body=soup.select_one("#partner_content")
    parts=(body.get_text("\n",strip=True) if body else "").split("베팅 요약 및 추천")
    if len(parts)!=2:raise ValueError("missing_fullgame_section")
    return re.split(r"전반전|글로벌 주요",parts[1],maxsplit=1)[0]


def candidate_events(winner_event, url, html):
    """Preserve extra evidence without changing the legacy winner electorate.

    The existing source/fixture/T-30 checks must already have produced the
    winner event. Winner-less articles remain unsupported by this route.
    These events assert an article's line, never execution or price validity.
    """
    import hashlib,json
    from public_tipster_votes import SOURCE,team
    if not (winner_event.get("source")==SOURCE and winner_event.get("event_type")=="first_observed" and winner_event.get("eligible_pre_event") is True
            and winner_event.get("market_type")=="full_game_winner" and winner_event.get("provenance")=="live_http_observation"):
        raise ValueError("verified_winner_event_required")
    if (winner_event.get("content_hash")!=hashlib.sha256(html.encode()).hexdigest()
            or winner_event.get("source_url")!=url.split("?")[0].rstrip("/")):
        raise ValueError("candidate_source_bytes_mismatch")
    out=[]
    for candidate in extract_candidates(recommendation_section(url,html)):
        if candidate["family"]=="handicap" and team(winner_event["home"],winner_event["league"])!=team(winner_event["fixture"][2],winner_event["league"]):
            # Winner matching permits reversed source teams, but a signed home
            # handicap cannot inherit that orientation implicitly.
            continue
        identity=[winner_event["fixture"],winner_event["voter"],winner_event["source_url"],candidate["family"],candidate["line"],candidate["period"],candidate["settlement"]]
        event=dict(winner_event,**candidate,market_type="full_game_"+candidate["family"],candidate_only=True,validated_edge=False)
        event.pop("selected",None)
        event["pick_id"]=hashlib.sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        out.append(event)
    return out
