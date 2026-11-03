"""Local staged prospective capture; source adapters are not yet certified/wired."""
import argparse,copy,gzip,hashlib,importlib.util,json,math,os,sys
from datetime import datetime,timezone,timedelta,date
from pathlib import Path
from urllib.parse import urlparse
P=Path(__file__).resolve().parent;V=P/"vendor/evolution";sys.path.insert(0,str(V/"common"));import core
SOURCE_PREFIX="experiments/kbo-m0-evolution-20261103/"
def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf8")
def digest(x):return hashlib.sha256(x).hexdigest()
def utcnow():return datetime.now(timezone.utc)
def moment(s):
 d=datetime.fromisoformat(s)
 if d.tzinfo is None or d.utcoffset() is None:raise ValueError("explicit timezone required")
 return d.astimezone(timezone.utc)
def load_module(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

def load_models():
 manifest=json.loads((P/"source-manifest.json").read_bytes())
 for path,wanted in manifest["source_files"].items():
  if digest((V/path.removeprefix(SOURCE_PREFIX)).read_bytes())!=wanted:raise ValueError("frozen source changed")
 core.verify();ref=core.baseline_reference();shrink=json.loads((V/"recency/final/model.json").read_bytes());dist=json.loads(gzip.decompress((V/"distribution/artifacts/final-predictions.json.gz").read_bytes()));rec=load_module("followup_shrink",V/"recency/candidate.py");nb=load_module("followup_nb",V/"distribution/candidate.py")
 if dist["selected"]!="negative_binomial_alpha03":raise ValueError("fixed NB recipe changed")
 hashes={"baseline":digest(canonical(ref["final_model"])),"shrink":digest(canonical(shrink)),"nb03":digest(canonical(dist["selected_model"]))}
 return ref["final_model"],shrink,dist["selected_model"],rec,nb,hashes

def forecast(row):
 baseline,shrink,dist,rec,nb,hashes=load_models();ps=[core.predict_baseline(baseline,[row])[0],rec.predict(shrink,[row])[0],nb.predict(dist,[row])[0]];q={k:sum(x["probabilities_3way"][k] for x in ps)/3 for k in ("home","draw","away")};den=q["home"]+q["away"];e={"fixture_id":row["fixture_id"],"event_date":row["event_date"],"probabilities_3way":q,"probabilities_2way":{k:q[k]/den for k in ("home","away")}}
 return {"baseline":ps[0],"equal_ensemble":e},hashes

def validate_snapshot(x,now):
 if now.tzinfo is None:raise ValueError("clock must include timezone")
 event=x["event"];market=x["market"];row=x["row"];kickoff=moment(event["kickoff_at"]);localday=datetime.fromisoformat(event["kickoff_at"]).astimezone(timezone(timedelta(hours=9))).date()
 if now.astimezone(timezone.utc)>kickoff-timedelta(minutes=30):raise ValueError("first capture is later than T-30; retrospective backfill rejected")
 if not event["official_game_id"] or row["fixture_id"]!=event["official_game_id"]+str(event["season"]) or row["season"]!=event["season"] or row["event_date"]!=localday.isoformat() or event["season"]!=localday.year:raise ValueError("official identity/date mismatch")
 if (row["home_id"],row["away_id"])!=(event["home_id"],event["away_id"]) or row["home_id"]==row["away_id"]:raise ValueError("event teams mismatch")
 if market["kind"]!="full_game_2way" or market["settlement_rule"]!="draw_void_refund" or not market["market_id"] or set(market["odds"])!={"home","away"}:raise ValueError("same full-game two-way product required")
 from decimal import Decimal
 if any(not Decimal(str(v)).is_finite() or Decimal(str(v))<=1 for v in market["odds"].values()):raise ValueError("invalid two-sided odds")
 expected=set(core.legacy.adapter.SOURCE)
 if set(row["features"])!=expected or set(row["raw_features"])!=expected:raise ValueError("frozen feature set required")
 announcement=x["announcement"]
 if announcement["lineup_confirmed"] is not True or announcement["pitcher_announcement_confirmed"] is not True or announcement["basis"]!="official_confirmed_pregame_announcement":raise ValueError("confirmed pregame announcement required; probable/recent/postgame roster not allowed")
 if row["roster"]!=announcement["roster"]:raise ValueError("announced roster and model row differ")
 for side in ("home","away"):
  z=row["roster"][side]
  if len(z["hitters"])!=9 or len(set(z["hitters"]))!=9 or not all(isinstance(v,str) and v for v in z["hitters"]) or not z["starter"]:raise ValueError("ordered nine and announced starter required")
 if set(row["roster"]["home"]["hitters"])&set(row["roster"]["away"]["hitters"]) or row["roster"]["home"]["starter"]==row["roster"]["away"]["starter"]:raise ValueError("opposing player identity collision")
 def past(obj):
  if isinstance(obj,dict):
   for key,value in obj.items():
    if key in ("date","latest_source_date") and value is not None and date.fromisoformat(value)>=localday:raise ValueError("same-day/future history rejected")
    if key in ("fixture_id","id") and value in (row["fixture_id"],event["official_game_id"]):raise ValueError("target-game performance rejected")
    if key=="source_fixture_ids" and any(v in (row["fixture_id"],event["official_game_id"]) for v in value):raise ValueError("target-game lineage rejected")
    past(value)
  elif isinstance(obj,list):
   for value in obj:past(value)
 past(row["raw_features"])
 for key,entry in row["raw_features"].items():
  if core.legacy.adapter.scalar(key,entry["value"])!=row["features"][key]["value"]:raise ValueError("raw/scalar feature mismatch")
 for side in ("home","away"):
  a=row["raw_features"][side+"_lineup_strength"]["value"];b=row["raw_features"][side+"_starter_strength"]["value"]
  if a is not None and [z["player_id"] for z in a]!=row["roster"][side]["hitters"]:raise ValueError("batting history owner mismatch")
  if a is not None and any(len(z.get("history") or [])>10 for z in a):raise ValueError("player history exceeds frozen last10 recipe")
  if b is not None and (b["projected_player_id"]!=row["roster"][side]["starter"] or len(b["appearances"])>10):raise ValueError("pitcher history owner/window mismatch")
 evidence=x["evidence"]
 if set(evidence)!={"announcement","odds","history"}:raise ValueError("three source receipt bundles required")
 for kind,z in evidence.items():
  uri=urlparse(z["uri"])
  if uri.scheme!="https" or not uri.hostname or not z["body"]:raise ValueError("source URL and body required")
  received=moment(z["received_at"])
  if received>now.astimezone(timezone.utc) or received>kickoff-timedelta(minutes=30):raise ValueError("source receipt is future/late")
  if kind=="announcement" and uri.hostname not in ("www.koreabaseball.com","koreabaseball.com"):raise ValueError("official announcement source URL required")
  if digest(canonical(z["body"]))!=z["sha256"]:raise ValueError("source body digest mismatch")
 return kickoff

def read_ledger(path):
 path=Path(path)
 if not path.exists():return []
 previous="0"*64;rows=[]
 raw=path.read_bytes()
 if raw and not raw.endswith(b"\n"):raise ValueError("incomplete ledger; do not silently truncate")
 for line in raw.splitlines():
  r=json.loads(line);claimed=r.pop("hash")
  if r["previous_hash"]!=previous or digest(canonical(r))!=claimed:raise ValueError("ledger chain differs")
  r["hash"]=claimed;previous=claimed;rows.append(r)
 return rows

def append_locked(path,make):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);lock=path.with_suffix(path.suffix+".lock");fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
 try:
  os.close(fd);rows=read_ledger(path);record=make(rows)
  if record is None:return None
  record["previous_hash"]=rows[-1]["hash"] if rows else "0"*64;record["hash"]=digest(canonical(record));out=canonical(record)+b"\n"
  outfd=os.open(path,os.O_CREAT|os.O_APPEND|os.O_WRONLY)
  try:
   n=os.write(outfd,out)
   if n!=len(out):raise OSError("incomplete append; ledger quarantined until review")
   os.fsync(outfd)
  finally:os.close(outfd)
  return record
 finally:lock.unlink()

def capture(payload,ledger,clock=utcnow,infer=forecast):
 first=clock();kickoff=validate_snapshot(payload,first);predictions,models=infer(payload["row"]);finished=clock()
 if finished>kickoff-timedelta(minutes=30):raise ValueError("forecast completed later than T-30")
 key=digest(canonical({"season":payload["event"]["season"],"official_game_id":payload["event"]["official_game_id"],"product":"full_game_2way"}))
 def make(records):
  if any(r["type"]=="capture" and r["event_key"]==key for r in records):raise ValueError("first captured opportunity already frozen; no overwrite/repricing")
  return {"type":"capture","event_key":key,"received_at":first.isoformat(),"frozen_at":finished.isoformat(),"event":payload["event"],"market":payload["market"],"input_sha256":digest(canonical(payload)),"input":payload,"model_sha256":models,"predictions":predictions,"source_verification":"pending_independent_source_adapter","eligible_for_effectiveness_validation":False,"local_receipt_proves_input_timing_not_source_authenticity":True}
 return append_locked(ledger,make)

def settle(payload,ledger,clock=utcnow):
 now=clock()
 def make(records):
  captures={r["event_key"]:r for r in records if r["type"]=="capture"};key=payload["event_key"]
  if key not in captures or any(r["type"]=="settlement" and r["event_key"]==key for r in records):raise ValueError("missing frozen capture or conflicting/repeated result")
  original=captures[key]
  if now<moment(original["event"]["kickoff_at"]) or moment(payload["evidence"]["received_at"])>now:raise ValueError("result observed before kickoff or future receipt")
  ev=payload["evidence"]
  uri=urlparse(ev["uri"])
  if uri.scheme!="https" or uri.hostname not in ("www.koreabaseball.com","koreabaseball.com") or digest(canonical(ev["body"]))!=ev["sha256"]:raise ValueError("official result source evidence missing/differs")
  if payload["official_game_id"]!=original["event"]["official_game_id"] or payload["status"] not in ("final","cancelled"):raise ValueError("official result identity/status mismatch")
  if any(payload[k]!=original["event"][k] for k in ("season","home_id","away_id")):raise ValueError("result event metadata mismatch")
  game_day=datetime.fromisoformat(original["event"]["kickoff_at"]).astimezone(timezone(timedelta(hours=9))).date().isoformat()
  if payload["game_date"]!=game_day:raise ValueError("result date differs; explicit identity review required")
  if payload["status"]=="final" and moment(ev["received_at"])<moment(original["event"]["kickoff_at"]):raise ValueError("final result receipt before kickoff")
  h,a=payload.get("home_score"),payload.get("away_score")
  if payload["status"]=="final" and (type(h) is not int or type(a) is not int or min(h,a)<0):raise ValueError("invalid official final scores")
  truth="void" if payload["status"]=="cancelled" else "draw" if h==a else "home" if h>a else "away";profits={}
  from decimal import Decimal
  for name,p in original["predictions"].items():
   q=p["probabilities_2way"];pick="away" if q["away"]>q["home"] else "home";z=Decimal(0) if truth in ("draw","void") else Decimal(str(original["market"]["odds"][pick]))-1 if pick==truth else Decimal(-1);profits[name]={"pick":pick,"net_units":str(z),"refund":truth in ("draw","void")}
  return {"type":"settlement","event_key":key,"received_at":now.isoformat(),"result":payload,"paper_profits":profits,"eligible_for_effectiveness_validation":False,"source_verification":"pending_independent_source_adapter"}
 return append_locked(ledger,make)

def status(ledger):
 rows=read_ledger(ledger);captures=[r for r in rows if r["type"]=="capture"];settled=[r for r in rows if r["type"]=="settlement"]
 return {"local_staged_captures":len(captures),"local_staged_settlements":len(settled),"source_verified_paired_settlements":0,"formal_review_ready":False,"reason":"No trusted source adapter is deployed/wired; local receipts/hash chain do not certify official facts or actual execution.","schedule_installed":False,"model_adoption":False}
def main():
 p=argparse.ArgumentParser();p.add_argument("command",choices=("capture","settle","status"));p.add_argument("--input");p.add_argument("--ledger",default=str(P/"prospective/staging-ledger.jsonl"));a=p.parse_args()
 if a.command=="status":print(json.dumps(status(a.ledger),ensure_ascii=False));return
 if not a.input:p.error("--input is required")
 payload=json.loads(Path(a.input).read_bytes());r=capture(payload,a.ledger) if a.command=="capture" else settle(payload,a.ledger);print(json.dumps({"hash":r["hash"],"event_key":r["event_key"],"type":r["type"],"eligible_for_effectiveness_validation":r["eligible_for_effectiveness_validation"]}))
if __name__=="__main__":main()
