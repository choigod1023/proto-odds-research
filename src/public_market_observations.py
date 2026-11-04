"""Read-only, explicitly unpriced observations from a narrow public post format."""
import hashlib
import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from public_tipster_votes import KST, fixture_key, kickoff, stamp, team

SOURCE = "public_market_reference_v1"
LEAGUES = {"한국남자농구": ("bk", "KBL"), "한국여자농구": ("bk", "WKBL"),
           "한국남자배구": ("vl", "KOVO남"), "한국여자배구": ("vl", "KOVO여")}
REFERENCE_ALIASES = {"KOVO남": {"KB손해보험": "KB손보", "OK저축은행": "OK저축"},
                     "KOVO여": {"정관장": "대전정관"}}


def reference_team(name, league):
    normalized = re.sub(r"\s+", "", str(name)).upper()
    aliases = {key.upper(): value.upper() for key, value in REFERENCE_ALIASES.get(league, {}).items()}
    return team(aliases.get(normalized, name), league)


DATE = r"(\d{1,2})월\s*(\d{1,2})일"
BLOCK = re.compile(
    DATE + r"\s+(\d{1,2}):(\d{2})분!+\s*"
    r"(한국남자농구|한국여자농구|한국남자배구|한국여자배구)!+\s*"
    r"\d+\.([^\s:]+):([^\s:]+)\s+([^\n]+)")


def safe_reference_url(url):
    try:
        p = urlsplit(url)
        return (p.scheme == "https" and p.netloc == "www.goli.tv" and
                re.fullmatch(r"/board/free/\d+", p.path) is not None and
                not p.query and not p.fragment)
    except (ValueError, TypeError):
        return False


def parse_reference_events(url, html, observed_at, fixtures):
    """Extract only source-stated references; fixture and receipt are caller supplied.

    Caller must supply the actual completed HTTP observation, not article time.
    Unsupported names, ambiguous fixture matches and uncertain prose are withheld.
    """
    if not safe_reference_url(url) or not isinstance(html, str):
        return []
    try:
        observed = stamp(observed_at)
    except (ValueError, TypeError, OverflowError):
        return []
    soup = BeautifulSoup(html, "lxml")
    headings, bodies, stats = soup.select("#page-heading"), soup.select("#partner_content"), soup.select(".view_stat")
    if len(headings) != 1 or len(bodies) != 1 or len(stats) != 1:
        return []
    info = stats[0].find_parent(class_="writer_info")
    authors = info.select(".writer_box a._userView") if info else []
    if len(authors) != 1:
        return []
    author = authors[0].get_text(" ", strip=True)
    pubtext = stats[0].select_one("li")
    if not author or not pubtext:
        return []
    try:
        published = datetime.strptime(pubtext.get_text(strip=True), "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    except ValueError:
        return []
    if published > observed:
        return []
    body = bodies[0].get_text("\n", strip=True).replace("\u200b", "")
    title = headings[0].get_text(" ", strip=True)
    dates = re.findall(DATE, title + "\n" + body)
    target = observed.astimezone(KST).date()
    if not dates or any((int(m), int(d)) != (target.month, target.day) for m, d in dates):
        return []
    if not re.search(r"\d+\s*폴더", body) or re.search(r"취소|제외|아닌|아니|비추천|반대|변경|수정|전반|후반|쿼터|세트", body):
        return []
    voter = "www.goli.tv:" + author
    result = []
    for m in BLOCK.finditer(body):
        month, day, hour, minute = map(int, m.group(1, 2, 3, 4))
        sport, league = LEAGUES[m[5]]
        try:
            claimed = datetime(target.year, month, day, hour, minute, tzinfo=KST)
        except ValueError:
            continue
        if claimed.hour != 19 or claimed.minute != 0:
            continue
        source_teams = {reference_team(m[6], league), reference_team(m[7], league)}
        if len(source_teams) != 2:
            continue
        matches = []
        for game in fixtures:
            try:
                start = kickoff(game)
                if (game["sport"] == sport and game["league"] == league and start.date() == target
                        and abs((start - claimed).total_seconds()) <= 600
                        and observed + timedelta(minutes=30) < start
                        and {reference_team(game["home"], league), reference_team(game["away"], league)} == source_teams):
                    matches.append(game)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        if len(matches) != 1:
            continue
        pick = m[8].strip()
        handicap = re.fullmatch(r"([^\s]+)\s+(-\d+(?:\.\d+)?)점\s+마핸승!+\s*(\d+(?:\.\d+)?)배!+", pick)
        total = re.fullmatch(r"(\d+(?:\.\d+)?)점\s+(오버|언더)!+\s*(\d+(?:\.\d+)?)배!+", pick)
        if handicap and reference_team(handicap[1], league) in source_teams:
            selected = reference_team(handicap[1], league)
            home = reference_team(matches[0]["home"], league)
            away = reference_team(matches[0]["away"], league)
            if home == away or selected not in {home, away}:
                continue
            fields = dict(family="handicap", selected_team=handicap[1], team_line=float(handicap[2]),
                          side="home" if selected == home else "away", source_selection="마핸승")
            price = handicap[3]
        elif total:
            fields = dict(family="total", side={"오버": "over", "언더": "under"}[total[2]], line=float(total[1]))
            price = total[3]
        else:
            continue
        key = fixture_key(matches[0])
        identity = [key, voter, url, fields["family"]]
        event = dict(source=SOURCE, market_type="source_line_reference", event_type="first_observed",
                     eligible_pre_event=True, reference_only=True, is_parlay_leg=True, fixture=key, voter=voter,
                     observed_at=observed_at, content_hash=hashlib.sha256(html.encode("utf-8")).hexdigest(), url=url, source_url=url,
                     period="unknown", settlement="unknown", unit="points",
                     affects_model=False, affects_probability=False, affects_recommendation=False,
                     price_eligible=False, source_odds_text=price + "배", evidence=pick, **fields)
        event["pick_id"] = hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        result.append(event)
    # Multiple same-family entries for one fixture are ambiguous, not separate votes.
    return [event for event in result if sum(other["pick_id"] == event["pick_id"] for other in result) == 1]
