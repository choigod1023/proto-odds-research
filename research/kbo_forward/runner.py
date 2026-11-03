"""Bounded direct-source observer. No betting, refit, production DB or services."""
import argparse,copy,gzip,hashlib,json,os,sys,traceback
from datetime import datetime,timezone,timedelta
from pathlib import Path

sys.path.insert(0,str(Path(__file__).parent))
import ledger
from locking import single_writer

KST=timezone(timedelta(hours=9))
DEFAULT_STATE=Path.home()/"Documents/ChatGPT/sportstoto-research-state/kbo-m0-forward-v1"

def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("w",encoding="utf8",newline="\n") as f:json.dump(data,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);f.write("\n");f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)

def event(game):
    g=game["source"];start=datetime.strptime(g["G_DT"]+" "+g["G_TM"],"%Y%m%d %H:%M").replace(tzinfo=KST)
    return {"official_game_id":g["G_ID"],"season":int(g["SEASON_ID"]),"home_id":g["HOME_ID"],"away_id":g["AWAY_ID"],"home_name":g["HOME_NM"],"away_name":g["AWAY_NM"],"kickoff_at":start.isoformat()}

def evidence(client,receipt):
    raw=(client.root/receipt["body_file"]).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=receipt["sha256"]:raise ValueError("direct official evidence corrupted")
    body=json.loads(raw)
    return {"uri":receipt["url"],"received_at":receipt["finished_at"],"body":body,"sha256":ledger.digest(ledger.canonical(body)),"raw_sha256":receipt["sha256"]}

def price_packet(quote,state,official_event):
    """Wrap verified Betman row terms; retain exact bytes and canonical body digest."""
    from prices import DETAIL_URL,parse_detail
    source=quote["evidence"]
    if source["uri"]!=DETAIL_URL or source["received_at"]!=quote["received_at"]:raise ValueError("unapproved price source or receipt differs")
    body_path=Path(source["body_file"]).resolve()
    allowed=(Path(state)/"prices/price-evidence").resolve()
    if not body_path.is_relative_to(allowed):raise ValueError("price evidence outside collector state")
    raw=body_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=source["sha256"]:raise ValueError("price raw bytes changed")
    body=json.loads(raw)
    listing_path=allowed/(source["round_listing_sha256"]+".body")
    listing_raw=listing_path.read_bytes()
    if hashlib.sha256(listing_raw).hexdigest()!=source["round_listing_sha256"]:raise ValueError("listing evidence changed")
    parts=quote["market_id"].split(":")
    rounds=[r for r in json.loads(listing_raw)["protoGames"] if r.get("gmId")=="G101" and str(r.get("gmTs"))==parts[1]]
    if len(parts)!=3 or len(rounds)!=1:raise ValueError("price round not in verified listing")
    parsed,_=parse_detail(raw,rounds[0],[official_event],source["received_at"],source)
    matches=[q for q in parsed if q["market_id"]==quote["market_id"]]
    if len(matches)!=1 or any(matches[0][k]!=quote[k] for k in ("official_game_id","home_id","away_id","kickoff_at","kind","settlement_rule","odds")):raise ValueError("normalized quote differs from raw official row")
    market={k:quote[k] for k in ("kind","settlement_rule","market_id","odds")}
    market.update({k:quote[k] for k in ("close_at","round_close_at","source_product") if k in quote})
    receipt={"uri":source["uri"],"received_at":source["received_at"],"body":body,"sha256":ledger.digest(ledger.canonical(body)),"raw_sha256":source["sha256"],"round_listing_sha256":source.get("round_listing_sha256")}
    return market,receipt

def packet(snapshot):
    if not snapshot.get("source_verified"):raise ValueError("source snapshot not verified")
    x=snapshot.get("payload") or snapshot
    if not all(k in x for k in ("event","row","announcement","evidence")):raise ValueError("official adapter missing canonical model payload")
    if set(x["row"].get("history_coverage",{}))!={"home","away"} or any(c.get("status")!="complete" for c in x["row"]["history_coverage"].values()):raise ValueError("verified history coverage incomplete")
    return {k:copy.deepcopy(x[k]) for k in ("event","row","announcement","evidence")}

def run_once(state,official=None,price_fn=None,clock=ledger.utcnow,infer=ledger.infer_frozen):
    state=Path(state);state.mkdir(parents=True,exist_ok=True)
    with single_writer(state/"observer.lock") as owned:
        if not owned:return {"status":"another_run_active"}
        started=clock();path=state/"paired-ledger.jsonl";rows=ledger.read(path);captures=[r for r in rows if r["type"]=="capture"]
        previous=json.loads((state/"last-run.json").read_bytes()) if (state/"last-run.json").exists() else {}
        if official is None:
            from official import OfficialClient
            official=OfficialClient(state/"official",max_requests=80)
        if price_fn is None:
            from prices import collect_prices
            price_fn=collect_prices
        report={"started_at":started.isoformat(),"status":"ok","captured_this_run":0,"unpriced_this_run":0,"settled_this_run":0,"excluded":[],"requests":0,"scheduler_scope":"separate local task; PC must be awake/online"}
        try:
            today=started.astimezone(KST).date();dates={today.isoformat(),(today+timedelta(days=1)).isoformat()}
            # Fair bounded reconciliation: never run an unbounded historical crawl.
            done={r["event_key"] for r in rows if r["type"] in ("settlement","correction")}
            waiting=sorted({c["input"]["row"]["event_date"] for c in captures if c["event_key"] not in done or c["input"]["row"]["event_date"]>=(today-timedelta(days=7)).isoformat()})
            cursor=int(previous.get("settlement_cursor",0));take=(waiting[cursor:]+waiting[:cursor])[:5] if waiting else []
            dates.update(take);report["settlement_cursor"]=(cursor+len(take))%len(waiting) if waiting else 0
            schedule=official.schedule(sorted(dates));games=schedule["games"];byid={}
            for g in games:
                e=event(g);key=(e["season"],e["official_game_id"])
                if key in byid:raise ValueError("duplicate official event identity")
                byid[key]=g
            candidates=[]
            for g in games:
                e=event(g);delta=ledger.moment(e["kickoff_at"])-clock()
                if timedelta(minutes=30)<=delta<=timedelta(hours=3) and str(g["source"].get("GAME_STATE_SC"))=="1" and str(g["source"].get("CANCEL_SC_ID"))=="0":candidates.append(g)
            price_result=price_fn(state/"prices",[event(g) for g in candidates]) if candidates else {"quotes":[],"excluded":[],"requests":[]}
            report["price_excluded"]=price_result["excluded"];report["price_request_count"]=len(price_result["requests"])
            if price_result["requests"]:
                with (state/"price-request-receipts.jsonl").open("ab") as sink:
                    for rec in price_result["requests"]:sink.write(ledger.canonical(rec)+b"\n")
                    sink.flush();os.fsync(sink.fileno())
            candidates.sort(key=lambda g:(event(g)["kickoff_at"],event(g)["official_game_id"]))
            candidate_cursor=int(previous.get("candidate_cursor",0))%len(candidates) if candidates else 0
            selected=(candidates[candidate_cursor:]+candidates[:candidate_cursor])[:5]
            report["candidate_cursor"]=(candidate_cursor+len(selected))%len(candidates) if candidates else 0
            for g in selected:
                try:
                    official_id=g["source"]["G_ID"]
                    current_quotes=[q for q in price_result["quotes"] if q["official_game_id"]==official_id]
                    if not current_quotes and any(r["type"]=="unpriced_forecast" and r["input"]["event"]["official_game_id"]==official_id for r in rows):continue
                    if current_quotes and all(any(r["type"]=="capture" and r["input"]["event"]["official_game_id"]==official_id and r["input"]["market"]["kind"]==q["kind"] for r in rows) for q in current_quotes):continue
                    s=official.snapshot(g)
                    if not s.get("source_verified"):
                        report["excluded"].append({"official_game_id":g["source"]["G_ID"],"reasons":s.get("missing",["unverified_source"])});continue
                    x=packet(s);x["source_game"]=g
                    qs=[q for q in price_result["quotes"] if q["official_game_id"]==x["event"]["official_game_id"] and q.get("season",x["event"]["season"])==x["event"]["season"]]
                    if not qs:
                        x["evidence"].pop("odds",None)
                        rec=ledger.capture_unpriced(x,path,clock=clock,infer=infer);report["unpriced_this_run"]+=int(rec is not None)
                    for q in qs:
                        if q["home_id"]!=x["event"]["home_id"] or q["away_id"]!=x["event"]["away_id"] or ledger.moment(q["kickoff_at"])!=ledger.moment(x["event"]["kickoff_at"]):raise ValueError("price event metadata mismatch")
                        priced=copy.deepcopy(x);priced["market"],priced["evidence"]["odds"]=price_packet(q,state,x["event"])
                        rec=ledger.capture(priced,path,clock=clock,infer=infer);report["captured_this_run"]+=int(rec is not None)
                except Exception as exc:report["excluded"].append({"official_game_id":g["source"]["G_ID"],"error_type":type(exc).__name__,"reason":str(exc)[:300]})
            # Re-read captures so successful inference is never lost on later failures.
            for c in [r for r in ledger.read(path) if r["type"]=="capture"]:
                e=c["input"]["event"];g=byid.get((e["season"],e["official_game_id"]))
                if g is None:continue
                try:
                    z=official.result(g)
                    if not z.get("verified"):continue
                    current=z["game"];r={"event_key":c["event_key"],**{k:e[k] for k in ("official_game_id","season","home_id","away_id")},"game_date":datetime.strptime(current["source"]["G_DT"],"%Y%m%d").date().isoformat(),"status":z.get("status","final"),"home_score":z.get("home_score"),"away_score":z.get("away_score"),"evidence":evidence(official,current["receipt"])}
                    out=ledger.settle(r,path,clock=clock);report["settled_this_run"]+=int(out is not None)
                except Exception as exc:report["excluded"].append({"official_game_id":e["official_game_id"],"result_error_type":type(exc).__name__,"reason":str(exc)[:300]})
            report["requests"]=getattr(official,"count",0)
        except Exception as exc:
            report.update(status="failed",error_type=type(exc).__name__,reason=str(exc)[:300]);report["requests"]=getattr(official,"count",0)
        finally:
            report["finished_at"]=clock().isoformat();report["comparison"]=ledger.summary(path,now=clock())
            atomic_json(state/"summary.json",report["comparison"]);atomic_json(state/"last-run.json",report)
            # A digest-addressed history retains changes; identical repeated skips are quiet.
            meaningful={k:report[k] for k in ("status","excluded","comparison")};key=ledger.digest(ledger.canonical(meaningful))
            if key!=previous.get("notification_digest"):
                atomic_json(state/"reports"/(key+".json"),report)
            report["notification_digest"]=key;atomic_json(state/"last-run.json",report)
        return report

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--state",type=Path,default=DEFAULT_STATE);ap.add_argument("--status",action="store_true");args=ap.parse_args()
    if args.status:
        print(json.dumps(ledger.summary(args.state/"paired-ledger.jsonl"),ensure_ascii=False));return
    result=run_once(args.state)
    print(json.dumps({k:result.get(k) for k in ("status","captured_this_run","unpriced_this_run","settled_this_run","requests")},ensure_ascii=False))
    if result["status"]=="failed":sys.exit(1)
if __name__=="__main__":main()
