"""Bounded public-page intake. Only LIVE observation proves pregame availability.

No historical backfill, network access in inference, fuzzy team matching, or
invented probabilities. Editorial publishers count as ONE voter, not people.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import time
from urllib.parse import unquote, urljoin, urlsplit

import requests

SOURCE = "public_fullgame_vote_v1"
KST = timezone(timedelta(hours=9))
HOSTS = {"pick79.com", "www.goli.tv", "golfmasterj.tistory.com"}
MAX_ARTICLES = 15  # Six listings + fifteen bodies retain the old 21-request cap.
COLLECTION_SECONDS = 90
LEAGUES = {"프리미어리그": "EPL", "리그앙": "프리그1", "리그1": "프리그1",
           "J리그1": "J1리그", "J리그2": "J2리그", "AFC 챔피언스리그": "ACLE",
           "세리에 A": "세리에A", "EFL컵": "잉리그컵", "EFL 컵": "잉리그컵",
           "잉글랜드캐피털원컵": "잉리그컵", "메이저리그사커": "MLS"}
# Explicit league-scoped aliases only. Unknown names remain unmatched.
ALIASES = {
    "KBL": {"DB": "원주DB", "LG": "창원LG", "안양정관장": "안양정관", "KT": "KT소닉붐",
            "수원KT": "KT소닉붐", "한국가스공사": "한국가스", "대구한국가스공사": "한국가스",
            "현대모비스": "울산모비", "울산현대모비스": "울산모비", "부산KCC": "부산KCC"},
    "KBO": {"삼성라이온즈": "삼성", "삼성라이온스": "삼성", "KIA타이거즈": "KIA", "기아": "KIA",
            "LG트윈스": "LG", "SSG랜더스": "SSG", "두산베어스": "두산",
            "롯데자이언츠": "롯데", "한화이글스": "한화", "KT위즈": "KT",
            "NC다이노스": "NC", "키움히어로즈": "키움"},
    "NBA": dict(zip(
        "샬럿호네츠|워싱턴위저즈|클리블랜드캐벌리어스|미네소타팀버울브스|보스턴셀틱스|시카고불스|마이애미히트|댈러스매버릭스|밀워키벅스|인디애나페이서스|오클라호마시티썬더|피닉스선즈|휴스턴로키츠|애틀랜타호크스|유타재즈|멤피스그리즐리스|덴버너기츠|골든스테이트워리어스|LA레이커스|LA클리퍼스|브루클린네츠|뉴욕닉스|올랜도매직|토론토랩터스|필라델피아세븐티식서스|디트로이트피스턴스|새크라멘토킹스".split("|"),
        "샬럿호네 워싱위저 클리캐벌 미네울브 보스셀틱 시카불스 마이히트 댈러매버 밀워벅스 인디페이 오클썬더 피닉선즈 휴스로케 애틀호크 유타재즈 멤피그리 덴버너게 골든워리 LA레이커 LA클리퍼 브루네츠 뉴욕닉스 올랜매직 토론랩터 필라76s 디트피스 새크킹스".split())),
    "프리그1": {"파리생제르맹": "PSG", "올랭피크리옹": "리옹"},
    "ACLE": {"알아흘리": "알아흘사", "알사드": "알사드SC"},
    "MLB": {"탬파베이레이스": "탬파레이", "밀워키브루어스": "밀워브루"},
    "KOVO컵남": {"현대캐피탈": "현대캐피", "KB손해보험": "KB손보", "OK저축은행": "OK금융"},
    "KOVO컵여": {"흥국생명": "흥국생명", "IBK기업은행": "IBK기업", "한국도로공사": "도로공사"},
}
ALIASES['NBA'].update({'포틀랜드트레일블레이저스': '포틀트레',
                      '샌안토니오스퍼스': '샌안스퍼', '뉴올리언스펠리컨스': '뉴올펠리'})
ALIASES['NPB'] = {'소프트뱅크호크스': '소프트뱅', '소프트뱅크': '소프트뱅',
                  '요코하마DeNA베이스타스': '요코베이', '요코하마DeNA': '요코베이',
                  '한신타이거스': '한신', '요미우리자이언츠': '요미우리',
                  '주니치드래곤즈': '주니치', '히로시마도요카프': '히로카프',
                  '야쿠르트스왈로즈': '야쿠르트', '니혼햄파이터스': '닛폰햄',
                  '닛폰햄파이터스': '닛폰햄', '오릭스버팔로스': '오릭스',
                  '라쿠텐골든이글스': '라쿠텐', '세이부라이온즈': '세이부',
                  '지바롯데마린스': '지바롯데'}
ALIASES['K리그1'] = {'울산HD': '울산HDFC', '울산현대': '울산HDFC',
                     '포항스틸러스': '포항스틸', '대전하나시티즌': '대전하나',
                     '인천유나이티드': '인천유나', '제주SK': '제주SKFC', '부천FC1995': '부천FC'}
ALIASES['K리그2'] = {'부산아이파크': '부산아이', '서울이랜드': '서울이랜',
                     '서울이랜드FC': '서울이랜', '안산그리너스': '안산그리',
                     '전남드래곤즈': '전남드래', '파주프런티어': '파주프런'}
ALIASES['세리에A'] = {'파르마칼초1913': '파르마', '파르마칼초': '파르마',
                      '우디네세칼초': '우디네세', 'AC몬차': 'AC몬차',
                      'SSC나폴리': '나폴리', '제노아CFC': '제노아', '유벤투스FC': '유벤투스',
                      '베네치아FC': '베네치아', '인테르나치오날레': '인테르'}


def stamp(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.utcoffset() is None:
        raise ValueError("timezone_required")
    return dt


def team(name, league):
    key = re.sub(r"\s+", "", str(name)).upper()
    aliases = {k.upper(): v.upper() for k, v in ALIASES.get(league, {}).items()}
    return aliases.get(key, key)


def kickoff(game):
    m = re.fullmatch(r"(\d{2})\.(\d{2})\([^)]*\) (\d{2}):(\d{2})", game.get("date", ""))
    if not m:
        raise ValueError("unknown_kickoff")
    return datetime(int(game["year"]), *map(int, m.groups()), tzinfo=KST)


def fixture_key(game):
    return [game["sport"], game["league"], game["home"], game["away"], kickoff(game).isoformat()]


def safe_url(url):
    p = urlsplit(url)
    if p.netloc == 'golfmasterj.tistory.com':
        path = unquote(p.path)
        return (p.scheme == 'https' and not p.query and not p.fragment
                and len(url) <= 2400 and '\\' not in path
                and not any(ord(c) < 32 for c in path)
                and bool(re.fullmatch(r'/entry/[^/]+/?', path)))
    return p.scheme == "https" and p.netloc in HOSTS and bool(
        re.fullmatch(r"/posts/detail/[a-zA-Z0-9_-]+/?", p.path) if p.netloc == "pick79.com"
        else re.fullmatch(r"/board/analysis/\d+", p.path))


def parse_page(url, html, observed_at):
    from bs4 import BeautifulSoup
    if not safe_url(url):
        raise ValueError("untrusted_url")
    observed = stamp(observed_at)
    soup = BeautifulSoup(html, "lxml")
    if urlsplit(url).netloc == 'golfmasterj.tistory.com':
        from tipster_tistory import parse_golfmaster
        return parse_golfmaster(url, html, observed_at, soup)
    if urlsplit(url).netloc == "pick79.com":
        nodes = []
        for script in soup.select('script[type="application/ld+json"]'):
            obj = json.loads(script.get_text())
            nodes.extend(obj.get("@graph", [obj]) if isinstance(obj, dict) else [])
        events = [x for x in nodes if x.get("@type") == "SportsEvent"]
        articles = [x for x in nodes if x.get("@type") == "Article"]
        if len(events) != 1 or len(articles) != 1:
            raise ValueError("missing_event_metadata")
        event, article = events[0], articles[0]
        if event.get("eventStatus") != "https://schema.org/EventScheduled":
            raise ValueError("not_scheduled")
        home, away = event["homeTeam"]["name"], event["awayTeam"]["name"]
        league = LEAGUES.get(event["superEvent"]["name"], event["superEvent"]["name"])
        scheduled = stamp(event["startDate"])
        blocks = [x for x in soup.select(".section-widget")
                  if x.select_one("h2") and x.select_one("h2").get_text(strip=True) == "추천 픽"]
        if len(blocks) != 1:
            raise ValueError("missing_explicit_pick")
        labels = blocks[0].find_all(string=lambda s: s and s.strip() == "승무패")
        if len(labels) != 1:
            raise ValueError("ambiguous_pick")
        choice = labels[0].parent.parent.parent.get_text(" ", strip=True)
        author = article.get("author", {}).get("name")
        # This site's JSON-LD datePublished equals kickoff, including future
        # times. Preserve it as a CLAIM, never as evidence of early publication.
        # A freshly downloaded page observed before T-30 is independent proof.
        publication = {"published_at": None, "reported_published_at": article.get("datePublished"),
                       "publication_time_quality": "unverified_source_claim"}
        sport = {"Soccer": "sc", "Football": "sc", "Baseball": "bs",
                 "Basketball": "bk", "Volleyball": "vl"}.get(event.get("sport"))
    else:
        title = soup.select_one("#page-heading")
        m = re.fullmatch(r"(.+?) 대 (.+?) (\S+) (\d{1,2})월\s*(\d{1,2})일 맞대결", title.get_text(strip=True) if title else "")
        if not m:
            raise ValueError("unknown_title")
        home, away, league = m[1], m[2], LEAGUES.get(m[3], m[3])
        author_node = soup.select_one(".writer_box ._userView")
        author = author_node.get_text(" ", strip=True) if author_node else None
        stats = soup.select_one(".view_stat")
        date = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", stats.get_text() if stats else "")
        if not date:
            raise ValueError("missing_publication")
        published = datetime.fromisoformat(date[0]).replace(tzinfo=KST)
        if published > observed:
            raise ValueError("future_publication")
        publication = {"published_at": published.isoformat(), "publication_time_quality": "source_claim"}
        body = soup.select_one("#partner_content")
        text = body.get_text("\n", strip=True) if body else ""
        # Never mix first-half and full-game recommendations.
        section = text.split("베팅 요약 및 추천")
        if len(section) != 2:
            raise ValueError("missing_fullgame_section")
        section = re.split("전반전|글로벌 주요", section[1])[0]
        picks = re.findall(r"\[승/무/패\]\s*([^\n]+)", section)
        if len(picks) != 1:
            raise ValueError("ambiguous_pick")
        choice = picks[0]
        scheduled = None
        sport = "bs" if league in {"MLB", "KBO", "NPB"} else "bk" if league in {"NBA", "KBL", "WKBL"} else "vl" if "KOVO" in league or "V리그" in league else "sc"
        publication.update(month=int(m[4]), day=int(m[5]))
    if not author or sport not in {"sc", "bs", "bk", "vl"}:
        raise ValueError("missing_author_or_sport")
    choice = re.sub(r"^승무패\s*", "", choice).strip()
    if choice in {"무", "무승부"}:
        selected = "draw"
    else:
        pick = re.fullmatch(r"(.+?)\s*(승리|승|패배|패)", choice)
        if not pick:
            raise ValueError("not_explicit_winner")
        sides = {team(home, league): "home", team(away, league): "away"}
        selected = sides.get(team(pick[1], league))
        if not selected:
            raise ValueError("unknown_selection_team")
        if pick[2] in {"패", "패배"}:
            # In 3-way soccer 'home loses' means away wins, not draw.
            selected = "away" if selected == "home" else "home"
    row = dict(source=SOURCE, source_url=url.split("?")[0].rstrip("/"), author=author,
                sport=sport, league=league, home=home, away=away, selected=selected, selection_text=choice[:200],
                kickoff=scheduled.isoformat() if scheduled else None,
                observed_at=observed.isoformat(), content_hash=hashlib.sha256(html.encode()).hexdigest(),
                **publication)
    body = soup.select_one('#partner_content') or soup.select_one('article')
    if body:
        row.update(analysis_fingerprint(body.get_text(' ', strip=True)))
    return row


def analysis_fingerprint(text):
    """Exact normalized analysis only; never call similar opinions copies."""
    normalized = re.sub(r'\s+', '', text)
    return ({'analysis_hash': hashlib.sha256(normalized.encode()).hexdigest(),
             'analysis_characters': len(normalized)} if len(normalized) >= 200 else {})


def discover_links(url, html, observed_at, fixtures=()):
    """Old-first archive order is not recency. Dates here only prioritize fetches."""
    from bs4 import BeautifulSoup, SoupStrainer
    today = stamp(observed_at).astimezone(KST).date()
    keywords = []
    priorities = {'K리그1': 0, 'K리그2': 0, 'KBO': 1, 'NPB': 2, 'MLB': 3, 'KBL': 4, 'NBA': 5}
    for game in fixtures:
        try:
            if kickoff(game) >= stamp(observed_at)+timedelta(minutes=30):
                keywords.append((re.sub(r'\s+', '', game['home']),
                                 re.sub(r'\s+', '', game['away']), priorities.get(game['league'], 6)))
        except (KeyError, ValueError, TypeError):
            continue
    found = {}
    for anchor in BeautifulSoup(html, 'lxml', parse_only=SoupStrainer('a')).select('a[href]'):
        link = urljoin(url, anchor['href']).split('?')[0].split('#')[0]
        if not safe_url(link):
            continue
        title = anchor.get_text(' ', strip=True)
        hint = unquote(link) + ' ' + title
        dates = re.findall(r'(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)', hint)
        day = None
        if dates:
            try:
                day = datetime(*map(int, dates[-1])).date()
            except ValueError:
                continue
            if not today-timedelta(days=3) <= day <= today+timedelta(days=3):
                continue
        # Known upcoming fixtures first, then recent past, then unknown dates.
        rank = (0, (day-today).days) if day and day >= today else (
            (1, (today-day).days) if day else (2, 0))
        compact = re.sub(r'\s+', '', hint)
        matching = [priority for home, away, priority in keywords if home in compact and away in compact]
        rank = (min(matching) if matching else 7, *rank)
        found.setdefault(link.rstrip('/') + ('/' if 'pick79.com' in link else ''), rank)
    return sorted(found, key=lambda link: found[link])


def match_vote(article, fixtures):
    observed = stamp(article["observed_at"])
    matches = []
    for game in fixtures:
        try:
            start = kickoff(game)
            if game["sport"] != article["sport"] or game["league"] != article["league"]:
                continue
            names = [team(game[s], game["league"]) for s in ("home", "away")]
            source_names = [team(article[s], game["league"]) for s in ("home", "away")]
            if len(set(names)) != 2 or set(names) != set(source_names):
                continue
            if article.get("kickoff"):
                if abs((start-stamp(article["kickoff"])).total_seconds()) > 600:
                    continue
            elif (start.month, start.day) != (article["month"], article["day"]):
                continue
            # Include all same-day fixtures when checking ambiguity (doubleheaders).
            matches.append((game, start, names, source_names))
        except (KeyError, TypeError, ValueError):
            continue
    if len(matches) != 1:
        raise ValueError("unmatched_or_ambiguous_fixture")
    game, start, names, source_names = matches[0]
    if article.get("published_at") and not start-timedelta(days=3) <= stamp(article["published_at"]) <= observed:
        raise ValueError("stale_publication")
    if not start-timedelta(days=3) <= observed <= start-timedelta(minutes=30):
        raise ValueError("outside_pregame_window")
    chosen = article["selected"]
    side = "무" if chosen == "draw" else "홈" if source_names[0 if chosen == "home" else 1] == names[0] else "원정"
    if side == "무" and game["sport"] != "sc":
        raise ValueError("unsupported_draw")
    key = fixture_key(game)
    voter = urlsplit(article["source_url"]).netloc + ":" + article["author"]
    identity = hashlib.sha256(json.dumps([key, voter, article["source_url"]], ensure_ascii=False).encode()).hexdigest()
    return dict(article, fixture=key, side=side, voter=voter, pick_id=identity,
                event_type="first_observed", eligible_pre_event=True,
                market_type="full_game_winner", provenance="live_http_observation")


def read_fixtures(db):
    # Project only identity fields. Do not decode the multi-MB picks artifact.
    keys = ("sport", "league", "home", "away", "year", "date")
    projection = ",".join(f"'{k}',json_extract(g.value,'$.{k}')" for k in keys)
    with db.connect() as conn:
        rows = conn.execute(f"SELECT json_object({projection}) FROM artifacts a, json_each(a.payload_json,'$.live') g WHERE a.name='picks_v2' AND g.type='object'")
        return [json.loads(r[0]) for r in rows]


def fetch(session, url):
    # Bounded body, time and host; redirects never escape the public allowlist.
    with session.get(url, timeout=(5, 12), stream=True, allow_redirects=False) as r:
        r.raise_for_status()
        if r.status_code != 200 or "text/html" not in r.headers.get("Content-Type", ""):
            raise ValueError("not_html_200")
        data = bytearray()
        start = time.monotonic()
        for part in r.iter_content(32768):
            data.extend(part)
            if len(data) > 6_000_000 or time.monotonic()-start > 20:
                raise ValueError("response_budget_exceeded")
        return data.decode("utf-8")


def collect(db, session):
    started = time.monotonic()
    def progress(stage):
        print(f"public_tipster_intake stage={stage} elapsed={time.monotonic()-started:.1f}s", flush=True)
    progress("fixtures_start")
    fixtures = read_fixtures(db)
    progress(f"fixtures_ready:{len(fixtures)}")
    now = datetime.now(timezone.utc)
    summary = {"observed_at": now.isoformat(), "source": SOURCE, "written": 0, "parsed": 0,
               "sports": {}, "errors": {}, "rejected": {}}
    counts, rejects, sports, leagues = Counter(), Counter(), Counter(), Counter()
    by_source = {}
    authors = set()
    links = []
    # Interleave sports so a busy NBA day cannot starve volleyball/baseball.
    groups = []
    latest_links = []
    listings = [(c, f'https://pick79.com/posts/{c}/')
                for c in ('soccer', 'baseball', 'basketball', 'volleyball')]
    listings += [('goli', 'https://www.goli.tv/board/analysis'),
                 ('golfmaster', 'https://golfmasterj.tistory.com/')]
    for category, url in listings:
        if time.monotonic()-started >= COLLECTION_SECONDS:
            counts['collection_deadline'] += 1
            break
        progress(f"listing:{category}")
        try:
            found = discover_links(url, fetch(session, url), now.isoformat(), fixtures)
            groups.append(found[:12])
            latest_links.extend(found[:1])
            if not found:
                counts[category+":empty_listing"] += 1
        except (requests.RequestException, ValueError, UnicodeError) as exc:
            counts[category+":"+type(exc).__name__] += 1
    for i in range(12):
        links.extend(g[i] for g in groups if len(g) > i)
    # A rotating cursor prevents permanent starvation with a bounded workload.
    state = db.get_document("public_tipster_intake") or {}
    links = list(dict.fromkeys(u for u in links if safe_url(u)))
    # Check each listing's highest-priority article on every poll, within the same
    # 15-body request budget. Rotate the remainder so older evidence also gets
    # checked; a cursor near the end must not delay today's first article.
    latest_links = list(dict.fromkeys(u for u in latest_links if safe_url(u)))
    latest_set = set(latest_links)
    rotating = [u for u in links if u not in latest_set]
    cursor = int(state.get("cursor", 0)) % max(1, len(rotating))
    rotated = (rotating[cursor:]+rotating[:cursor])[:MAX_ARTICLES-len(latest_links)]
    candidates = latest_links + rotated
    attempted = 0
    rotated_attempted = 0
    for index, url in enumerate(candidates):
        if time.monotonic()-started >= COLLECTION_SECONDS:
            counts['collection_deadline'] += 1
            break
        attempted += 1
        rotated_attempted += int(url not in latest_set)
        source = by_source.setdefault(urlsplit(url).netloc, Counter())
        source['attempted'] += 1
        progress(f"article:{index+1}/{len(candidates)}")
        try:
            html = fetch(session, url)
            # Timestamp AFTER receiving bytes, never before a slow HTTP request.
            row = parse_page(url, html, datetime.now(timezone.utc).isoformat())
            summary["parsed"] += 1
            source['parsed'] += 1
            event = match_vote(row, fixtures)
            written = db.append_events("public_tipster_votes", [event], identity_keys=("source", "pick_id"))
            summary["written"] += written
            source['matched'] += 1
            source['written'] += written
            sports[event["sport"]] += 1
            leagues[event['league']] += 1
            authors.add(event['voter'])
            progress(f"matched:{event['sport']}")
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as exc:
            rejects[str(exc) if isinstance(exc, ValueError) else type(exc).__name__] += 1
            source['rejected'] += 1
        time.sleep(.25)
    summary.update(cursor=(cursor+rotated_attempted) % max(1, len(rotating)),
                   candidates=attempted, discovered=len(links), errors=dict(counts),
                   rejected=dict(rejects), sports=dict(sports), leagues=dict(leagues),
                   authors=len(authors), by_source={k: dict(v) for k, v in by_source.items()},
                   deferred=len(candidates)-attempted,
                   elapsed_seconds=round(time.monotonic()-started, 3))
    summary["status"] = "matched" if sports else "no_eligible_votes"
    db.put_document("public_tipster_intake", summary, generated_at=summary["observed_at"])
    progress("complete")
    return summary


def main():
    from runtime_db import RuntimeDatabase, database_enabled
    if not database_enabled():
        raise RuntimeError("Runtime database required; no fake local production intake")
    with requests.Session() as session:
        session.headers["User-Agent"] = "proto-odds-research/1.0 public-page-collector"
        summary = collect(RuntimeDatabase(), session)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 1 if summary["errors"] and not summary["parsed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
