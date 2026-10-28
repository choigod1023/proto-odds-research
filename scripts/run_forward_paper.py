"""Forward-only paper runner. GitHub journal stores immutable decisions; no wagering API."""
import base64
import copy
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
import hashlib
import math
from pathlib import Path
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts.audit_recommendation_shadow import KST, LIMIT, STAKE, payout, stamp

BRANCH = "codex/paper-state-v1"
FILE = "paper/journal.json"
POLICY = "tipster-vote-v1"
ACCOUNT = "tipster-vote-single-v1"
LEGACY_PROTOCOL = "forward-paper-v1"
PROTOCOL = "forward-paper-tipster-vote-v1"
IDENTITY = ("id", "recorded_at", "published_at", "kickoff_at", "sport", "league",
            "home", "away", "round", "market", "market_label", "n_way",
            "sel", "odds", "probability", "policy")

def identity(row):
    return {**{k: row.get(k) for k in IDENTITY},
            **{k: copy.deepcopy(row[k]) for k in ("selection_policy", "tipster_selection") if k in row}}

def settlement_row(leg, history, now):
    """Allow timestamp-only republication, never changed betting/probability terms.

    Preserve the original journal revision. A fresh publication timestamp is not
    a new outcome when event, market, selection, price AND probability agree.
    Conflicting/duplicate identities remain unresolved.
    """
    candidates = [r for r in history if r.get("id") == leg.get("id")
                  and r.get("kickoff_at") == leg.get("kickoff_at")]
    if len(candidates) != 1:
        return None
    row = candidates[0]
    if identity(row) == leg:
        return row
    stable = [k for k in leg if k not in ("recorded_at", "published_at")]
    if any(row.get(k) != leg.get(k) for k in stable):
        return None
    try:
        kickoff = stamp(leg["kickoff_at"])
        for field in ("recorded_at", "published_at"):
            original, current = stamp(leg[field]), stamp(row[field])
            if not original <= kickoff-timedelta(minutes=30) or not original <= current <= now:
                return None
    except (KeyError, TypeError, ValueError):
        return None
    return row

def ticket_summary(events, account):
    bets = [e for e in events if e["type"] == "bet" and e["account"] == account]
    settlements = {e["ticket"]: e for e in events if e["type"] == "settlement"}
    months = {}
    net = peak = drawdown = 0
    for b in bets:
        months.setdefault(b["month"], 0)
    for e in events:
        if e["type"] == "settlement" and e["account"] == account:
            months[e["month"]] = months.get(e["month"], 0) + e["net"]
            net += e["net"]
            peak = max(peak, net)
            drawdown = max(drawdown, peak-net)
    done = [b for b in bets if b["ticket"] in settlements]
    return dict(bets=len(bets), settled=len(done), net=net,
                pending_stake=sum(b["stake"] for b in bets if b["ticket"] not in settlements), monthly_net=months,
                roi=net/sum(b["stake"] for b in done) if done else None, max_drawdown=drawdown)

def budget_summary(events, month):
    """One shared paper budget, including retired comparison accounts and old reserves.

    Realized P/L stays attributed to placement month. Every unresolved stake,
    including prior months, remains reserved. Migration never resets spending.
    """
    settled = {e["ticket"] for e in events if e["type"] == "settlement"}
    net = sum(e["net"] for e in events if e["type"] == "settlement" and e["month"] == month)
    pending = sum(e["stake"] for e in events if e["type"] == "bet" and e["ticket"] not in settled)
    return dict(month=month, monthly_net=net, pending_stake=pending,
                available_loss_budget=LIMIT + net - pending, monthly_loss_limit=LIMIT,
                scope="all_accounts_all_leagues_including_legacy")


def event_key(row):
    return (stamp(row["kickoff_at"]), row["sport"], row["league"], row["home"], row["away"])


def tipster_rows(history, now):
    """Latest observed full-game revision only; never backfill or use highlight gates."""
    latest = {}
    for row in history:
        try:
            if row.get("market") not in ("승패", "승무패") or row.get("market_label") not in (None, ""):
                continue
            key = event_key(row)
            at = max(stamp(row["recorded_at"]), stamp(row["published_at"]))
            if key not in latest or at > latest[key][0]:
                latest[key] = (at, [row])
            elif at == latest[key][0]:
                latest[key][1].append(row)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    rows = []
    sides = {"home": "홈", "away": "원정", "draw": "무", "홈": "홈", "원정": "원정", "무": "무"}
    for key, (at, revisions) in latest.items():
        try:
            if len(revisions) != 1:
                continue
            row = revisions[0]
            info = row.get("tipster_selection") or {}
            side = sides.get(info.get("selected_side"))
            shares = {sides.get(k): float(v) for k, v in info.get("vote_shares", {}).items()}
            leaders = [s for s, v in shares.items() if v == max(shares.values())]
            observed = stamp(info["latest_observed_at"])
            p, o = float(row["probability"]), float(row["odds"])
            if (row.get("selection_policy") != POLICY or info.get("version") != POLICY
                    or info.get("status") != "applied" or type(info.get("authors")) is not int
                    or info["authors"] < 1 or not info.get("evidence_hash")
                    or side is None or row.get("sel") != side or leaders != [side]
                    or any(s is None or not math.isfinite(v) or not 0 <= v <= 1 for s,v in shares.items())
                    or not math.isfinite(p) or not 0 < p < 1 or not math.isfinite(o) or o <= 1
                    or row.get("n_way") not in (2, 3) or (side == "무" and row["n_way"] != 3)
                    or not timedelta(0) <= now-at <= timedelta(hours=2)
                    or not observed <= at or observed > key[0]-timedelta(minutes=30)
                    or key[0].date() != now.date() or key[0] <= now+timedelta(minutes=30)
                    or row.get("result") not in (None, "unknown") or row.get("settled_at")
                    or row.get("official_market_result") is not None):
                continue
            rows.append(row)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    # No probability/EV ranking: calibrated market probabilities are not vote shares.
    return sorted(rows, key=lambda r: (event_key(r), str(r["id"])))


def advance(journal, doc, now, digest=None):
    """Pure transition: no backfill; all placements and settlements use observation time."""
    if journal and any(e.get("protocol") not in (LEGACY_PROTOCOL, PROTOCOL) for e in journal):
        raise ValueError("Unknown journal protocol")
    if journal and stamp(journal[-1]["at"]) > now:
        raise ValueError("Clock went backwards")
    events = copy.deepcopy(journal)
    added = []
    def append(kind, **kw):
        event = dict(type=kind, protocol=PROTOCOL, at=now.isoformat(), **kw)
        events.append(event)
        added.append(event)
    if not any(e["type"] == "transition" and e.get("account") == ACCOUNT for e in events):
        append("transition", account=ACCOUNT, selection_policy=POLICY, stake=STAKE,
               retired_accounts=sorted({e["account"] for e in journal if e["type"] == "bet" and e["account"] != ACCOUNT}),
               budget_scope="all_accounts_all_leagues_including_legacy", monthly_loss_limit=LIMIT,
               legacy_mode="settlement_only")
    # Validate shape before treating an empty/invalid response as a successful observation.
    if doc is not None:
        if not isinstance(doc.get("recommendation_history"), dict):
            raise ValueError("Missing recommendation history")
        generation = stamp(doc["generated_at"])
        status = "fresh" if timedelta(0) <= now-generation <= timedelta(hours=2) else "stale"
    else:
        status = "unavailable"
    previous = next((e for e in reversed(events) if e["type"] == "source"), None)
    if not previous or previous["status"] != status:
        append("source", status=status, generated_at=doc.get("generated_at") if doc else None)
    if doc is None:
        return events, added
    history = list(doc["recommendation_history"].values())
    # Preserve frozen terms; accept timestamp-only republication, not new prices/probabilities.
    settled = {e["ticket"] for e in events if e["type"] == "settlement"}
    for bet in [e for e in events if e["type"] == "bet" and e["ticket"] not in settled]:
        results = []
        for leg in bet["legs"]:
            row = settlement_row(leg, history, now)
            valid = row and row.get("result_source") == "official" and row.get("result") in ("hit","miss","void")
            if valid:
                try:
                    valid = stamp(leg["kickoff_at"]) <= stamp(row["settled_at"]) <= now
                except (KeyError, TypeError, ValueError):
                    valid = False
            results.append(row if valid else None)
        if any(r and r["result"] == "miss" for r in results) or all(results):
            if any(r and r["result"] == "miss" for r in results):
                returned = 0
            elif bet["account"] == ACCOUNT:
                returned = (int((Decimal(str(bet["legs"][0]["odds"])) * bet["stake"]).quantize(
                    Decimal("1"), rounding=ROUND_DOWN)) if results[0]["result"] == "hit" else bet["stake"])
            else:
                # Preserve the historical two-leg rounding convention for old tickets.
                returned = payout([float(leg["odds"]) if r["result"] == "hit" else 1
                                   for leg, r in zip(bet["legs"], results)])
            append("settlement", ticket=bet["ticket"], account=bet["account"], month=bet["month"],
                   net=returned-bet["stake"], payout=returned, source_sha256=digest)
        elif now-max(stamp(l["kickoff_at"]) for l in bet["legs"]) > timedelta(days=3):
            if not any(e["type"] == "attention" and e["ticket"] == bet["ticket"] for e in events):
                append("attention", ticket=bet["ticket"], reason="official_revision_unsettled_72h")
    if status != "fresh":
        return events, added
    month = now.strftime("%Y-%m")
    used = {event_key(leg) for e in events if e["type"] == "bet" for leg in e["legs"]}
    for row in tipster_rows(history, now):
        key = event_key(row)
        if key in used or budget_summary(events, month)["available_loss_budget"] < STAKE:
            continue
        ticket = hashlib.sha256(json.dumps([ACCOUNT, row["kickoff_at"], *key[1:]], ensure_ascii=False).encode()).hexdigest()[:24]
        append("bet", ticket=f"{ACCOUNT}:{ticket}", account=ACCOUNT, month=month,
               stake=STAKE, source_sha256=digest, generated_at=doc["generated_at"],
               selection_policy=POLICY, legs=[identity(row)],
               available_loss_budget_before=budget_summary(events, month)["available_loss_budget"])
        used.add(key)
    assert events[:len(journal)] == journal
    return events, added

def api(path, method="GET", body=None):
    req = Request("https://api.github.com/"+path, method=method,
                  data=json.dumps(body).encode() if body is not None else None,
                  headers={"Authorization":"Bearer "+os.environ["GH_TOKEN"],
                           "Accept":"application/vnd.github+json","Content-Type":"application/json"})
    with urlopen(req,timeout=30) as response:
        return json.load(response)

def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    root = "repos/"+repo
    # Explicit state initialization only; subsequent missing state fails closed.
    try:
        stored = api(root+"/contents/"+FILE+"?ref="+BRANCH)
        journal = json.loads(base64.b64decode(stored["content"]))
    except HTTPError as error:
        if error.code != 404 or os.environ.get("PAPER_INITIALIZE") != "true":
            raise
        try:
            api(root+"/git/ref/heads/"+BRANCH)
        except HTTPError as absent:
            if absent.code != 404: raise
            sha = api(root+"/git/ref/heads/main")["object"]["sha"]
            api(root+"/git/refs","POST",{"ref":"refs/heads/"+BRANCH,"sha":sha})
        else:
            raise RuntimeError("Existing state branch has no journal; refusing to reset")
        stored, journal = None, []
    now = datetime.now(KST)
    try:
        with urlopen("https://proto-odds-collector.fly.dev/api/today-recommendations",timeout=25) as response:
            raw = response.read()
        doc = json.loads(raw)
        stamp(doc["generated_at"])
        if not isinstance(doc.get("recommendation_history"), dict): raise ValueError("Invalid source")
        digest = hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        doc, digest = None, None
    events, added = advance(journal,doc,now,digest)
    if added:
        payload = {"message":"paper: append forward observations","branch":BRANCH,
                   "content":base64.b64encode(json.dumps(events,ensure_ascii=False,indent=2).encode()).decode()}
        if stored: payload["sha"] = stored["sha"]
        api(root+"/contents/"+FILE,"PUT",payload) # CAS failure aborts; no lost updates.
    report = paper_report(events, now, len(added))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if os.environ.get("PAPER_SUMMARY_PATH"):
        Path(os.environ["PAPER_SUMMARY_PATH"]).write_text(rendered+"\n", encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write("모의 운용: 신규 거래는 팁스터 단폴 계정 하나. 기존 비교 계정은 정산만 계속합니다.\n\n")
            stream.write("월 손실 5만 원은 기존 계정 손익과 모든 미정산 금액을 합산합니다. 실매수 없음·수익성 미검증.\n\n")
            stream.write("```json\n"+rendered+"\n```\n")
    print(rendered)


def paper_report(events, now, new_events=0):
    accounts = sorted({e["account"] for e in events if e["type"] == "bet"} | {ACCOUNT})
    return dict(new_events=new_events, protocol=PROTOCOL, primary_account=ACCOUNT,
                as_of=now.isoformat(), source=next((e for e in reversed(events) if e["type"] == "source"), None),
                budget=budget_summary(events, now.strftime("%Y-%m")),
                accounts={a: dict(ticket_summary(events, a),
                                  mode="primary_singles" if a == ACCOUNT else "legacy_comparison_settlement_only")
                          for a in accounts},
                verification_status="unvalidated", real_money=False,
                attention_tickets=[e["ticket"] for e in events if e["type"] == "attention"
                                   and e["ticket"] not in {r["ticket"] for r in events if r["type"] == "settlement"}])

if __name__ == "__main__":
    main()
