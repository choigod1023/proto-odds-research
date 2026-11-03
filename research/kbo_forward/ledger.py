"""Append-only, directly collected KBO comparison; never a wagering ledger."""
import collections,copy,hashlib,importlib.util,json,math,os,sys
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OLD=ROOT/"experiments/kbo-m0-followup-20261103"
sys.path.insert(0,str(OLD))
spec=importlib.util.spec_from_file_location("frozen_kbo_forward",OLD/"forward.py")
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
MODELS=("baseline","equal_ensemble")
SIDES=("home","draw","away")
POLICY="direct-public-source-local-receipt-v1"

def canonical(x):return f.canonical(x)
def digest(x):return f.digest(x)
def utcnow():return datetime.now(timezone.utc)
def moment(x):return f.moment(x)
def contract_sha():return digest((Path(__file__).parent/"contract.json").read_bytes())

def price_terms(market):
    kind=market["kind"]
    if kind not in ("full_game_2way","full_game_3way"):raise ValueError("only full-game moneyline products")
    keys={"home","away"} if kind=="full_game_2way" else set(SIDES)
    rule="draw_void_refund" if kind=="full_game_2way" else "draw_is_outcome"
    if market["settlement_rule"]!=rule or set(market["odds"])!=keys or not market["market_id"]:raise ValueError("product/settlement/complete odds mismatch")
    if any(not Decimal(str(v)).is_finite() or Decimal(str(v))<=1 for v in market["odds"].values()):raise ValueError("invalid odds")
    return kind

def validate_model_snapshot(payload,now,unpriced=False):
    # The pinned validator covers row identity, lineup, lineage, receipt timing,
    # last10, and raw-to-scalar equivalence. This view validates MODEL INPUT ONLY.
    # Product terms are validated separately and the original product is stored.
    if not unpriced:price_terms(payload["market"])
    view=copy.deepcopy(payload)
    view["market"]={"kind":"full_game_2way","settlement_rule":"draw_void_refund","market_id":"MODEL_INPUT_VALIDATION_ONLY","odds":{k:payload["market"]["odds"][k] for k in ("home","away")} if not unpriced else {"home":"1.01","away":"1.01"}}
    if unpriced:
        if set(payload["evidence"])!={"announcement","history"}:raise ValueError("unpriced input requires real announcement/history receipts only")
        view["evidence"]["odds"]=copy.deepcopy(payload["evidence"]["history"])
    return f.validate_snapshot(view,now)

def read(path):return f.read_ledger(path)
def append(path,make):return f.append_locked(path,make)

def capture(payload,path,clock=utcnow,infer=f.forecast):
    first=clock();kickoff=validate_model_snapshot(payload,first)
    if first-moment(payload["evidence"]["odds"]["received_at"])>timedelta(minutes=10):raise ValueError("price receipt stale at capture")
    predictions,hashes=infer(payload["row"])
    if set(predictions)!=set(MODELS):raise ValueError("paired models required")
    for p in predictions.values():
        q=p["probabilities_3way"];two=p["probabilities_2way"]
        if set(q)!=set(SIDES) or any(not math.isfinite(v) or not 0<=v<=1 for v in q.values()) or abs(sum(q.values())-1)>1e-9:raise ValueError("invalid model probabilities")
        den=q["home"]+q["away"]
        if den<=0 or set(two)!={"home","away"} or any(abs(two[k]-q[k]/den)>1e-9 for k in two):raise ValueError("invalid conditional probabilities")
        if p["fixture_id"]!=payload["row"]["fixture_id"] or p["event_date"]!=payload["row"]["event_date"]:raise ValueError("forecast identity mismatch")
    finished=clock()
    if finished<first:raise ValueError("local clock moved backwards during prediction")
    if finished>kickoff-timedelta(minutes=30):raise ValueError("forecast completed after T-30")
    kind=payload["market"]["kind"]
    key=digest(canonical({"official_game_id":payload["event"]["official_game_id"],"season":payload["event"]["season"],"product":kind}))
    def make(rows):
        if any(r["type"]=="capture" and r["event_key"]==key for r in rows):return None
        return {"type":"capture","event_key":key,"received_at":first.isoformat(),"frozen_at":finished.isoformat(),"input":payload,"input_sha256":digest(canonical(payload)),"models":predictions,"model_sha256":hashes,"contract_sha256":contract_sha(),"source_policy":POLICY,"source_verification":"direct_fetch_parsed_identity_checked_local_clock","actual_execution_verified":False,"eligible_for_paired_paper_comparison":True}
    return append(path,make)

def capture_unpriced(payload,path,clock=utcnow,infer=f.forecast):
    """Separate observed model forecasts: no invented odds and never ROI-eligible."""
    first=clock();kickoff=validate_model_snapshot(payload,first,unpriced=True);ps,hashes=infer(payload["row"]);end=clock()
    if end>kickoff-timedelta(minutes=30):raise ValueError("forecast completed after T-30")
    key=digest(canonical({"official_game_id":payload["event"]["official_game_id"],"season":payload["event"]["season"],"product":"unpriced"}))
    def make(rows):
        if any(r["type"]=="unpriced_forecast" and r["event_key"]==key for r in rows):return None
        return {"type":"unpriced_forecast","event_key":key,"received_at":first.isoformat(),"frozen_at":end.isoformat(),"input":payload,"input_sha256":digest(canonical(payload)),"models":ps,"model_sha256":hashes,"contract_sha256":contract_sha(),"source_policy":POLICY,"eligible_for_paired_paper_comparison":False,"reason":"no verified same-product pregame price"}
    return append(path,make)

def profits(capture,result):
    market=capture["input"]["market"];kind=price_terms(market);h,a=result.get("home_score"),result.get("away_score")
    if result["status"] not in ("final","cancelled"):raise ValueError("result not final")
    if result["status"]=="final" and (type(h)is not int or type(a)is not int or min(h,a)<0):raise ValueError("invalid final score")
    truth="void" if result["status"]=="cancelled" else "draw" if h==a else "home" if h>a else "away"
    out={}
    for name,p in capture["models"].items():
        q=p["probabilities_2way"] if kind=="full_game_2way" else p["probabilities_3way"]
        choice=max((k for k in SIDES if k in q),key=q.get)
        refund=truth=="void" or (kind=="full_game_2way" and truth=="draw")
        net=Decimal(0) if refund else Decimal(str(market["odds"][choice]))-1 if choice==truth else Decimal(-1)
        out[name]={"pick":choice,"truth":truth,"hit":choice==truth,"refund":refund,"net_units":str(net)}
    return out

def settle(result,path,clock=utcnow):
    now=clock()
    def make(rows):
        key=result["event_key"];captures=[r for r in rows if r["type"]=="capture" and r["event_key"]==key]
        if len(captures)!=1:raise ValueError("unique frozen capture missing")
        c=captures[0];event=c["input"]["event"]
        if any(result[k]!=event[k] for k in ("official_game_id","season","home_id","away_id")):raise ValueError("result identity mismatch")
        if result["game_date"]!=c["input"]["row"]["event_date"]:raise ValueError("result date mismatch; explicit postponement review required")
        ev=result["evidence"];received=moment(ev["received_at"])
        from urllib.parse import urlparse
        if urlparse(ev["uri"]).hostname not in ("www.koreabaseball.com","koreabaseball.com") or digest(canonical(ev["body"]))!=ev["sha256"]:raise ValueError("official result evidence mismatch")
        if received>now or received<moment(c["frozen_at"]):raise ValueError("result receipt outside capture/current time")
        if result["status"]=="final" and received<moment(event["kickoff_at"]):raise ValueError("final score before kickoff")
        previous=[r for r in rows if r["event_key"]==key and r["type"] in ("settlement","correction")]
        economic={k:result.get(k) for k in ("official_game_id","season","home_id","away_id","game_date","status","home_score","away_score")}
        if previous and previous[-1]["economic_result"]==economic:return None
        if previous and received<=moment(previous[-1]["result"]["evidence"]["received_at"]):raise ValueError("stale conflicting result receipt")
        return {"type":"correction" if previous else "settlement","event_key":key,"recorded_at":now.isoformat(),"result":result,"economic_result":economic,"supersedes_hash":previous[-1]["hash"] if previous else None,"paper_profits":profits(c,result),"source_policy":POLICY,"actual_execution_verified":False}
    return append(path,make)

def summary(path,now=None):
    rows=read(path);caps={r["event_key"]:r for r in rows if r["type"]=="capture"};done={r["event_key"]:r for r in rows if r["type"] in ("settlement","correction")};groups={}
    for kind in sorted({c["input"]["market"]["kind"] for c in caps.values()}):
        cs={k:c for k,c in caps.items() if c["input"]["market"]["kind"]==kind};ss=[(cs[k],done[k]) for k in cs if k in done];m={}
        for model in MODELS:
            net=Decimal(0);daily=collections.defaultdict(Decimal);monthly=collections.defaultdict(Decimal);hits=refunds=0
            for c,s in ss:
                z=s["paper_profits"][model];v=Decimal(z["net_units"]);net+=v;day=c["input"]["row"]["event_date"];daily[day]+=v;monthly[day[:7]]+=v;hits+=z["hit"];refunds+=z["refund"]
            peak=balance=dd=Decimal(0)
            for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
            m[model]={"settled":len(ss),"hits":hits,"refunds":refunds,"decisive_accuracy":hits/(len(ss)-refunds) if len(ss)>refunds else None,"roi":float(net/len(ss)) if ss else None,"net_units":str(net),"monthly_units":{k:str(v) for k,v in sorted(monthly.items())},"max_daily_close_drawdown_units":str(dd)}
        first=min((moment(c["frozen_at"]) for c in cs.values()),default=None);elapsed=(now or utcnow())-first if first else timedelta(0)
        groups[kind]={"captured":len(cs),"settled":len(ss),"pending":len(cs)-len(ss),"models":m,"formal_review_ready":len(ss)>=200 and elapsed>=timedelta(days=56),"automatic_adoption":False}
    return {"protocol":"kbo-m0-vs-equal-forward-v1","unpriced_paired_forecasts":sum(r["type"]=="unpriced_forecast" for r in rows),"captured":len(caps),"paired_settled":len(done),"corrections":sum(r["type"]=="correction" for r in rows),"groups":groups,"actual_execution_verified":False,"unit_stakes_only":True,"existing_monthly_loss_ledger_modified":False}
