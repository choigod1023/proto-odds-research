"""Locked-recipe cross-period diagnostic; no new parameter/period selection."""
import argparse,gzip,hashlib,json,math,subprocess,sys
from pathlib import Path
P=Path(__file__).resolve().parent;REPO=P.parents[1];BASE="experiments/kbo-m0-followup-20261103/";sys.path.insert(0,str(P/"vendor/evolution/common"));import core
import metrics,outcome_metrics
SOURCES={}
REPLAY_MANIFEST_SHA256="c9cc637a6e2f5b99441e1184d0cf475ef8b609064c0ec5b0184e79887212d5ec"

def fixed(rev,path,repo=REPO):
 # Original local-only commits are absent in a fresh CI checkout. Preserve
 # their exact blobs, not replacements from the current tree or new results.
 manifest_raw=(P/"replay-sources.json").read_bytes()
 if hashlib.sha256(manifest_raw).hexdigest()!=REPLAY_MANIFEST_SHA256:raise ValueError("frozen replay manifest changed")
 entry=json.loads(manifest_raw)["sources"].get(rev+":"+path)
 if entry is not None:
  raw=gzip.decompress((P/entry["relative_path"]).read_bytes())
  if len(raw)!=entry["raw_bytes"] or hashlib.sha256(raw).hexdigest()!=entry["raw_sha256"]:raise ValueError("frozen replay blob changed")
 else:raw=subprocess.check_output(["git","-C",str(repo),"show",rev+":"+path])
 SOURCES[rev+":"+path]=hashlib.sha256(raw).hexdigest();body=gzip.decompress(raw) if path.endswith(".gz") else raw;return [json.loads(line) for line in body.splitlines() if line.strip()] if ".jsonl" in path else json.loads(body)
def unique(x):return core.unique(x)
def validate(preds,targets):
 target_map=unique(targets);ids=set(target_map)
 for name,rs in preds.items():
  if set(unique(rs))!=ids:raise ValueError("unequal target predictions")
  for r in rs:
   ref=target_map[r["fixture_id"]]
   if r["event_date"]!=ref["event_date"]:raise ValueError("prediction date mismatch")
   q=r["probabilities_3way"];two=r["probabilities_2way"]
   if set(q)!={"home","draw","away"} or any(not math.isfinite(z) or z<0 or z>1 for z in q.values()) or abs(sum(q.values())-1)>1e-9:raise ValueError("invalid probability simplex")
   den=q["home"]+q["away"]
   if den<=0 or set(two)!={"home","away"} or any(not math.isfinite(two[k]) or abs(two[k]-q[k]/den)>1e-9 for k in two):raise ValueError("conditional probability mismatch")
def average(parts,targets):
 indexes=[unique(p) for p in parts];out=[]
 for target in targets:
  fid=target["fixture_id"];q={k:sum(x[fid]["probabilities_3way"][k] for x in indexes)/3 for k in ("home","draw","away")};den=q["home"]+q["away"];out.append({"fixture_id":fid,"event_date":target["event_date"],"probabilities_3way":q,"probabilities_2way":{k:q[k]/den for k in ("home","away")}})
 return out

def coverage(targets,quotes,labels):
 ids=set(unique(quotes));missing=[r["fixture_id"] for r in targets if r["fixture_id"] not in ids]
 return {"target_n":len(targets),"price_n":len(quotes),"missing_price_n":len(missing),"target_draws":sum(metrics.winner(labels[r["fixture_id"]])=="draw" for r in targets),"price_draws":sum(metrics.winner(labels[f])=="draw" for f in ids),"missing_draws":sum(metrics.winner(labels[f])=="draw" for f in missing),"missing_decisive":sum(metrics.winner(labels[f])!="draw" for f in missing),"excluded_ids":missing}

def assert_replay_equal(expected,actual,path="result"):
 # Hashes, identities, integer counts and Decimal money strings stay exact.
 # Floating metrics may differ by libm/NumPy rounding across platforms only.
 if type(expected) is not type(actual):raise ValueError("replay type mismatch at "+path)
 if isinstance(expected,dict):
  if expected.keys()!=actual.keys():raise ValueError("replay keys mismatch at "+path)
  for key in expected:assert_replay_equal(expected[key],actual[key],path+"/"+str(key))
 elif isinstance(expected,list):
  if len(expected)!=len(actual):raise ValueError("replay length mismatch at "+path)
  for index,(left,right) in enumerate(zip(expected,actual)):assert_replay_equal(left,right,path+"/"+str(index))
 elif isinstance(expected,float):
  if not math.isfinite(expected) or not math.isfinite(actual) or abs(expected-actual)>1e-12:
   raise ValueError(f"replay float mismatch at {path}: {expected!r} != {actual!r}")
 elif expected!=actual:raise ValueError(f"replay exact mismatch at {path}: {expected!r} != {actual!r}")

def summarize(predictions,labels,quotes,targets):
 validate(predictions,targets);qids=set(unique(quotes));tids=set(unique(targets));tmap=unique(targets)
 if not qids<=tids or set(labels)!=tids:raise ValueError("price/label population mismatch")
 for row in quotes:
  fid=row["fixture_id"];t=tmap[fid]
  if row["date"]!=t["event_date"] or (row["home_id"],row["away_id"])!=(t["home_id"],t["away_id"]):raise ValueError("quote event identity mismatch")
 result={"coverage":coverage(targets,quotes,labels),"outcome":{},"price":{},"price_paired_ensemble_minus_baseline":None};records={}
 for name,rs in predictions.items():
  result["outcome"][name]=core.metrics(rs,labels);index=unique(rs);result["price"][name],records[name]=metrics.settle(quotes,{fid:{"q2":r["probabilities_2way"]} for fid,r in index.items()},labels)
 result["outcome_paired_ensemble_minus_baseline"]=outcome_metrics.outcome_pair(predictions["equal_ensemble"],predictions["baseline"],labels)
 if len(quotes)>=30:result["price_paired_ensemble_minus_baseline"]=metrics.paired(records["equal_ensemble"],records["baseline"])
 else:result["price_inference_note"]="fewer than30 settled pairs: descriptive only"
 result["price_roi_delta"]=None if not quotes else result["price"]["equal_ensemble"]["roi"]-result["price"]["baseline"]["roi"]
 return result

def main(verify=False):
 core.verify()
 mf=fixed("0fe05367fc85e71f2df2784c6ddf50fb017bcba2",BASE+"source-manifest.json")
 if hashlib.sha256((P/"contract.json").read_bytes()).hexdigest()!=mf["contract_sha256"]:raise ValueError("followup contract changed")
 history=core.rows("train-actual.jsonl.gz");targets={year:[r for r in history if r["season"]==year] for year in (2024,2025)};targets[2026]=core.rows("test-actual.jsonl.gz")
 rev24="7f06e76dfe77cbb3dd7d9395995133effdb1b66a";pred24=fixed(rev24,BASE+"historical/predictions2024.json")
 if SOURCES[rev24+":"+BASE+"historical/predictions2024.json"]!="2643db9ee19e01de8a31ac13316ad0b2c05366ccf5ffb911b4b8b865a0d4f92b":raise ValueError("unfrozen2024 forecasts")
 old="ed11ca0561e485319f2b951409287f42167f42b5";ob="experiments/kbo-m0-evolution-20261103/";v=fixed(old,ob+"artifacts/selection.json")["baseline_and_nominee_validation_predictions"];pred25={"baseline":v["baseline"],"shrink":v["recency"],"nb03":v["distribution"]};pred25["equal_ensemble"]=average([v[k] for k in ("recency","opponent","distribution")],targets[2025])
 pred26=fixed("627cadca9096acae18a30f72484c342edab7acd3",ob+"artifacts/predictions.json")["predictions"]["actual"];pred26={"baseline":pred26["baseline"],"shrink":pred26["recency"],"nb03":pred26["distribution"],"equal_ensemble":pred26["equal_ensemble"]};predictions={2024:pred24,2025:pred25,2026:pred26}
 datarev="f6681d7525e992ef52e770e50bf6ef2401c6ef42";handoff=fixed(datarev,BASE+"data/handoff.json");labels={};prices={}
 for year in (2024,2025):
  for prefix,dest in (("labels",labels),("prices",prices)):
   name=f"{prefix}-{year}";entry=handoff["files"][name];value=fixed(datarev,entry["path"])
   if SOURCES[datarev+":"+entry["path"]]!=entry["sha256"]:raise ValueError("data Git blob SHA mismatch")
   dest[year]=unique(value) if prefix=="labels" else value
 oldinput="d08ccb708033fb6b067e84e80eae81ea3c9b4cfa";ib="experiments/kbo-player-effects-20261103/inputs/";alllabels=unique(fixed(oldinput,ib+"test-labels.jsonl.gz"));ids={r["fixture_id"] for r in targets[2026]};labels[2026]={f:alllabels[f] for f in ids};tiers26=fixed(oldinput,ib+"price-ledgers.json")["ledgers"];prices[2026]=[r for r in tiers26["extended_day_team_source_confirmed"] if r["fixture_id"] in ids]
 result={"recipe_source_commit":old,"new2024_prediction_commit":rev24,"price_join_commit":datarev,"annual":{},"halves":{},"retrospective_not_new_unseen_holdout":True,"parameters_or_weights_reselected":False,"pooled_cross_year_roi_not_reported":True,"source_execution_proven":False,"prospective_verified_settlements":0,"context2026_reused_not_additional_data":True}
 for year in (2024,2025,2026):
  predictions[year]={k:sorted(rs,key=lambda r:(r["event_date"],r["fixture_id"])) for k,rs in predictions[year].items()};targets[year]=sorted(targets[year],key=lambda r:(r["event_date"],r["fixture_id"]));prices[year]=sorted(prices[year],key=lambda r:(r["date"],r["fixture_id"]))
  result["annual"][str(year)]=summarize(predictions[year],labels[year],prices[year],targets[year]);result["halves"][str(year)]={}
  for half in (1,2):
   t=[r for r in targets[year] if (1 if r["event_date"][5:7]<="06" else 2)==half];fids={r["fixture_id"] for r in t}
   if not t:continue
   ps={name:[r for r in rs if r["fixture_id"] in fids] for name,rs in predictions[year].items()};ls={f:labels[year][f] for f in fids};qs=[r for r in prices[year] if r["fixture_id"] in fids];result["halves"][str(year)][str(half)]=summarize(ps,ls,qs,t)
 result["2026_strict_price_sensitivity"]=summarize(pred26,labels[2026],[r for r in tiers26["strict_original_time_match"] if r["fixture_id"] in ids],targets[2026]);result["data_join_limitation"]=handoff["draw_selection_limitation"];result["source_hashes"]=SOURCES
 if verify:
  assert_replay_equal(json.loads((P/"results.json").read_bytes()),result)
 else:core.dump(P/"results.json",result)
 for year,d in result["annual"].items():
  print(year,{k:v for k,v in d["coverage"].items() if k!="excluded_ids"})
  for name in ("baseline","shrink","nb03","equal_ensemble"):
   m=d["outcome"][name];z=d["price"][name];print(name,m["hits"],round(m["accuracy"]*100,4),z["n"],z["net_units"],round(z["roi"]*100,4),z["max_daily_close_drawdown_units"])
  print("ROI_deltaCI%",[round(100*v,4) for v in d["price_paired_ensemble_minus_baseline"]["roi_delta_ci95"]])
if __name__=="__main__":
 parser=argparse.ArgumentParser();parser.add_argument("--verify",action="store_true");main(parser.parse_args().verify)
