"""Bounded research capture; no production adapter and no pregame claims.

Discovery stores link/title metadata only. Article inspection stores metadata,
hashes and <=160-character recommendation evidence, never complete articles.
Curated observations are the reviewed factual input for deterministic replay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/tipster-roi-expansion-20261028/korean"


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(name, default):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def append(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open("a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


def fetch(url, purpose):
    started = now()
    try:
        # Intentionally no retry, proxy rotation, cookies or challenge bypass.
        response = requests.get(url, timeout=15, allow_redirects=True)
        record = dict(url=url, transport="python_requests", purpose=purpose,
                      request_started_at=started, observed_at=now(), status=response.status_code,
                      final_url=response.url, sha256=hashlib.sha256(response.content).hexdigest(),
                      response_bytes=len(response.content))
        append("transport.jsonl", record)
        response.raise_for_status()
        return BeautifulSoup(response.content, "html.parser"), record
    except requests.RequestException as exc:
        append("transport.jsonl", dict(url=url, purpose=purpose, transport="python_requests",
                                     request_started_at=started, observed_at=now(),
                                     error=type(exc).__name__, detail=str(exc)[:300]))
        return None, None


def discover(pages):
    inventory = read_json("discovered.json", [])
    seen = {x["url"] for x in inventory}
    for page in pages:
        url = f"https://sportsanalysis1.com/?page={page}"
        soup, record = fetch(url, "listing_pagination")
        if soup is None:
            continue
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if "/entry/" not in href:
                continue
            full = urljoin(url, href)
            if full in seen:
                continue
            seen.add(full)
            title = unquote(href.split("/entry/", 1)[1])
            league = "K1" if "K리그1" in title else "K2" if "K리그2" in title else None
            if league is None and any(x in title for x in ["NPB", "세이부", "오릭스", "요미우리", "히로시마"]):
                league = "NPB"
            inventory.append(dict(url=full, title_slug=title, league_hint=league,
                                  listing_url=url, observed_at=record["observed_at"]))
    write_json("discovered.json", inventory)
    for i, row in enumerate(inventory):
        print(i, row["league_hint"], row["title_slug"])


def inspect_articles(indices):
    inventory = read_json("discovered.json", [])
    documents = read_json("documents.json", [])
    for i in indices:
        row = inventory[i]
        soup, record = fetch(row["url"], "article_explicit_pick_inspection")
        if soup is None:
            continue
        body = soup.select_one(".contents_style") or soup.select_one(".tt_article_useless_p_margin") or soup.select_one(".entry-content")
        if body is None:
            append("transport.jsonl", dict(url=row["url"], observed_at=now(),
                                         error="article_body_not_identified", purpose="parser_exclusion"))
            continue
        text = body.get_text(" ", strip=True)
        meta = {m.get("property") or m.get("name"): m.get("content") for m in soup.select("meta[content]")}
        doc = dict(url=row["url"], title=meta.get("og:title"),
                   author=meta.get("author") or meta.get("article:author") or
                   (soup.select_one(".author").get_text(" ", strip=True) if soup.select_one(".author") else None),
                   published=meta.get("article:published_time"),
                   observed_at=record["observed_at"], sha256=record["sha256"],
                   league_hint=row["league_hint"])
        # Inspect in memory; only reviewed, short evidence is persisted below.
        snippets = []
        for el in body.select("tr,p,li,h2,h3,h4"):
            t = el.get_text(" ", strip=True)
            if re.search(r"최종|추천|베팅|승리.*배당|예상|경기.*일시|2025년", t) and 3 < len(t) < 600:
                snippets.append(t)
        conclusions = [s[:160] for s in snippets if re.search(r"최종 판단은|최종 결론은|주력 추천|차선책 추천|최종 예측은|추천 베팅|최종 예측:|승리.*예측", s)]
        odds = [text[max(0, m.start()-30):m.end()+45] for m in re.finditer(r"\d+\.\d{1,2}", text)
                if re.search("배당", text[max(0, m.start()-60):m.end()+65])]
        doc["inspection_evidence"] = (conclusions[-3:] or [s[:160] for s in snippets[-2:]])
        doc["odds_evidence"] = odds[:5]
        print(json.dumps(dict(index=i, **doc, tail=text[-650:]), ensure_ascii=False))
        documents = [d for d in documents if d["url"] != doc["url"]] + [doc]
    write_json("documents.json", documents)


def build():
    observations = read_json("observations.json", [])
    rows, exclusions = [], []
    for article in observations:
        for pick in article["picks"]:
            record = {k: v for k, v in article.items() if k != "picks"}
            record.update(pick)
            record.setdefault("source_timezone", None)
            record.setdefault("reported_decimal_odds", None)
            record.setdefault("odds_time_quality", "not_reported")
            record.setdefault("line", None)
            record.setdefault("side", None)
            record.setdefault("selected_team", None)
            record["cutoff_proven"] = False
            record["teams_order_semantics"] = "source_order_only"
            record["pregame_evidence"] = "source_date_claim_only; first_observed_postgame"
            if record.get("exclusion_reason"):
                exclusions.append(record)
            else:
                rows.append(record)
    for name, records in [("candidates.jsonl", rows), ("excluded_picks.jsonl", exclusions)]:
        (OUT / name).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in records), encoding="utf-8")
    checks = validate(rows, exclusions)
    write_json("verification.json", checks)
    print(json.dumps(checks, ensure_ascii=False))


def validate(rows, exclusions):
    required = {"source", "author", "source_url", "source_reported_published_at", "source_timezone",
                "observed_at", "match_date", "league", "teams", "market", "selected_team", "side",
                "reported_decimal_odds", "odds_time_quality", "cutoff_proven", "evidence_excerpt"}
    keys = set()
    for row in rows + exclusions:
        assert required <= row.keys(), required - row.keys()
        assert row["cutoff_proven"] is False
        assert row["market"] in {"moneyline", "spread", "total"}
        assert len(row["teams"]) == 2 and row["teams"][0] != row["teams"][1]
        assert len(row["evidence_excerpt"]) <= 160
        assert "probability" not in row
        assert datetime.fromisoformat(row["observed_at"]).tzinfo is not None
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["match_date"])
        date.fromisoformat(row["match_date"])
        if row["market"] in {"moneyline", "spread"}:
            assert row["selected_team"] in row["teams"] or row["side"] == "draw"
        if row["market"] == "total":
            assert row["side"] in {"over", "under"} and isinstance(row["line"], (float, int))
        if row["reported_decimal_odds"] is not None:
            assert row["reported_decimal_odds"] > 1 and row["odds_time_quality"] != "not_reported"
        key = (row["source_url"], row["match_date"], tuple(row["teams"]), row["market"], row["selected_team"], row["side"], row["line"])
        assert key not in keys, key
        keys.add(key)
    return dict(candidate_rows=len(rows), excluded_rows=len(exclusions),
                unique_source_fixtures=len({(r["source"], r["match_date"], tuple(r["teams"])) for r in rows}),
                by_source=dict(Counter(r["source"] for r in rows)),
                by_league=dict(Counter(r["league"] for r in rows)),
                by_market=dict(Counter(r["market"] for r in rows)),
                rows_with_source_odds=sum(r["reported_decimal_odds"] is not None for r in rows),
                cutoff_proven_rows=0, schema_and_duplicate_checks="passed")


def self_test():
    """Offline positive/negative checks; never perform requests during replay."""
    import copy
    rows = [json.loads(s) for s in (OUT / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    excluded = [json.loads(s) for s in (OUT / "excluded_picks.jsonl").read_text(encoding="utf-8").splitlines()]
    validate(rows, excluded)
    checks = []
    for name, mutate in [
        ("duplicate_pick", lambda r: r.append(copy.deepcopy(r[0]))),
        ("team_not_in_fixture", lambda r: r[0].update(selected_team="unrelated_team")),
        ("unproven_cutoff_claim", lambda r: r[0].update(cutoff_proven=True)),
        ("odds_without_quality", lambda r: r[0].update(reported_decimal_odds=1.9)),
        ("synthetic_probability", lambda r: r[0].update(probability=.6)),
        ("naive_observation_time", lambda r: r[0].update(observed_at="2026-10-28T10:00:00")),
    ]:
        trial = copy.deepcopy(rows)
        mutate(trial)
        try:
            validate(trial, excluded)
        except AssertionError:
            checks.append(name)
        else:
            raise AssertionError(f"bad input accepted: {name}")
    assert len(excluded) == 1 and excluded[0]["exclusion_reason"].startswith("article_explicitly")
    assert len(rows) == 48 and len({(r["source"], r["match_date"], tuple(r["teams"])) for r in rows}) == 45
    assert Counter(r["source"] for r in rows) == {"happytoto25.com": 31, "fixtv24.com": 4, "sportsanalysis1.com": 13}
    assert not any(r["match_date"] == "2026-08-29" and r["teams"][0] == "니혼햄 파이터스" for r in rows)
    spread = [r for r in rows if r["market"] == "spread"]
    assert len(spread) == 1 and spread[0]["line"] == 1 and spread[0]["reported_decimal_odds"] is None
    assert len([r for r in rows if r["market"] == "total"]) == 3
    american = [r for r in rows if "reported_american_odds" in r]
    assert len(american) == 1 and american[0]["reported_decimal_odds"] is None
    # Re-run exact result checks against local detail, independent of source predictions.
    local = ROOT / "data/raw/detail/kleague_soccer_2023_2026.json"
    detail = json.loads(local.read_text(encoding="utf-8"))
    outcomes = read_json("outcome_facts.json", [])
    official_npb = [r for r in outcomes if r["league"] == "NPB"]
    assert len(official_npb) == 5
    assert all(r["is_official_result_source"] and r["match_date"] == "2026-09-02" for r in official_npb)
    assert all(isinstance(r[k], int) for r in outcomes for k in ["home_score", "away_score"])
    assert len([r for r in official_npb if r["home_score"] == r["away_score"]]) == 1
    for result in outcomes:
        if result["league"] != "K1":
            continue
        assert result["is_official_result_source"] is False
        actual = detail[result["game_id"]]
        assert all(result[k] == actual[k] for k in ["home", "away", "home_score", "away_score"])
        assert result["match_date"] == actual["date"]
        assert result["outcome_source_sha256"] == hashlib.sha256(local.read_bytes()).hexdigest()
    summary = dict(checked_at=now(), negative_cases_rejected=checks,
                   explicit_inplay_excluded=True, distinct_markets_preserved=True,
                   american_odds_not_falsely_reported_as_decimal=True,
                   exact_local_outcome_matches=sum(r["league"] == "K1" for r in outcomes),
                   official_npb_score_rows=len(official_npb),
                   network_requests=0, result="passed")
    write_json("parsing_checks.json", summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discover", nargs="+", type=int)
    parser.add_argument("--inspect", nargs="+", type=int)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.discover:
        discover(args.discover)
    if args.inspect:
        inspect_articles(args.inspect)
    if args.build:
        build()
    if args.self_test:
        self_test()
