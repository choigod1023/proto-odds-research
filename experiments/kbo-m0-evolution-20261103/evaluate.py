"""Load final outcomes only after immutable integrated forecast verification."""
import argparse,collections,gzip,hashlib,importlib.util,json,math,re,subprocess,sys
from datetime import date
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parent;REPO=P.parents[1];BASE="experiments/kbo-m0-evolution-20261103/";sys.path.insert(0,str(P/"common"));import core
spec=importlib.util.spec_from_file_location("m0_evolution_metrics",P/"evaluation/metrics.py");metrics=importlib.util.module_from_spec(spec);spec.loader.exec_module(metrics)
OLD="d08ccb708033fb6b067e84e80eae81ea3c9b4cfa";OB="experiments/kbo-player-effects-20261103/inputs/"

def frozen(commit,path,wanted):
 if not re.fullmatch("[0-9a-f]{40}",commit) or not re.fullmatch("[0-9a-f]{64}",wanted):raise ValueError("full freeze commit and SHA required")
 raw=subprocess.check_output(["git","-C",str(REPO),"show",commit+":"+BASE+path])
 if hashlib.sha256(raw).hexdigest()!=wanted or raw!=(P/path).read_bytes():raise ValueError("predictions differ from frozen Git blob")
 return json.loads(raw)
def load_evaluation():
 files={};parsed={}
 for name in ("test-labels.jsonl.gz","price-ledgers.json","price-reject-audit.jsonl.gz"):
  raw=subprocess.check_output(["git","-C",str(REPO),"show",OLD+":"+OB+name]);files[name]=hashlib.sha256(raw).hexdigest();parsed[name]=[json.loads(x) for x in gzip.decompress(raw).splitlines()] if name.endswith(".gz") else json.loads(raw)
 labels=core.unique(parsed["test-labels.jsonl.gz"]);prices=parsed["price-ledgers.json"]["ledgers"];rejects=core.unique(parsed["price-reject-audit.jsonl.gz"])
 return labels,prices,rejects,files

def outcome_pair(left,right,labels):
 a,b=core.unique(left),core.unique(right)
 if set(a)!=set(b):raise ValueError("outcome pairing mismatch")
 groups=collections.defaultdict(lambda:np.zeros(4));changes=0
 for fid in sorted(a,key=lambda f:(a[f]["event_date"],f)):
  truth=metrics.winner(labels[fid]);x,y=a[fid]["probabilities_3way"],b[fid]["probabilities_3way"];values=[]
  for q in (x,y):values.append(np.array([int(max(("home","draw","away"),key=q.get)==truth),-math.log(max(q[truth],1e-15)),sum((q[k]-(k==truth))**2 for k in ("home","draw","away"))]))
  groups[date.fromisoformat(a[fid]["event_date"]).isocalendar()[:2]]+=np.r_[1,values[0]-values[1]];changes+=max(x,key=x.get)!=max(y,key=y.get)
 arr=np.array([groups[k] for k in sorted(groups)]);rng=np.random.default_rng(20261103);boot=arr[rng.integers(0,len(arr),(5000,len(arr)))].sum(1)
 return {"weeks":len(arr),"changed3way_picks":changes,**{key:np.quantile(boot[:,j]/boot[:,0],[.025,.975]).tolist() for j,key in enumerate(("accuracy_delta_ci95","logloss_delta_ci95","brier_delta_ci95"),1)}}

def evaluate(commit,sha,input_loader=load_evaluation):
 pred=frozen(commit,"artifacts/predictions.json",sha)
 if pred["test_outcomes_loaded"] is not False or pred["candidate_choice_used2026"] is not False or pred["contract_sha256"]!=core.CONTRACT_SHA:raise ValueError("unexpected frozen selection state")
 sraw=subprocess.check_output(["git","-C",str(REPO),"show",commit+":"+BASE+"artifacts/selection.json"])
 if sraw!=(P/"artifacts/selection.json").read_bytes():raise ValueError("selection changed after prediction freeze")
 selection=json.loads(sraw)
 if pred["primary"]!=selection["global_selection"]["selected"]:raise ValueError("primary changed")
 data=core.load();reselected=core.choose(selection["baseline_and_nominee_validation_predictions"],data["labels"])
 if reselected!=selection["global_selection"]:raise ValueError("2025 selection no longer reproducible")
 # Only this callback may introduce outcome-bearing test assets.
 labels,tiers,rejects,filehashes=input_loader();out={"prediction_commit":commit,"prediction_sha256":sha,"primary_preselected_on2025":pred["primary"],"evaluation_inputs_commit":OLD,"evaluation_input_sha256":filehashes,"results":{},"retrospective_already_seen_not_blind":True,"actual_wallet_replay":False,"model_adoption":False,"multiple_comparisons_adjusted":False,"ledgers":{}}
 for scenario,sets in pred["predictions"].items():
  if set(sets)!={"baseline","recency","opponent","distribution","equal_ensemble","draw_hurdle"}:raise ValueError("unexpected forecast settings")
  ids=set(core.unique(sets["baseline"]))
  if len(ids)!=709 or any(set(core.unique(v))!=ids for v in sets.values()):raise ValueError("unequal709 target")
  target=core.unique(data["test_"+scenario]);assert ids==set(target)
  for rs in sets.values():
   if any(r["event_date"]!=target[r["fixture_id"]]["event_date"] for r in rs):raise ValueError("target date changed")
  r={"outcome":{},"outcome_paired_vs_baseline":{},"price_cohorts":{}}
  for name,rs in sets.items():
   score=core.metrics(rs,labels);score["mean_predicted_draw"]=sum(z["probabilities_3way"]["draw"] for z in rs)/709;score["observed_draw_rate"]=sum(labels[f]["home_score"]==labels[f]["away_score"] for f in ids)/709;r["outcome"][name]=score
   if name!="baseline":r["outcome_paired_vs_baseline"][name]=outcome_pair(rs,sets["baseline"],labels)
  indexes={k:core.unique(v) for k,v in sets.items()};base2={f:x["probabilities_2way"] for f,x in indexes["baseline"].items()}
  if any(indexes["draw_hurdle"][f]["probabilities_2way"]!=x for f,x in base2.items()):raise ValueError("draw-only diagnostic changed conditional probabilities")
  selections={tier:[x for x in rows if x["fixture_id"] in ids] for tier,rows in tiers.items()}
  for tier,rows in list(selections.items()):
   core.unique(rows);selections[tier+"_current_reject_excluded"]=[x for x in rows if x["price_source"]["origin"]=="official_restored" or rejects[x["fixture_id"]]["official_result"]["buyReject"]=="0"]
  out["ledgers"][scenario]={}
  for tier,rows in selections.items():
   models={};ledger={}
   for name,rs in indexes.items():models[name],ledger[name]=metrics.settle(rows,{f:{"q2":x["probabilities_2way"]} for f,x in rs.items()},labels)
   if models["opponent"]!=models["baseline"] or models["draw_hurdle"]!=models["baseline"]:raise ValueError("unchanged-pick control settlement differs")
   r["price_cohorts"][tier]={"n":len(rows),"models":models,"paired_candidate_minus_baseline":{k:metrics.paired(v,ledger["baseline"]) for k,v in ledger.items() if k!="baseline"}};out["ledgers"][scenario][tier]=ledger
  out["results"][scenario]=r
 # Prior independently verified unchanged M0 control, same original ledger.
 previous=json.loads(subprocess.check_output(["git","show","54dbcfcdd9ffc3ca0ad473cf9d586a38398cde4e:experiments/kbo-actual-training-20261103/artifacts/results.json"]))
 for s,r in out["results"].items():
  assert r["outcome"]["baseline"]["hits"]==previous["results"][s]["outcome"]["M0_actual"]["threeway"]["hits"]
  for tier,x in r["price_cohorts"].items():
   a,b=x["models"]["baseline"],previous["results"][s]["price_cohorts"][tier]["models"]["M0_actual"]
   if a["net_units"]!=b["net_units"] or abs(a["roi"]-b["roi_including_refunds"])>1e-12 or a["monthly_units"]!=b["monthly_units"]:raise ValueError("original M0 control not reproduced")
 out["unchanged_M0_control_exact"]=True
 return out

def main():
 parser=argparse.ArgumentParser();parser.add_argument("--freeze-commit",required=True);parser.add_argument("--prediction-sha256",required=True);args=parser.parse_args();out=evaluate(args.freeze_commit,args.prediction_sha256);core.dump(P/"artifacts/results.json",out)
 for s,r in out["results"].items():
  print(s)
  for name,o in r["outcome"].items():
   m=r["price_cohorts"]["extended_day_team_source_confirmed"]["models"][name];print(name,o["hits"],round(o["accuracy"]*100,4),m["net_units"],round(m["roi"]*100,4),m["max_daily_close_drawdown_units"])
if __name__=="__main__":main()
