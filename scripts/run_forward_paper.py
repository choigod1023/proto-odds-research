"""Forward-only paper runner. GitHub journal stores immutable decisions; no wagering API."""
import base64
import copy
from datetime import datetime, timedelta
import hashlib
import itertools
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from scripts.audit_recommendation_shadow import KST, LIMIT, STAKE, eligible, payout, stamp

BRANCH = "codex/paper-state-v1"
FILE = "paper/journal.json"
POLICY = "daily-p55-o150-unbounded-v4"
PROTOCOL = "forward-paper-v1"
TITLE = "[모의 운용] 관심 픽 전향 검증 결과"
IDENTITY = ("id", "recorded_at", "published_at", "kickoff_at", "sport", "league",
            "home", "away", "round", "market", "market_label", "n_way",
            "sel", "odds", "probability", "policy")

def identity(row):
    return {k: row.get(k) for k in IDENTITY}

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
    stable = [k for k in IDENTITY if k not in ("recorded_at", "published_at")]
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
            months[e["month"]] += e["net"]
            net += e["net"]
            peak = max(peak, net)
            drawdown = max(drawdown, peak-net)
    done = [b for b in bets if b["ticket"] in settlements]
    return dict(bets=len(bets), settled=len(done), net=net,
                pending_stake=(len(bets)-len(done))*STAKE, monthly_net=months,
                roi=net/(len(done)*STAKE) if done else None, max_drawdown=drawdown)

def advance(journal, doc, now, digest=None):
    """Pure transition: no backfill; all placements and settlements use observation time."""
    if journal and any(e.get("protocol") != PROTOCOL for e in journal):
        raise ValueError("Unknown journal protocol")
    if journal and stamp(journal[-1]["at"]) > now:
        raise ValueError("Clock went backwards")
    events = copy.deepcopy(journal)
    added = []
    def append(kind, **kw):
        event = dict(type=kind, protocol=PROTOCOL, at=now.isoformat(), **kw)
        events.append(event)
        added.append(event)
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
            returned = 0 if any(r and r["result"] == "miss" for r in results) else payout(
                [float(leg["odds"]) if r["result"] == "hit" else 1 for leg,r in zip(bet["legs"],results)])
            append("settlement", ticket=bet["ticket"], account=bet["account"], month=bet["month"],
                   net=returned-STAKE, payout=returned, source_sha256=digest)
        elif now-max(stamp(l["kickoff_at"]) for l in bet["legs"]) > timedelta(days=3):
            if not any(e["type"] == "attention" and e["ticket"] == bet["ticket"] for e in events):
                append("attention", ticket=bet["ticket"], reason="official_revision_unsettled_72h")
    if status != "fresh":
        return events, added
    rows, _ = eligible(doc, now, actual=True)
    rows = [r for r in rows if r.get("policy") == POLICY and r["p"] >= .55 and r["o"] >= 1.5
            and r["k"].date() == now.date() and r["k"] > now+timedelta(minutes=30)
            and now-r["t"] <= timedelta(hours=2) and r.get("result") not in ("hit","miss","void")]
    for weekly in (2,3):
        for mode in ("probability","ev_rank"):
            account = f"{mode}-weekly{weekly}"
            bets = [e for e in events if e["type"] == "bet" and e["account"] == account]
            if any(stamp(b["at"]).date() == now.date() for b in bets):
                continue
            if sum(stamp(b["at"]).isocalendar()[:2] == now.isocalendar()[:2] for b in bets) >= weekly:
                continue
            summary = ticket_summary(events, account)
            month = now.strftime("%Y-%m")
            if summary["monthly_net"].get(month,0)-summary["pending_stake"]-STAKE < -LIMIT:
                continue
            used = {(l["kickoff_at"], l["sport"], l["league"], l["home"],l["away"]) for b in bets for l in b["legs"]}
            options = [r for r in rows if (r["kickoff_at"],r["sport"],r["league"],r["home"],r["away"]) not in used]
            pairs = [(a,b) for a,b in itertools.combinations(options,2) if a["event"] != b["event"] and a["round"] == b["round"]]
            if not pairs:
                continue
            def score(pair):
                a,b = pair
                probability = a["p"]*b["p"]
                return probability if mode == "probability" else probability*payout([a["o"],b["o"]])/STAKE-1
            a,b = min(pairs,key=lambda pair:(-score(pair),tuple(sorted(r["id"] for r in pair))))
            append("bet", ticket=f"{account}:{now.date()}", account=account, month=month,
                   stake=STAKE, source_sha256=digest, generated_at=doc["generated_at"],
                   legs=[identity(a),identity(b)],
                   expected_roi_independence_assumption=a["p"]*b["p"]*payout([a["o"],b["o"]])/STAKE-1)
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
    summaries = {f"{m}-weekly{w}":ticket_summary(events,f"{m}-weekly{w}")
                 for w in (2,3) for m in ("probability","ev_rank")}
    # Notification retries are durable: acknowledgement is appended only after successful delivery.
    acknowledged = next((e["through"] for e in reversed(events) if e["type"] == "notification"),0)
    notify = any(e["type"] in ("source","settlement","attention") for e in events[acknowledged:])
    if notify:
        issues = api(root+"/issues?state=open&per_page=100")
        issue = next((i for i in issues if i["title"] == TITLE and "pull_request" not in i),None)
        source_event = next(e for e in reversed(events) if e["type"] == "source")
        source = source_event["status"]
        body = "실제 베팅 없는 전향 모의 운용입니다. 각 비교 계좌 월 손실 한도 5만원(미정산 포함).\n\n"
        body += "데이터 상태: "+source+" · 관측 "+now.isoformat()+"\n"
        body += "원본 생성 시각: "+str(source_event.get("generated_at"))+"\n\n"
        for name,s in summaries.items():
            roi = "미정의" if s["roi"] is None else f'{s["roi"]:.1%}'
            body += f'- {name}: 정산 {s["settled"]}건, 손익 {s["net"]:,}원, ROI {roi}, 미정산 {s["pending_stake"]:,}원, 최대 낙폭 {s["max_drawdown"]:,}원\n'
        body += "\n검증된 추천은 0개입니다. stale/unavailable이면 새 선택을 보류합니다. 무베팅 0원은 수익성 개선이 아닙니다."
        if any(e["type"] == "attention" for e in events[acknowledged:]):
            body += "\n72시간 이상 정확한 사전 revision의 공식 정산을 확인하지 못한 조합이 있습니다. 미정산 금액은 한도에서 계속 차감합니다."
        if issue: api(root+f'/issues/{issue["number"]}/comments',"POST",{"body":body})
        else: api(root+"/issues","POST",{"title":TITLE,"body":body})
        ack = dict(type="notification",protocol=PROTOCOL,at=now.isoformat(),through=len(events))
        latest = api(root+"/contents/"+FILE+"?ref="+BRANCH)
        current = json.loads(base64.b64decode(latest["content"]))
        if current != events: raise RuntimeError("State changed before notification acknowledgement")
        api(root+"/contents/"+FILE,"PUT",{"message":"paper: acknowledge report","branch":BRANCH,"sha":latest["sha"],
            "content":base64.b64encode(json.dumps(events+[ack],ensure_ascii=False,indent=2).encode()).decode()})
    print(json.dumps({"new_events":len(added),"accounts":summaries},ensure_ascii=False))

if __name__ == "__main__":
    main()
