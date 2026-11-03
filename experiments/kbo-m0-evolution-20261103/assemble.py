"""Integrate previously frozen family nominees before loading final outcomes."""
import gzip,hashlib,json,math,subprocess,sys
from pathlib import Path
P=Path(__file__).resolve().parent;REPO=P.parents[1];sys.path.insert(0,str(P/"common"));import core
BASE="experiments/kbo-m0-evolution-20261103/"
SOURCES={}
def fixed(rev,path):
 raw=subprocess.check_output(["git","-C",str(REPO),"show",rev+":"+BASE+path]);SOURCES[rev+":"+BASE+path]=hashlib.sha256(raw).hexdigest();return json.loads(gzip.decompress(raw) if path.endswith(".gz") else raw)
def validate(rows,targets):
 actual=core.unique(rows);expected=core.unique(targets)
 if set(actual)!=set(expected):raise ValueError("forecast cohort mismatch")
 for fid,r in actual.items():
  if r["event_date"]!=expected[fid]["event_date"]:raise ValueError("forecast date mismatch")
  q,v=r["probabilities_2way"],r["probabilities_3way"]
  for value,keys in ((q,{"home","away"}),(v,{"home","draw","away"})):
   if set(value)!=keys or any(not math.isfinite(z) or z<0 or z>1 for z in value.values()) or abs(sum(value.values())-1)>1e-9:raise ValueError("invalid forecasts")
  den=v["home"]+v["away"]
  if den<=0 or any(abs(q[k]-v[k]/den)>1e-9 for k in ("home","away")):raise ValueError("conditional forecast mismatch")
 return [actual[r["fixture_id"]] for r in targets]
def average(sets,targets):
 indexes=[core.unique(x) for x in sets];out=[]
 if len(indexes)!=3:raise ValueError("exactly three fixed family weights required")
 for r in targets:
  fid=r["fixture_id"];q={k:sum(m[fid]["probabilities_3way"][k] for m in indexes)/3 for k in ("home","draw","away")};den=q["home"]+q["away"]
  out.append({"fixture_id":fid,"event_date":r["event_date"],"probabilities_3way":q,"probabilities_2way":{k:q[k]/den for k in ("home","away")}})
 return out

def main():
 data=core.load();_,val=core.development(data);base=core.baseline_reference();family={};dev={"baseline":base["development_predictions"]};info={}
 rdev="7cc6b0b4aefcc52fcf2d7416d685ffdb33da6e2d";rfin="1110b13e7a1b552607cbeed53468cf15ca3bfbda";rsel=fixed(rdev,"recency/development/selection.json");rd={k:fixed(rdev,"recency/development/"+k+"/predictions.json") for k in rsel["configurations"]};rd["baseline"]=base["development_predictions"];rcheck=core.choose(rd,data["labels"])
 if rcheck["selected"]!=rsel["selected"]:raise ValueError("recency validation selection changed")
 dev["recency"]=rd[rsel["selected"]];family["recency"]={s:fixed(rfin,"recency/final/"+s+".json") for s in ("actual","projected")};info["recency"]={"selected":rsel["selected"],"development_commit":rdev,"prediction_commit":rfin,"selection":rcheck}
 odev="ca41b6da7f6d5cb89fd5d5e8dbfb6ad882bfcafb";ofin="8c708d0915f08b2f17c51e34b266cafc574b1035";os=fixed(odev,"opponent/development/selection.json");od=fixed(odev,"opponent/development/predictions.json");oc=core.choose(od,data["labels"])
 if oc["selected"]!=os["selected"]:raise ValueError("opponent selection changed")
 dev["opponent"]=od[os["selected"]];op=fixed(ofin,"opponent/final/predictions.json");family["opponent"]={s:op[s] for s in ("actual","projected")};info["opponent"]={"selected":os["selected"],"development_commit":odev,"prediction_commit":ofin,"selection":oc}
 ddev="373cd13a2590c2c0e8c1fb0516dbc9e5396e80a0";dfin="fddc7f998d4b1860867601529192031f1de188de";dd=fixed(ddev,"distribution/artifacts/development.json.gz");dc=core.choose(dd["predictions"],data["labels"])
 if dc["selected"]!=dd["selection"]["selected"]:raise ValueError("distribution selection changed")
 dp=fixed(dfin,"distribution/artifacts/final-predictions.json.gz");dev["distribution"]=dd["predictions"][dc["selected"]];family["distribution"]={s:dp["selected_predictions"]["test_"+s] for s in ("actual","projected")};info["distribution"]={"selected":dc["selected"],"development_commit":ddev,"prediction_commit":dfin,"selection":dc}
 selected=core.choose(dev,data["labels"]);allsets={};control=json.loads((P/"artifacts/baseline-final.json").read_bytes())
 for scenario in ("actual","projected"):
  target=data["test_"+scenario];sets={"baseline":control["prediction_sets"][scenario],**{key:v[scenario] for key,v in family.items()}};sets={k:validate(v,target) for k,v in sets.items()};sets["equal_ensemble"]=validate(average([sets[k] for k in ("recency","opponent","distribution")],target),target);sets["draw_hurdle"]=validate(dp["draw_hurdle_diagnostic_predictions"]["test_"+scenario],target);allsets[scenario]=sets
  if sets["opponent"]!=sets["baseline"]:raise ValueError("opponent fallback baseline no longer exact")
 core.dump(P/"artifacts/selection.json",{"family_info":info,"global_selection":selected,"baseline_and_nominee_validation_predictions":dev,"equal_ensemble_validation_metrics":core.metrics(average([dev[k] for k in ("recency","opponent","distribution")],val),data["labels"]),"test_outcomes_loaded":False,"source_hashes":SOURCES,"global_primary_winner_unchangeable_after_test":True})
 core.dump(P/"artifacts/predictions.json",{"predictions":allsets,"primary":selected["selected"],"contract_sha256":core.CONTRACT_SHA,"source_hashes":SOURCES,"test_outcomes_loaded":False,"ensemble_definition":"equal arithmetic mean of3unconditional3way distributions, conditional2way derived afterwards","candidate_choice_used2026":False})
 print(json.dumps({"family_nominees":{k:v["selected"] for k,v in info.items()},"global_primary":selected["selected"],"validation_hits":{k:v["decisive_hits"] for k,v in selected["metrics"].items()},"test_sets":{s:{k:len(v) for k,v in z.items()} for s,z in allsets.items()}}))
if __name__=="__main__":main()
