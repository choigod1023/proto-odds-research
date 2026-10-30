"""Bounded, credential-free research capture; never reads outcomes or trains models.

Raw responses stay outside the checkout. Only minimal provenance is exported.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/source-link-tipster-20261030"
HOSTS = {"www.goli.tv", "www.attv24.com", "xn--950bo4em5v.co",
         "named.com", "www.livescore.co.kr", "livescore.co.kr",
         "pick79.com", "golfmasterj.tistory.com"}
RULES = {
    "version": "tipster-independent-v1", "frozen_before_ROI": True,
    "fixture": "league + explicit teams + full source date; require unique physical event; source kickoff if present must agree exactly; never use scores",
    "no_year": "never infer year from observed_at; allow title month/day only with explicit source publication year and <=3 day publication-to-fixture distance",
    "selection": "explicit labelled full-game winner/1X2 only; no prose sentiment, handicap, totals, first-half or inferred scoreline",
    "identity": "site-scoped author, never merge names across sites; missing author excluded",
    "duplicates": "per fixture one identical raw document SHA or normalized article-body SHA; then one vote per site-scoped author; conflicting directions excluded; equal author names across sites do not prove either identity or independence",
    "independence": "cross-site copies excluded by body hash; remaining provenance-distinct authors are candidates, not verified independent human opinions",
    "availability": "captured_pregame only if actual observed_at <= kickoff-30m; retrospective_claimed_pregame only if explicit published claim <= kickoff-30m and observed after that; other rows diagnostic only",
    "request_limit": 90, "timeouts_seconds": [5, 12],
    "response_limit_bytes": 2000000, "no_redirect_follow": True,
    "raw_cache": "external local directory only; no full third-party pages in git",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class PublicCache:
    """Each wire request, including failures, consumes persisted total budget."""
    def __init__(self, cache_dir, limit=90):
        self.path = Path(cache_dir).resolve()
        if self.path == ROOT or ROOT in self.path.parents:
            raise ValueError("raw_cache_must_be_outside_checkout")
        self.path.mkdir(parents=True, exist_ok=True)
        self.limit = min(limit, 90)
        self.log = self.path / "requests.jsonl"

    def fetch(self, url):
        p = urlsplit(url)
        if (p.scheme != "https" or p.hostname not in HOSTS or p.username or p.password
                or p.port not in (None, 443)):
            raise ValueError("unsupported_public_url")
        proxy = os.environ.get("WBC_PROXY_URL", "")
        if proxy:
            transport = urlsplit(proxy)
            if transport.username or transport.password or transport.query:
                raise ValueError("credential_free_proxy_required")
        key = sha((url + ("|configured_proxy" if proxy else "")).encode())
        meta = self.path / (key + ".json")
        body = self.path / (key + ".body")
        if meta.exists():
            item = json.loads(meta.read_text(encoding="utf-8"))
            return item, body.read_bytes() if body.exists() else b""
        count = len(self.log.read_text(encoding="utf-8").splitlines()) if self.log.exists() else 0
        if count >= self.limit:
            raise ValueError("request_budget_exhausted")
        item = {"url": url, "request_number": count + 1, "fetch_started_at": now(),
                "transport": "configured_credential_free_proxy" if proxy else "direct"}
        # Reserve budget before I/O, including interrupted attempts.
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item) + "\n")
        raw = bytearray()
        deadline = time.monotonic() + 22
        try:
            with requests.Session() as session:
                session.trust_env = False  # no inherited auth, cookies, proxy credentials
                if proxy:
                    session.proxies = {"http": proxy, "https": proxy}
                with session.get(url, timeout=(5, 12), allow_redirects=False, stream=True,
                                 headers={"User-Agent": "PublicTipsterResearch/1.0"}) as res:
                    item.update(status=res.status_code, content_type=res.headers.get("Content-Type", ""))
                    if res.is_redirect:
                        item["redirect_url"] = urljoin(url, res.headers.get("Location", ""))
                    for chunk in res.iter_content(16384):
                        if time.monotonic() > deadline:
                            raise ValueError("response_wall_time_limit")
                        if len(raw) + len(chunk) > 2_000_000:
                            raise ValueError("response_size_limit")
                        raw.extend(chunk)
        except (requests.RequestException, ValueError) as exc:
            # Do not serialize arbitrary exception messages / environment details.
            item["error"] = type(exc).__name__
        item.update(observed_at=now(), bytes=len(raw), document_sha256=sha(raw))
        body.write_bytes(raw)
        dump(meta, item)
        return item, bytes(raw)


def freeze():
    protocol = Path("C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/link-new-source-evidence-20261030/docs/research/2026-10-30-source-link-step2-protocol.json")
    dest = OUT / "dedup-rules.json"
    if not dest.exists():
        dump(dest, dict(RULES, frozen_at=now(), parent_protocol_sha256=sha(protocol.read_bytes())))


def stamp(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.utcoffset() is None:
        raise ValueError("timezone_required")
    return dt


def compact(value):
    return re.sub(r"\s+", "", str(value)).casefold()


# Explicit aliases only; no fuzzy matching, transliteration or inferred teams.
GROUPS = {
    "KBO": ["LG|LG트윈스", "KT|KT위즈", "KIA|기아|KIA타이거즈", "SSG|SSG랜더스",
            "NC|NC다이노스", "롯데|롯데자이언츠", "한화|한화이글스", "키움|키움히어로즈",
            "두산|두산베어스", "삼성|삼성라이온즈|삼성라이온스"],
    "NPB": ["닛폰햄|니혼햄|니혼햄파이터스|닛폰햄파이터스|홋카이도닛폰햄파이터스|HokkaidoNippon-HamFighters",
            "지바롯데|치바롯데|지바롯데마린스|치바롯데마린스|ChibaLotteMarines", "세이부|사이타마세이부라이온즈|SaitamaSeibuLions",
            "라쿠텐|라쿠텐골든이글스|TohokuRakutenGoldenEagles",
            "소프트뱅|소프트뱅크|소프트뱅크호크스|FukuokaSoftBankHawks",
            "오릭스|오릭스버팔로스|OrixBuffaloes", "요코베이|요코하마|요코하마DeNA|요코하마DeNA베이스타스",
            "요미우리|요미우리자이언츠|YomiuriGiants", "한신|한신타이거스|HanshinTigers",
            "주니치|주니치드래곤즈|ChunichiDragons", "히로카프|히로시마|히로시마도요카프",
            "야쿠르트|야쿠르트스왈로즈|TokyoYakultSwallows"],
    "K리그1": ["전북|전북현대", "울산|울산HD|울산HDFC|울산현대", "서울|FC서울",
               "인천|인천유나|인천유나이티드", "대전|대전하나|대전하나시티즌", "광주|광주FC",
               "포항|포항스틸|포항스틸러스", "제주|제주SK|제주SKFC|제주유나이티드",
               "안양|FC안양", "강원|강원FC", "김천|김천상무"],
    "K리그2": ["서울E|서울이랜드", "충북청주|청주", "충남아산|아산", "부산|부산아이파크",
               "경남|경남FC", "안산|안산그리너스", "성남|성남FC", "화성|화성FC"],
}


def team(value, league):
    name = compact(value)
    for group in GROUPS.get(league, []):
        aliases = [compact(s) for s in group.split("|")]
        if name in aliases:
            return aliases[0]
    return name


def read_cases(path):
    """Project identities and frozen old vector only; never expose labels/prices."""
    keys = ("cohort", "event_id", "league", "home", "away", "kickoff", "decision_at", "sides", "external_p")
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [{k: row.get(k) for k in keys} for line in handle
                if not (row := json.loads(line)).get("auxiliary")]


def parse_article(raw, url, observed_at, hash_kind="http_response_bytes"):
    if urlsplit(url).hostname != "www.goli.tv" or not re.fullmatch(r"/board/analysis/\d+", urlsplit(url).path):
        raise ValueError("unsupported_article_adapter")
    stamp(observed_at)
    soup = BeautifulSoup(raw, "html.parser")
    heading = soup.select_one("#page-heading")
    title = heading.get_text(" ", strip=True) if heading else ""
    match = re.fullmatch(r"(.+?) 대 (.+?) (KBO|NPB|K리그1|K리그2|K리그 1|K리그 2) (\d{1,2})월\s*(\d{1,2})일 맞대결(?: 생중계, 스포츠중계, 스포츠분석)?", title)
    if not match:
        raise ValueError("unsupported_or_missing_fixture_title")
    home, away, league, month, day = match.groups()
    league = league.replace(" ", "")
    author_node = soup.select_one(".writer_box ._userView")
    if not author_node or not author_node.get_text(strip=True):
        raise ValueError("missing_author")
    author = author_node.get_text(" ", strip=True)
    stats = soup.select_one(".view_stat")
    pub = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", stats.get_text(" ") if stats else "")
    if not pub:
        raise ValueError("missing_publication_year_and_time")
    published = datetime.fromisoformat(pub[0]).replace(tzinfo=KST)
    source_date = datetime(published.year, int(month), int(day), tzinfo=KST).date()
    if abs((source_date - published.date()).days) > 3:
        raise ValueError("publication_fixture_date_distance")
    body = soup.select_one("#partner_content")
    if not body:
        raise ValueError("missing_article_body")
    text = body.get_text("\n", strip=True)
    if league in {"K리그1", "K리그2"}:
        sections = text.split("베팅 요약 및 추천")
        if len(sections) != 2:
            raise ValueError("missing_unique_fullgame_summary")
        # Later sections often contain a DIFFERENT first-half pick.
        text = re.split(r"전반전|글로벌 주요", sections[1])[0].strip()
    choices = []
    lines = [s.strip() for s in text.splitlines() if s.strip()]
    for index, line in enumerate(lines):
        line = line.strip().replace("\xa0", " ")
        # 승1패, handicap, totals and opinion prose are deliberately unsupported.
        m = re.fullmatch(r"(?:예상 승리팀|승/무/패|\[승/무/패\]|승무패)\s*[:：]?\s*(.+?)\s*", line)
        if not m and index == 0 and league in {"K리그1", "K리그2"}:
            m = re.fullmatch(r"(.+?\s+(?:승리|승|패배|패))", line)
        if not m:
            continue
        val = m[1].strip()
        if val in {"무", "무승부"}:
            choices.append("draw")
            continue
        loses = bool(re.search(r"(?:패배|패)$", val))
        winner = re.sub(r"\s*(?:승리|승|패배|패)$", "", val).strip()
        if team(winner, league) == team(home, league):
            choices.append("away" if loses else "home")
        elif team(winner, league) == team(away, league):
            choices.append("home" if loses else "away")
        else:
            raise ValueError("unknown_explicit_selection_team")
    if not choices:
        raise ValueError("missing_explicit_full_game_winner")
    if len(set(choices)) != 1 or (choices[0] == "draw" and league in {"KBO", "NPB"}):
        raise ValueError("ambiguous_or_unsupported_selection")
    return dict(league=league, home=home, away=away, source_date=source_date.isoformat(),
                source_kickoff=None, selection=choices[0], author=author,
                source_site=urlsplit(url).hostname, source_url=url,
                published_at_claim=published.isoformat(), observed_at=observed_at,
                document_sha256=sha(raw), document_hash_kind=hash_kind,
                http_response_sha256=sha(raw) if hash_kind == "http_response_bytes" else None,
                article_body_sha256=sha(re.sub(r"\s+", " ", body.get_text(" ", strip=True)).strip().encode()),
                market="full_game_moneyline" if league in {"KBO", "NPB"} else "승무패",
                independence_status="provenance_distinct_candidate_not_verified_independent_human",
                publication_timezone_basis="Korean site local time interpreted as Asia/Seoul")


def availability(published, observed, kickoff):
    cutoff = stamp(kickoff) - timedelta(minutes=30)
    actual = stamp(observed)
    claim = stamp(published) if published else None
    if claim and claim > actual:
        return "diagnostic_future_publication_claim"
    if actual <= cutoff:
        return "captured_pregame"
    if claim and claim <= cutoff:
        return "retrospective_claimed_pregame"
    return "diagnostic_no_pregame_publication_claim"


def join_article(article, cases):
    matches = {}
    for case in cases:
        if case["league"] != article["league"]:
            continue
        league = case["league"]
        source_names = [team(article[k], league) for k in ("home", "away")]
        case_names = [team(case[k], league) for k in ("home", "away")]
        if len(set(source_names)) != 2 or set(source_names) != set(case_names):
            continue
        start = stamp(case["kickoff"])
        if start.astimezone(KST).date().isoformat() != article["source_date"]:
            continue
        if article.get("source_kickoff") and stamp(article["source_kickoff"]) != start:
            continue
        matches[case["event_id"]] = (case, case_names, source_names)
    if len(matches) != 1:
        raise ValueError("ambiguous_fixture" if matches else "no_exact_primary_fixture")
    case, case_names, source_names = next(iter(matches.values()))
    chosen = article["selection"]
    if chosen != "draw":
        winner = source_names[0 if chosen == "home" else 1]
        chosen = "home" if winner == case_names[0] else "away"
    return dict(article, event_id=case["event_id"], cohort=case["cohort"], kickoff=case["kickoff"],
                selection=chosen, availability_tier=availability(article["published_at_claim"], article["observed_at"], case["kickoff"]),
                join_evidence={"method": "unique_league_teams_source_year_month_day", "candidate_count": 1,
                               "source_home": article["home"], "source_away": article["away"],
                               "source_date": article["source_date"], "source_kickoff": article.get("source_kickoff"),
                               "kickoff_basis": "frozen_case_only_not_inferred_from_article", "scores_used": False})


def deduplicate(rows):
    """Preserve raw rows separately; return accepted rows and per-row decisions."""
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault((row["event_id"], row["source_site"], row["author"]), []).append(i)
    conflicts = {i for ids in groups.values() if len({rows[i]["selection"] for i in ids}) > 1 for i in ids}
    seen_hash, seen_body, seen_author, accepted, audit = set(), set(), set(), [], []
    for i, row in enumerate(rows):
        rawkey = (row["event_id"], row.get("document_sha256"))
        bodykey = (row["event_id"], row.get("article_body_sha256"))
        authkey = (row["event_id"], row["source_site"], row["author"])
        reason = "accepted"
        if i in conflicts:
            reason = "conflicting_site_author"
        elif row.get("document_sha256") and rawkey in seen_hash:
            reason = "identical_document_hash"
        elif row.get("article_body_sha256") and bodykey in seen_body:
            reason = "identical_article_body_hash"
        elif authkey in seen_author:
            reason = "duplicate_site_author"
        if reason == "accepted":
            accepted.append(row)
            seen_hash.add(rawkey)
            seen_body.add(bodykey)
            seen_author.add(authkey)
        audit.append({"row_index_0based": i, "event_id": row["event_id"], "source_url": row["source_url"],
                      "author": row["author"], "document_sha256": row.get("document_sha256"), "decision": reason})
    return accepted, audit


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode()
    path.write_bytes(gzip.compress(payload, mtime=0))


def normalize_old(path, cases):
    frozen = {r["event_id"]: r for r in cases if r["cohort"].startswith("retrospective_")}
    rows, rejected = [], []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            frame = json.loads(line)
            case = frozen.get(frame["event_id"])
            if not case:
                continue
            same = frame["league"] == case["league"] and stamp(frame["kickoff"]) == stamp(case["kickoff"])
            same = same and all(team(frame[k], case["league"]) == team(case[k], case["league"]) for k in ("home", "away"))
            for index, f in enumerate(frame.get("raw_forecasts", [])):
                reason = None
                if not same:
                    reason = "old_frame_identity_mismatch"
                elif not f.get("author") or not f.get("source_url"):
                    reason = "missing_original_author_or_url"
                elif f.get("side") not in case["sides"]:
                    reason = "unsupported_original_selection"
                elif not f.get("observed_at"):
                    reason = "missing_original_observed_at"
                if reason:
                    rejected.append({"event_id": case["event_id"], "raw_forecast_index": index, "reason": reason})
                    continue
                pub = f.get("published_at_claim")
                try:
                    tier = availability(pub, f["observed_at"], case["kickoff"])
                except ValueError:
                    tier = "diagnostic_unparseable_original_publication"
                missing = []
                if not pub:
                    missing.append("original_publication_claim_missing")
                if not (f.get("document_sha256") or f.get("source_sha256")):
                    missing.append("original_document_hash_missing")
                rows.append(dict(event_id=case["event_id"], league=case["league"], kickoff=case["kickoff"],
                    cohort=case["cohort"], home=case["home"], away=case["away"], selection=f["side"],
                    market="승무패" if "draw" in case["sides"] else "full_game_moneyline",
                    market_evidence="frozen_primary_direction_schema; raw_forecasts.side; no ancillary market inferred",
                    author=f["author"], source_site=urlsplit(f["source_url"]).hostname, source_url=f["source_url"],
                    published_at_claim=pub, observed_at=f["observed_at"],
                    document_sha256=f.get("document_sha256") or f.get("source_sha256"),
                    document_hash_kind="original_input_document_sha256" if f.get("document_sha256") else "original_input_source_sha256",
                    original_document_sha256=f.get("document_sha256"), original_source_sha256=f.get("source_sha256"),
                    source_reported_published_at=f.get("source_reported_published_at"),
                    metadata_missing_reasons=missing,
                    availability_tier=tier, join_evidence={"method": "exact_frozen_event_id_league_teams_kickoff",
                        "candidate_count": 1, "raw_forecast_index": index, "raw_row_1based": f.get("raw_row_1based"),
                        "json_pointer": f.get("json_pointer"), "original_source_file": f.get("source_file"),
                        "scores_used": False}))
    return rows, rejected


def audit_vectors(rows, cases):
    audit = []
    for case in cases:
        if not case["cohort"].startswith("retrospective_"):
            continue
        selected = [r for r in rows if r["event_id"] == case["event_id"]]
        counts = Counter(r["selection"] for r in selected)
        vector = [counts[s] / len(selected) for s in case["sides"]] if selected else None
        external = case["external_p"]
        agrees = vector is not None and len(vector) == len(external) and all(abs(a-b) < 1e-10 for a, b in zip(vector, external))
        audit.append({"event_id": case["event_id"], "league": case["league"], "sides": case["sides"],
                      "old_vote_count": len(selected), "reconstructed_vote_share": vector,
                      "frozen_external_p": external, "exact_vector_agreement": agrees,
                      "eligible_for_union": agrees,
                      "semantics": "direction shares only; not calibrated probability; no outcomes inspected"})
    return audit


def build(cache_dir, cases_path, old_path, output=OUT):
    cache_dir, output = Path(cache_dir), Path(output)
    cases = read_cases(cases_path)
    old_raw, rejected = normalize_old(old_path, cases)
    old, old_audit = deduplicate(old_raw)
    vectors = audit_vectors(old, cases)
    eligible = {r["event_id"] for r in vectors if r["eligible_for_union"]}
    for row in old:
        row["eligible_for_union"] = row["event_id"] in eligible
        row["eligibility_scope"] = "frozen_old_vector_gate_only; downstream publication and hash checks remain required"
        row["union_gate_reason"] = "old_vector_agrees" if row["eligible_for_union"] else "old_vector_mismatch"
    captures, new_raw, nojoin, forward = [], [], [], []
    capture_log = cache_dir / "browser-captures.jsonl"
    for line in capture_log.read_text(encoding="utf-8").splitlines() if capture_log.exists() else []:
        item = json.loads(line)
        raw = (cache_dir / item["capture_path"]).read_bytes()
        if sha(raw) != item["document_sha256"]:
            raise ValueError("capture_hash_mismatch")
        meta = {k: v for k, v in item.items() if k != "capture_path"}
        try:
            article = parse_article(raw, item["url"], item["observed_at"], item["document_hash_kind"])
            meta.update(parse_status="parsed", author=article["author"], league=article["league"],
                        published_at_claim=article["published_at_claim"], source_date=article["source_date"])
            try:
                vote = join_article(article, cases)
                vote["eligible_for_union"] = vote["event_id"] in eligible and vote["availability_tier"] == "retrospective_claimed_pregame"
                vote["union_gate_reason"] = "old_vector_agrees" if vote["eligible_for_union"] else "no_eligible_old_claimed_case"
                new_raw.append(vote)
                meta.update(join_status="matched", event_id=vote["event_id"])
            except ValueError as exc:
                meta["join_status"] = str(exc)
                nojoin.append(dict(article, reason=str(exc), event_id=None, kickoff=None))
                if article["source_date"] > stamp(article["observed_at"]).astimezone(KST).date().isoformat():
                    # Date in future is a diagnostic candidate, NOT verified pregame.
                    forward.append(dict(article, reason=str(exc), event_id=None, kickoff=None,
                                        availability_tier="forward_unmatched_kickoff_unverified"))
        except ValueError as exc:
            meta["parse_status"] = str(exc)
            nojoin.append(dict(meta, reason=str(exc), event_id=None, kickoff=None))
        captures.append(meta)
    new, new_audit = deduplicate(new_raw)
    union, union_audit = deduplicate(old + new)
    files = {
        "old-votes-raw.jsonl.gz": old_raw,
        "old-votes-normalized.jsonl.gz": old,
        "old-votes-union-eligible.jsonl.gz": [r for r in old if r["eligible_for_union"]],
        "old-rejected.jsonl.gz": rejected,
        "old-dedup-audit.jsonl.gz": old_audit,
        "old-vector-audit.jsonl.gz": vectors,
        "new-votes-raw.jsonl.gz": new_raw,
        "new-votes.jsonl.gz": new,
        "new-dedup-audit.jsonl.gz": new_audit,
        "union-dedup-audit.jsonl.gz": union_audit,
        "document-inventory.jsonl.gz": captures,
        "nojoin.jsonl.gz": nojoin,
        "forward-unmatched.jsonl.gz": forward,
    }
    for filename, rows in files.items():
        write_jsonl(output / filename, rows)
    # All original author/document metadata; no outcome, odds or model evaluation.
    source_counts = Counter()
    original_frames = 0
    original_votes = 0
    outside = 0
    frozen_ids = {r["event_id"] for r in cases if r["cohort"].startswith("retrospective_")}
    with gzip.open(old_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            frame = json.loads(line)
            original_frames += 1
            outside += frame["event_id"] not in frozen_ids
            for forecast in frame.get("raw_forecasts", []):
                original_votes += 1
                source_counts[(urlsplit(forecast.get("source_url", "")).hostname, forecast.get("author"))] += 1
    probes = []
    for p in sorted(cache_dir.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "request_number" in data:
            probes.append(data)
        elif p.name == "browser-other-probes-actual.json":
            probes.extend(data)
    # Cached public list metadata only: enough to audit search-year false matches.
    searches = []
    for p in sorted(cache_dir.glob("browser-*.json")):
        if p.name.startswith("browser-search") or p.name in {"browser-listing.json", "browser-kbo-page5.json"}:
            data = json.loads(p.read_text(encoding="utf-8"))
            searches.append({"source_url": data["url"], "observed_at": data["observed_at"],
                             "capture_sha256": sha(p.read_bytes()),
                             "snapshot_quality": data.get("snapshot_quality", "listing_metadata_only_not_vote_evidence"),
                             "links": [] if data.get("navigation_race") else data.get("links", [])})
    dump(output / "source-inventory.json", {"old_original_frames": original_frames,
        "old_original_votes": original_votes, "old_frames_outside_frozen_claimed_cases": outside,
        "original_authors": [{"source_site": site, "author": author, "raw_votes": count}
                             for (site, author), count in sorted(source_counts.items())],
        "new_authors": sorted({r["source_site"] + ":" + r["author"] for r in new}),
        "probes": probes, "public_searches": searches,
        "independence_caveat": RULES["independence"]})
    result = {"schema": "tipster-source-link-build-v1", "outcomes_or_ROI_read": False,
        "models_fit": False, "raw_cache_dir": str(cache_dir.resolve()),
        "raw_http_successes": sum(p.get("status") == 200 for p in probes),
        "browser_article_captures": len(captures), "http_response_hash_available_for_new_articles": False,
        "document_hash_kind": "article_dom_utf8 (selected article DOM, not raw HTTP response)",
        "old_original_frames": original_frames, "old_original_votes": original_votes,
        "old_raw_matched_votes": len(old_raw), "old_deduplicated_votes": len(old),
        "old_claimed_cases": len(vectors), "old_vector_agree_cases": len(eligible),
        "old_vector_mismatch_cases": len(vectors)-len(eligible),
        "old_union_eligible_votes": sum(r["eligible_for_union"] for r in old),
        "old_availability_tiers": dict(Counter(r["availability_tier"] for r in old)),
        "old_missing_hash_votes": sum(not r["document_sha256"] for r in old),
        "old_missing_publication_votes": sum(not r["published_at_claim"] for r in old),
        "new_raw_matched_votes": len(new_raw), "new_deduplicated_votes": len(new),
        "new_unique_matched_cases": len({r["event_id"] for r in new}),
        "new_by_league": dict(Counter(r["league"] for r in new)),
        "new_by_availability": dict(Counter(r["availability_tier"] for r in new)),
        "nojoin_count": len(nojoin), "ambiguous_fixture_count": sum(r["reason"] == "ambiguous_fixture" for r in nojoin),
        "nojoin_reasons": dict(Counter(r["reason"] for r in nojoin)),
        "forward_unmatched_count": len(forward),
        "union_raw_records": len(old)+len(new), "union_deduplicated_records_including_mismatch_cases": len(union),
        "union_duplicates": len(old)+len(new)-len(union),
        "eligible_union_records": sum(r["eligible_for_union"] for r in union),
        "eligible_union_records_semantics": "old-vector agreement gate only; not certification of missing historical publication/hash or proven independent authors",
        "inputs": [{"path": str(Path(p).resolve()), "sha256": sha(Path(p).read_bytes())}
                   for p in (cases_path, old_path)],
        "artifacts": {name: {"rows": len(rows), "sha256": sha((output/name).read_bytes())}
                      for name, rows in files.items()},
    }
    dump(output / "manifest.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--probe", action="append", default=[])
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--cases", type=Path, default=ROOT / "experiments/pregame-feature-rd-20261030/inputs/cases.jsonl.gz")
    parser.add_argument("--old-input", type=Path)
    args = parser.parse_args()
    freeze()
    cache = PublicCache(args.cache_dir)
    if args.build:
        if not args.old_input:
            parser.error("--old-input required for --build")
        result = build(args.cache_dir, args.cases, args.old_input)
        print(json.dumps({k: v for k, v in result.items() if k not in {"inputs", "artifacts"}}, ensure_ascii=False))
    for url in args.probe:
        meta, raw = cache.fetch(url)
        soup = BeautifulSoup(raw, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else None
        links = [{"url": urljoin(url, a["href"]), "title": a.get_text(" ", strip=True)[:110]}
                 for a in soup.select("a[href]") if a.get_text(strip=True)]
        print(json.dumps({"fetch": meta, "title": title, "links": links[:35]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
