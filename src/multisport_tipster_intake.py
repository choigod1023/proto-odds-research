"""Strict public article intake; no production probability changes.

Publication dates alone never prove pregame availability. An article needs an
actual observation before T-30 and one exact fixture match. No fuzzy team match,
implicit home order, inferred kickoff or handicap-to-winner conversion.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import re
from urllib.parse import urlsplit

KST = timezone(timedelta(hours=9))
LEAGUES = {"NBA": "bk", "KBL": "bk", "WKBL": "bk", "KBO": "bs",
           "NPB": "bs", "KOVO컵": "vl", "V리그": "vl"}


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("timezone required")
    return result


def team_key(value):
    return re.sub(r"\s+", "", value)


def parse_article(*, url, title, author, published_at, observed_at, text):
    """Parse explicitly labelled winner ONLY; callers supply extracted metadata.

    Deliberately does not consume search-engine timestamps as observation time.
    Metadata extraction from live HTML must be validated before a scheduler is
    connected. Returned rows are evidence candidates, never eligible votes yet.
    """
    location = urlsplit(url)
    if (location.scheme != "https" or location.hostname != "www.goli.tv"
            or location.netloc != "www.goli.tv"
            or not re.fullmatch(r"/board/analysis/\d+", location.path)):
        raise ValueError("unsupported article URL")
    published, observed = timestamp(published_at), timestamp(observed_at)
    if published > observed or not author.strip():
        raise ValueError("invalid publication/author")
    title_match = re.fullmatch(
        r"(.+?) 대 (.+?) (\S+) (\d{1,2})월(\d{1,2})일 맞대결", title.strip())
    if not title_match or title_match[3] not in LEAGUES:
        raise ValueError("unsupported title/league")
    a, b, league = title_match[1], title_match[2], title_match[3]
    if team_key(a) == team_key(b):
        raise ValueError("duplicate teams")
    selections = re.findall(r"^\s*가능성이 높은 선택\s*>\s*(.+?)\s*승리\s*$", text, re.M)
    selections = {team_key(s) for s in selections}
    if len(selections) != 1 or not selections <= {team_key(a), team_key(b)}:
        raise ValueError("missing/ambiguous explicit full-game winner")
    # Keep month/day unresolved; never silently assign today's year to an old post.
    month, day = int(title_match[4]), int(title_match[5])
    datetime(2000, month, day)  # Reject impossible calendar dates, allow leap day.
    return dict(source="goli_public_article_v1", source_url="https://www.goli.tv" + location.path,
                source_id=location.path.rsplit("/", 1)[1], author=author.strip(),
                published_at=published.isoformat(), observed_at=observed.isoformat(),
                league=league, sport=LEAGUES[league], teams=[a, b], month=month, day=day,
                selected_team=next(iter(selections)), market="full_game_winner",
                content_hash=hashlib.sha256(text.encode()).hexdigest(), eligible=False)


def match_pregame(article, fixtures, *, as_of):
    """Return normalized vote or explicit exclusion; fixture times must be aware.

    Names must agree after whitespace removal only. Two fixtures with identical
    teams/day (doubleheaders) are ambiguous without a source kickoff, so withheld.
    """
    cutoff = timestamp(as_of)
    if (article.get("source") != "goli_public_article_v1"
            or article.get("market") != "full_game_winner"
            or article.get("selected_team") not in {team_key(t) for t in article.get("teams", [])}):
        return {"eligible": False, "reason": "invalid_article_identity"}
    observed, published = timestamp(article["observed_at"]), timestamp(article["published_at"])
    if published > observed or observed > cutoff:
        return {"eligible": False, "reason": "invalid_observation_time"}
    matches = []
    for fixture in fixtures:
        if fixture.get("league") != article["league"] or fixture.get("sport") != article["sport"]:
            continue
        try:
            kickoff = timestamp(fixture["kickoff"]).astimezone(KST)
            sides = {team_key(fixture[s]) for s in ("home", "away")}
        except (KeyError, TypeError, ValueError):
            continue
        if ((kickoff.month, kickoff.day) == (article["month"], article["day"])
                and sides == {team_key(t) for t in article["teams"]}
                and abs((kickoff - observed).total_seconds()) <= 3 * 86400):
            matches.append((fixture, kickoff))
    if len(matches) != 1:
        return {"eligible": False, "reason": "ambiguous_or_missing_fixture", "matches": len(matches)}
    fixture, kickoff = matches[0]
    if not fixture.get("id"):
        return {"eligible": False, "reason": "missing_fixture_id"}
    if max(observed, cutoff) > kickoff - timedelta(minutes=30):
        return {"eligible": False, "reason": "after_t30"}
    return {**article, "eligible": True, "fixture_id": fixture["id"],
            "kickoff": kickoff.isoformat(), "side": "home" if
            article["selected_team"] == team_key(fixture["home"]) else "away"}
