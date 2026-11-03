"""Hash-pinned actual-roster inputs and unchanged M0 interface, no test outcomes."""
import copy,gzip,hashlib,importlib.util,json,math,sys
from datetime import date,timedelta
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent;I=HERE/"inputs"
CONTRACT_SHA="ced8a96f42425cac0f532fe5e3b4f21252e570bc32d30941be0494b0c95a9ba6"
spec=importlib.util.spec_from_file_location("m0_evolution_legacy",HERE/"vendor/research.py");legacy=importlib.util.module_from_spec(spec);sys.modules[spec.name]=legacy;spec.loader.exec_module(legacy)
FIELDS=("fixture_id","event_date","season","home_id","away_id","features","roster")

def sha(raw):return hashlib.sha256(raw).hexdigest()
def unique(rows):
 out={x["fixture_id"]:x for x in rows}
 if len(out)!=len(rows):raise ValueError("duplicate fixture")
 return out
def rows(name):return [json.loads(x) for x in gzip.decompress((I/name).read_bytes()).splitlines()]
def normalize(row):return {k:copy.deepcopy(row[k]) for k in FIELDS}
def verify():
 m=json.loads((HERE/"manifest.json").read_bytes())
 if sha((ROOT/"contract.json").read_bytes())!=CONTRACT_SHA or m["contract_sha256"]!=CONTRACT_SHA:raise ValueError("contract bytes changed")
 for path,wanted in m["files"].items():
  if sha((HERE/path).read_bytes())!=wanted:raise ValueError("immutable input/vendor changed: "+path)
 if m["test_labels_or_prices_materialized"] is not False:raise ValueError("test outcomes materialized")
 return m

def load():
 verify();train=rows("train-actual.jsonl.gz");actual=rows("test-actual.jsonl.gz");projected=rows("test-projected.jsonl.gz");labels=unique(rows("development-labels.jsonl.gz"));known={k:date.fromisoformat(v) for k,v in json.loads((I/"source-dates.json").read_bytes()).items()}
 if len(train)!=2182 or len(actual)!=709 or len(projected)!=709 or set(labels)!=set(unique(train)):raise ValueError("wrong fixed cohort")
 if set(unique(actual))!=set(unique(projected)) or set(labels)&set(unique(actual)):raise ValueError("overlap or unequal target cohort")
 for group,items in (("train",train),("actual",actual),("projected",projected)):
  for r in items:
   fid=r["fixture_id"];day=date.fromisoformat(r["event_date"])
   if known[fid]!=day or day.year!=r["season"] or (group=="train")!=(r["season"]<2026):raise ValueError("date/split mismatch")
   if r["historical_receipt_proven"] is not False or r["availability_tier"]!="retrospective_unproven":raise ValueError("source availability promoted")
   legacy.adapter.check_nested(r["raw_features"],fid,day,known)
   for name,x in r["raw_features"].items():
    if legacy.adapter.scalar(name,x["value"])!=r["features"][name]["value"]:raise ValueError("scalar changed")
   for side in ("home","away"):
    z=r["roster"][side];v=r["raw_features"][side+"_lineup_strength"]["value"];s=r["raw_features"][side+"_starter_strength"]["value"]
    if group!="projected" and (not z["starter"] or len(z["hitters"])!=9 or len(set(z["hitters"]))!=9):raise ValueError("actual roster identity")
    if v is not None and [x["player_id"] for x in v]!=z["hitters"]:raise ValueError("hitter history owner")
    if s is not None and s["projected_player_id"]!=z["starter"]:raise ValueError("pitcher history owner")
 for fid,y in labels.items():
  if fid not in labels or y["status"]!="final" or any(type(y[k]) is not int or y[k]<0 for k in ("home_score","away_score")):raise ValueError("invalid development result")
 out={"train":sorted(train,key=lambda x:(x["event_date"],x["fixture_id"])),"test_actual":sorted(actual,key=lambda x:(x["event_date"],x["fixture_id"])),"test_projected":sorted(projected,key=lambda x:(x["event_date"],x["fixture_id"])),"labels":labels}
 return out

def development(data):
 val=[r for r in data["train"] if r["season"]==2025];cut=date.fromisoformat(val[0]["event_date"])-timedelta(days=7);train=[r for r in data["train"] if r["season"] in (2023,2024) and date.fromisoformat(r["event_date"])<=cut]
 if len(train)!=1451 or len(val)!=731:raise ValueError("development population changed")
 return train,val

def final_training(data):
 cut=date.fromisoformat(data["test_actual"][0]["event_date"])-timedelta(days=7);train=[r for r in data["train"] if date.fromisoformat(r["event_date"])<=cut]
 if len(train)!=2182:raise ValueError("final training population changed")
 return train

def fit_baseline(train,labels):
 f=unique(train);ys={fid:labels[fid] for fid in f};return legacy.fit([normalize(r) for r in train],ys,"M0",10.)
def predict_baseline(model,items):return legacy.predict(model,[normalize(r) for r in items])
def baseline_reference():return json.loads((I/"baseline-reference.json").read_bytes())
def metrics(predictions,labels):
 out=legacy.metrics(predictions,labels);decisive=hits=0;loss=brier=0.
 for p in predictions:
  y=labels[p["fixture_id"]]
  if y["home_score"]==y["away_score"]:continue
  truth="home" if y["home_score"]>y["away_score"] else "away";q=p["probabilities_2way"];choice="away" if q["away"]>q["home"] else "home";decisive+=1;hits+=truth==choice;loss-=math.log(max(q[truth],1e-15));brier+=sum((q[k]-(k==truth))**2 for k in ("home","away"))
 out.update(decisive=decisive,decisive_hits=hits,decisive_accuracy=hits/decisive,decisive_logloss=loss/decisive,decisive_brier=brier/decisive)
 return out

def choose(predictions,labels):
 if "baseline" not in predictions:raise ValueError("baseline missing")
 ids=set(unique(predictions["baseline"]));scores={}
 for key,rows_ in predictions.items():
  if set(unique(rows_))!=ids:raise ValueError("selection populations differ")
  if any(not str(labels[fid].get("assumed_completed_date","")).startswith("2025") for fid in ids):raise ValueError("selection restricted to2025")
  scores[key]=metrics(rows_,labels)
 order=sorted(scores,key=lambda k:(-scores[k]["decisive_accuracy"],scores[k]["decisive_logloss"],k!="baseline",k))
 return {"selected":order[0],"ranking":order,"metrics":scores,"used2026_outcomes":False}

def dump(path,obj):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 with path.open("x",encoding="utf8",newline="\n") as f:json.dump(obj,f,sort_keys=True,indent=2,ensure_ascii=False,allow_nan=False);f.write("\n")
