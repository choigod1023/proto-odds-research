"""Offline, outcome-blind K-league XI features; no runtime DB imports.

Date-only facts are structurally lagged, never authenticated pregame captures.
Feature definitions are fixed in FEATURE_PROTOCOL before evaluating returns.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
# Explicit source-name aliases only; never infer identity from scores/results.
TEAM_ALIASES = {"울산HDFC": "울산", "전북현대": "전북", "포항스틸": "포항",
    "FC서울": "서울", "강원FC": "강원", "광주FC": "광주", "대구FC": "대구",
    "대전하나": "대전", "김천상무": "김천", "인천유나": "인천", "제주SKFC": "제주",
    "수원삼성": "수원", "FC안양": "안양", "부천FC": "부천", "김포FC": "김포",
    "부산아이": "부산", "서울이랜": "서울E"}
FEATURE_PROTOCOL = {
    "version": 1, "outcomes_read": False, "roi_selection": False,
    "history": "same-season observed XI; date strictly before decision local date",
    "groups": {"prior_only": ["observed_games", "history_age_days", "previous_xi_regular_share",
        "regular_top11_start_share", "previous_churn", "mean_last5_churn",
        "previous_xi_mean_starts14"],
        "retrospective_current_xi_DIAGNOSTIC_ONLY": ["regular_share", "changes", "regulars_missing"]},
    "freshness": "workload and previous-XI features omitted after >30 day source gap",
    "missing": "omit, never impute; workload = starts proxy, not minutes",
}


def stamp(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return dt


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump_gz(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def adapt(raw):
    """Keep date/team/XI only. Discard scores, cards and target-match player stats."""
    out, rejected = [], Counter()
    for key, g in raw.items():
        for side in ("home", "away"):
            players = ((g.get("data") or {}).get(side) or {}).get("players") or []
            xi = {str(p["playerId"]) for p in players if p.get("playerId") is not None}
            if len(players) != 11 or len(xi) != 11:
                rejected["not_exactly_11_unique_starters"] += 1
                continue
            out.append({"date": g["date"], "team": g[side], "xi": sorted(xi),
                        "source_id": str(key), "home": g["home"], "away": g["away"]})
    return out, dict(rejected)


def prior_features(history, team, decision):
    cutoff = stamp(decision).astimezone(KST).date()
    h = sorted([g for g in history if g["team"] == team
                and g["date"][:4] == str(cutoff.year)
                and g["date"] < cutoff.isoformat()], key=lambda g: (g["date"], g["source_id"]))
    if not h:
        return {}, [], "no_same_season_prior_xi"
    # Reject ambiguous same-day/team duplicates rather than silently double count.
    if len({g["date"] for g in h}) != len(h):
        return {}, [], "ambiguous_team_date_history"
    counts = Counter(p for g in h for p in g["xi"])
    regulars = {p for p, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:11]}
    age = (cutoff - datetime.fromisoformat(h[-1]["date"]).date()).days
    f = {"observed_games": len(h), "history_age_days": age,
         "regular_top11_start_share": sum(counts[p] for p in regulars) / (11 * len(h))}
    if age > 30:
        return f, h, "stale_history_previous_xi_and_workload_omitted"
    f["previous_xi_regular_share"] = sum(counts[p] / len(h) for p in h[-1]["xi"]) / 11
    recent = [g for g in h if (cutoff - datetime.fromisoformat(g["date"]).date()).days <= 14]
    workload = Counter(p for g in recent for p in g["xi"])
    f["previous_xi_mean_starts14"] = sum(workload[p] for p in h[-1]["xi"]) / 11
    churn = [len(set(b["xi"]) - set(a["xi"])) for a, b in zip(h, h[1:])]
    if churn:
        f["previous_churn"] = churn[-1]
        f["mean_last5_churn"] = sum(churn[-5:]) / len(churn[-5:])
    return f, h, None


def extract(rosters, history, source_hash, archive_league=None):
    rows, diagnostics, audit = [], [], []
    seen = set()
    for r in rosters:
        archive = r.get("cohort") == "historical_market_proxy" and archive_league is not None and r.get("league") == archive_league
        if not r.get("event_id", "").startswith("kleague:") and not archive:
            continue
        decision = r.get("available_at") or (stamp(r["kickoff"]) - timedelta(minutes=30)).isoformat()
        key = (r["cohort"], r["event_id"], decision)
        if key in seen:
            continue
        seen.add(key)
        base = {k: r[k] for k in ("cohort", "event_id", "league", "kickoff")}
        base.update(decision_at=decision, features={}, feature_evidence={}, source_ids=[],
                    source_meta={"source_game_dates": {}, "fact_dates": []})
        if stamp(decision) >= stamp(r["kickoff"]):
            audit.append({**base, "reason": "decision_not_before_kickoff"})
            continue
        is_k1 = archive or r["event_id"].split(":")[2] == "1"
        reasons = []
        for side in ("home", "away"):
            if not is_k1:
                reasons.append(side + ":no_kleague2_xi_source")
                continue
            team = TEAM_ALIASES.get(r[side], r[side])
            f, h, reason = prior_features(history, team, decision)
            if reason:
                reasons.append(side + ":" + reason)
            for name, value in f.items():
                name = "soccer_prior_" + side + "_" + name
                base["features"][name] = value
                base["feature_evidence"][name] = {
                    "max_fact_time": None, "observed_at": None,
                    "max_fact_date": h[-1]["date"], "latest_source_game_date": h[-1]["date"],
                    "source_sha256": [source_hash], "availability_tier": "retrospective_lagged",
                    "note": "Date-only structural lag; last fact date=" + h[-1]["date"]
                    + "; partial observed XI archive, not complete schedule; no authenticated capture."}
            base["source_ids"].extend(g["source_id"] for g in h)
            base["source_meta"]["source_game_dates"].update({g["source_id"]: g["date"] for g in h})
            # Optional ex-post diagnostic is emitted separately, never into model rows.
            targets = [g for g in history if g["team"] == team
                       and g["date"] == stamp(r["kickoff"]).astimezone(KST).date().isoformat()
                       and g["home"] == TEAM_ALIASES.get(r["home"], r["home"])
                       and g["away"] == TEAM_ALIASES.get(r["away"], r["away"])]
            if h and len(targets) == 1:
                counts = Counter(p for g in h for p in g["xi"])
                regulars = {p for p, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:11]}
                xi = set(targets[0]["xi"])
                vals = {"regular_share": sum(counts[p] / len(h) for p in xi) / 11,
                        "changes": len(xi - set(h[-1]["xi"])),
                        "regulars_missing": len(regulars - xi)}
                diagnostics.append({**{k: base[k] for k in ("cohort", "event_id", "league", "kickoff", "decision_at")},
                    "features": {"soccer_retrospective_current_xi_" + side + "_" + k: v for k, v in vals.items()},
                    "feature_evidence": {"soccer_retrospective_current_xi_" + side + "_" + k: {
                        "max_fact_time": None, "observed_at": None, "source_sha256": [source_hash],
                        "max_fact_date": max(g["date"] for g in h + targets),
                        "latest_source_game_date": max(g["date"] for g in h + targets),
                        "availability_tier": "unverified", "note": "TARGET XI EX POST; never eligible pregame without capture proof"} for k in vals},
                    "source_ids": sorted({g["source_id"] for g in h + targets}),
                    "source_meta": {"source_game_dates": {g["source_id"]: g["date"] for g in h + targets},
                                    "fact_dates": sorted({g["date"] for g in h + targets})}})
        base["source_ids"] = sorted(set(base["source_ids"]))
        base["source_meta"]["fact_dates"] = sorted(set(base["source_meta"]["source_game_dates"].values()))
        rows.append(base)
        audit.append({"cohort": r["cohort"], "event_id": r["event_id"], "decision_at": decision,
                      "feature_count": len(base["features"]), "reasons": reasons,
                      "current_xi": "no_authenticated_predecision_capture"})
    return rows, diagnostics, audit


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("redirect blocked; supply canonical official URL")


class FixtureIdentity(TypedDict):
    event_id: str
    source_fixture_id: str
    league: str
    kickoff: str
    home: str
    away: str


class CaptureClock(TypedDict):
    url: str
    request_started_at: str
    observed_at: str
    source_sha256: str


def parse_xi_intake(payload: bytes, clock: CaptureClock,
                    expected: FixtureIdentity, decision_at: str) -> dict:
    """Pure typed NORMALIZED intake contract, NOT a discovered official endpoint.

    Body schema: {fixture: FixtureIdentity, starters: {home: [player_id]*11,
    away: [player_id]*11}}. Caller must map a verified official response to this
    schema. Caller-provided clock is checked, not authenticated: tier stays
    unverified. No publication time is inferred and no model rows are produced.
    """
    u = urlparse(clock["url"])
    if u.scheme != "https" or u.hostname not in ("kleague.com", "www.kleague.com") or u.username or u.password or u.port not in (None, 443):
        raise ValueError("official HTTPS source required")
    if len(payload) > 1_000_000 or sha(payload) != clock["source_sha256"]:
        raise ValueError("payload size/hash mismatch")
    required = set(FixtureIdentity.__annotations__)
    if set(expected) != required or any(not isinstance(expected[k], str) or not expected[k].strip() for k in required):
        raise ValueError("complete explicit fixture identity required")
    start, observed = stamp(clock["request_started_at"]), stamp(clock["observed_at"])
    if not start <= observed <= stamp(decision_at) < stamp(expected["kickoff"]):
        raise ValueError("capture clock must precede decision and kickoff")
    body = json.loads(payload)
    if body.get("fixture") != expected:
        raise ValueError("fixture identity mismatch")
    starters = body.get("starters")
    if not isinstance(starters, dict) or set(starters) != {"home", "away"}:
        raise ValueError("both starting XIs required")
    for side in ("home", "away"):
        xi = starters[side]
        if not isinstance(xi, list) or len(xi) != 11 or any(not isinstance(p, str) or not p.strip() for p in xi) or len(set(xi)) != 11:
            raise ValueError("exactly 11 unique nonempty player IDs per side required")
    if set(starters["home"]) & set(starters["away"]):
        raise ValueError("player cannot start for both sides")
    return {"fixture": dict(expected), "starters": starters, "capture": dict(clock),
            "observed_at": clock["observed_at"], "max_fact_time": None,
            "publication_at": None, "availability_tier": "unverified",
            "status": "normalized_intake_validated_clock_not_authenticated",
            "note": "Capture-only tooling; official XI endpoint/parser not established"}


def capture(url, output, event_id=None, kickoff=None):
    """One bounded official HTTP GET; clock is recorded NOW, never backdated.

    For future match intake supply exact roster event ID/kickoff and official
    match-detail URL. Opaque HTML is retained, not misclassified as announced XI.
    """
    u = urlparse(url)
    if u.scheme != "https" or u.hostname not in ("www.kleague.com", "kleague.com") or u.username or u.password or u.port not in (None, 443):
        raise ValueError("only public HTTPS kleague.com URLs allowed")
    started = datetime.now(timezone.utc)
    if kickoff and stamp(kickoff) <= started:
        raise ValueError("forward intake requires future kickoff")
    record = {"url": url, "request_started_at": started.isoformat(), "event_id": event_id,
              "capability": "capture_only_no_official_xi_parser",
              "kickoff": kickoff, "availability_tier": "unverified", "normalized_xi": None}
    try:
        req = Request(url, headers={"User-Agent": "PregameResearch/1.0"})
        with build_opener(NoRedirect).open(req, timeout=15) as response:
            body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise ValueError("response exceeds 1MB")
            observed = datetime.now(timezone.utc).isoformat()
            digest = sha(body)
            filename = "capture-" + digest[:16] + ".raw.gz"
            with gzip.open(output / filename, "wb") as f:
                f.write(body)
            record.update(observed_at=observed, source_sha256=digest, raw_file=filename,
                          status=response.status, note="Official raw snapshot; XI parser/identity verification required before promotion")
    except Exception as exc:
        record.update(observed_at=None, error=type(exc).__name__ + ": " + str(exc),
                      attempt_ended_at=datetime.now(timezone.utc).isoformat())
    token = started.strftime("%Y%m%dT%H%M%S%fZ")
    (output / ("capture-attempt-" + token + ".json")).write_text(json.dumps(record, indent=2), encoding="utf-8", newline="\n")
    return record


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--raw", type=Path)
    p.add_argument("--roster", type=Path, action="append", default=[])
    p.add_argument("--archive", type=Path, help="Auxiliary historical market proxy; never actual-tipster performance")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--capture-url")
    p.add_argument("--event-id")
    p.add_argument("--kickoff")
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.capture_url:
        print(json.dumps(capture(args.capture_url, args.output, args.event_id, args.kickoff)))
        return
    if not args.raw or not args.roster:
        p.error("extraction requires --raw and at least one --roster")
    # Freeze definitions without reading outcomes or any evaluation artifact.
    protocol = json.dumps(FEATURE_PROTOCOL, indent=2).encode()
    (args.output / "protocol.json").write_bytes(protocol)
    (args.output / "team-aliases.json").write_text(json.dumps(TEAM_ALIASES, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    raw_bytes = args.raw.read_bytes()
    raw_hash = sha(raw_bytes)
    history, rejected = adapt(json.loads(raw_bytes))
    # Compact selected fields only; original full bytes are referenced by hash.
    with gzip.open(args.output / "raw-xi.json.gz", "wt", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",", ":"))
    manifest = [{"path": str(args.raw.resolve()), "sha256": raw_hash,
                 "role": "date-only retrospective XI", "observed_at": None}]
    rosters = []
    for path in args.roster:
        content = path.read_bytes()
        manifest.append({"path": str(path.resolve()), "sha256": sha(content), "role": "exact forecast frame"})
        rosters.extend(json.loads(line) for line in gzip.decompress(content).decode("utf-8").splitlines())
    rows, diagnostics, audit = extract(rosters, history, raw_hash)
    if args.archive:
        k1_leagues = {r["league"] for r in rosters if r.get("event_id", "").startswith("kleague:") and r["event_id"].split(":")[2] == "1"}
        if len(k1_leagues) != 1:
            raise ValueError("unambiguous K1 league identity required from actual roster")
        league = next(iter(k1_leagues))
        archive_content = args.archive.read_bytes()
        manifest.append({"path": str(args.archive.resolve()), "sha256": sha(archive_content),
                         "role": "AUXILIARY settled-only retrospective historical_market_proxy"})
        fields = ("cohort", "event_id", "league", "home", "away", "kickoff", "available_at")
        auxiliary = []
        for line in gzip.decompress(archive_content).decode("utf-8").splitlines():
            r = json.loads(line)
            if r.get("league") == league and r.get("cohort") == "historical_market_proxy":
                auxiliary.append({k: r.get(k) for k in fields})
        broad, _, broad_audit = extract(auxiliary, history, raw_hash, archive_league=league)
        # Require both sides' real previous-XI features; never substitute rest-only history.
        kept = [r for r in broad if all("soccer_prior_" + side + "_previous_xi_regular_share" in r["features"] for side in ("home", "away"))]
        dump_gz(args.output / "archive-features-AUXILIARY.jsonl.gz", kept)
        dump_gz(args.output / "archive-audit.jsonl.gz", broad_audit)
        ids = {r["event_id"] for r in kept}
        dump_gz(args.output / "archive-input-frames.jsonl.gz", [r for r in auxiliary if r["event_id"] in ids])
        archive_summary = {"candidate_k1_events": len(auxiliary), "exported_rows": len(kept),
                           "feature_counts": dict(Counter(len(r["features"]) for r in kept)),
                           "label": "AUXILIARY settled-only historical_market_proxy, not service or actual tipster",
                           "years": dict(Counter(r["kickoff"][:4] for r in kept))}
        (args.output / "archive-summary.json").write_text(json.dumps(archive_summary, indent=2), encoding="utf-8", newline="\n")
        print(json.dumps(archive_summary))
    dump_gz(args.output / "features.jsonl.gz", rows)
    dump_gz(args.output / "current-xi-DIAGNOSTIC-DO-NOT-FIT.jsonl.gz", diagnostics)
    dump_gz(args.output / "audit.jsonl.gz", audit)
    # Store only input frame identity, not targets/odds/outcomes.
    dump_gz(args.output / "input-frames.jsonl.gz", [{k: r.get(k) for k in
        ("cohort", "event_id", "league", "home", "away", "kickoff", "available_at")}
        for r in rosters if r.get("event_id", "").startswith("kleague:")])
    summary = {"rows": len(rows), "nonempty_rows": sum(bool(r["features"]) for r in rows),
        "unique_events": len({r["event_id"] for r in rows}),
        "nonempty_unique_events": len({r["event_id"] for r in rows if r["features"]}),
        "captured_pregame_features": 0, "diagnostic_side_rows": len(diagnostics),
        "history_side_rows": len(history), "rejected": rejected,
        "coverage_by_cohort": {c: {"rows": sum(r["cohort"] == c for r in rows),
            "nonempty": sum(r["cohort"] == c and bool(r["features"]) for r in rows)}
            for c in sorted({r["cohort"] for r in rows})},
        "reasons": dict(Counter(s for a in audit for s in a.get("reasons", []))),
        "protocol_sha256": sha(protocol), "roi": "parent evaluator; no local ROI examined"}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8", newline="\n")
    outputs = [{"path": f.name, "sha256": sha(f.read_bytes())} for f in sorted(args.output.iterdir()) if f.is_file() and f.name != "manifest.json"]
    (args.output / "manifest.json").write_text(json.dumps({"inputs": manifest, "outputs": outputs}, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
