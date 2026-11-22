"""Recalculate only exact KBO event/price intersections from prior tipster studies."""
import collections,gzip,hashlib,json,subprocess
from datetime import datetime
from decimal import Decimal
import compare as c
P=c.P
SOURCE="dc9dffc82ae1f2081a3af113b08a8e22e3ef3b37"
CAND="66f1c255b41714dabc6dc0638bbd8f1e12e13006"
ROSTER="c4e6bdb67784ceaf76e39707e1b8083f222c0a7d"
RPATH="experiments/kbo-actual-training-data-20261103/actual-starters-2026.jsonl.gz"
TEAMS={"HT":"KIA","WO":"키움","OB":"두산","SK":"SSG","SS":"삼성","LT":"롯데","NC":"NC","KT":"KT","HH":"한화","LG":"LG"}

def direction(side):
 if side not in ("home","away"):raise ValueError("non two-way selection")
 return {"q2":{"home":int(side=="home"),"away":int(side=="away")},"q3":None}

def main():
 latest=c.read(c.NEW,c.NB+"artifacts/predictions.json")["predictions"];labels=c.unique(c.read(c.OLD,c.OB+"inputs/test-labels.jsonl.gz"));allprices=c.read(c.OLD,c.OB+"inputs/price-ledgers.json")["ledgers"];ids={r["fixture_id"] for r in latest["actual"]["M0_actual"]};prices=c.unique([r for r in allprices["extended_day_team_source_confirmed"] if r["fixture_id"] in ids]);strict={r["fixture_id"] for r in allprices["strict_original_time_match"]}
 raw=subprocess.check_output(["git","-C","C:/Users/user/Documents/Codex/kbtdata","show",ROSTER+":"+RPATH]);c.SOURCES[ROSTER+":"+RPATH]=hashlib.sha256(raw).hexdigest();official=[json.loads(x) for x in gzip.decompress(raw).splitlines()];aliases={};official_by_fid={}
 for r in official:
  r["date"]=datetime.strptime(r["date"],"%Y%m%d").date().isoformat() if len(r["date"])==8 else r["date"]
  fid=r["fixture_id"];official_by_fid[fid]=r
  for alias in (fid,r["official_game_id"],r["official_game_id"]+"2026"):
   if alias in aliases and aliases[alias]!=fid:raise ValueError("ambiguous official alias")
   aliases[alias]=fid
 def join(key,day,home,away,odds):
  if key not in aliases:raise ValueError("unverified official identity")
  fid=aliases[key];r=official_by_fid[fid]
  if day!=r["date"] or (home,away)!=(TEAMS[r["teams"]["home"]],TEAMS[r["teams"]["away"]]):raise ValueError(str(("event date/team mismatch",key,day,home,away,r["date"],r["teams"])))
  if fid not in prices:return fid,False
  pr=prices[fid]
  if (pr["home_id"],pr["away_id"])!=(home,away):raise ValueError("price team mismatch")
  if [Decimal(str(z)) for z in odds]!=[Decimal(str(pr["price_source"]["prices"][s])) for s in ("home","away")]:raise ValueError("both odds mismatch")
  return fid,True
 prefix="experiments/dynamic-score-state-model-20261030/";targets=[x for x in c.read(SOURCE,prefix+"inputs/targets.jsonl.gz") if x["league"]=="KBO" and x["actual_votes"]];allpred=[x for x in c.read(SOURCE,prefix+"results/predictions.jsonl.gz") if x["league"]=="KBO" and x["actual_votes"]];selected=c.read(SOURCE,prefix+"results/results.json")["selected"]["KBO"];kept={};excluded=[];eventmap={}
 for x in targets:
  if x["sides"]!=["home","away"] or x["vote_p"] is None:raise ValueError("not genuine two-way vote target")
  stamp=datetime.fromisoformat(x["kickoff"])
  if stamp.utcoffset().total_seconds()!=32400:raise ValueError("unexpected date timezone")
  fid,ok=join(x["event_id"],stamp.date().isoformat(),x["home"],x["away"],x["odds"]);eventmap[x["event_id"]]=fid
  if ok:
   truth=c.winner(labels[fid]);assert ["home","away"][x["outcome"]]==truth
   if fid in kept:raise ValueError("duplicate target")
   kept[fid]=x
  else:excluded.append({"fixture_id":fid,"reason":"not_in_fixed_M0_extended_price_cohort"})
 def refs(wanted):
  out={}
  for selector,name,label in [("actual","M0_actual","M0_actual"),("actual","M0_projected","M0_projected"),("actual","M2_actual","M2_actual")]:out[label]={r["fixture_id"]:c.forecast(r) for r in latest[selector][name] if r["fixture_id"] in wanted}
  return out
 configs=refs(kept);names={}
 for row in allpred:
  fid=eventmap[row["event_id"]]
  if fid not in kept:continue
  assert row["sides"]==["home","away"] and row["day"]==official_by_fid[fid]["date"]
  assert row["odds"]==kept[fid]["odds"] and row["outcome"]==kept[fid]["outcome"] and (row["home"],row["away"])==(kept[fid]["home"],kept[fid]["away"])
  for field in ("market","fixed9","vote","candidate"):
   if field!="candidate":name={"market":"market","fixed9":"market90_tipster10","vote":"tipster_vote"}[field]
   else:name="state."+row["mode"]+"."+row["model"]
   pick=row[field]["pick"];assert pick in (0,1);f=direction(["home","away"][pick]);bucket=configs.setdefault(name,{})
   if fid in bucket and bucket[fid]!=f:raise ValueError("inconsistent repeated baseline")
   bucket[fid]=f
   if field=="candidate":names[name]={"mode":row["mode"],"model":row["model"],"was_selected_on_2025":row["model"]==selected}
 def report(wanted,forecasts):
  rows=[prices[fid] for fid in sorted(wanted,key=lambda f:(prices[f]["date"],f))];ms={};ls={}
  for name,f in forecasts.items():
   if set(f)!=set(wanted):raise ValueError("unequal comparison cohort")
   ms[name],ls[name]=c.settle(rows,f,labels)
  return {"n":len(wanted),"strict_price_overlap":len(set(wanted)&strict),"date_range":[rows[0]["date"],rows[-1]["date"]],"metrics":ms,"rank_by_roi":sorted(ms,key=lambda k:(-ms[k]["roi"],-ms[k]["decisive_accuracy"],k)),"comparisons_M0_minus_other":{k:c.paired(ls["M0_actual"],v) for k,v in ls.items() if k!="M0_actual"},"fixture_ids":[r["fixture_id"] for r in rows]}
 out={"tipster_common":report(kept,configs),"selected_state_model":selected,"state_settings":names,"excluded_tipster":excluded,"historical_receipt_proven":False,"actual_execution_proven":False,"new_fits":0,"vote_onehot_is_direction_not_calibrated_probability":True}
 e0=[x for x in c.read(CAND,"data/model_candidates/kbo/archived-reproduction.jsonl.gz") if x["role"]=="evaluation" and x["date"].startswith("2026")];wanted={};econfigs={"e0":{},"e0_study_9_1":{}};excluded=[]
 for x in e0:
  fid,ok=join(x["source_fixture_id"],x["date"],x["home"],x["away"],x["prices"])
  if not ok:excluded.append({"fixture_id":fid,"reason":"not_in_fixed_M0_extended_price_cohort"});continue
  if fid in wanted:raise ValueError("duplicate e0 evaluation")
  wanted[fid]=x
  for key,field in [("e0","expected"),("e0_study_9_1","baseline_expected")]:
   side=x[field]["side"]
   if x[field]["settled"]!=1 or Decimal(str(x[field]["price"]))!=Decimal(str(x["prices"][["home","away"].index(side)])):raise ValueError("e0 selection price/settlement mismatch")
   econfigs[key][fid]=direction(side)
  if (x["hg"],x["ag"])!=(labels[fid]["home_score"],labels[fid]["away_score"]):raise ValueError("e0 score mismatch")
 econfigs.update(refs(wanted));out["e0_common"]=report(wanted,econfigs);out["excluded_e0"]=excluded;out["source_hashes"]=c.SOURCES
 (P/"tipster-results.json").write_text(json.dumps(out,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf8")
 for group in ("tipster_common","e0_common"):
  print(group,out[group]["n"])
  for name in out[group]["rank_by_roi"]:
   m=out[group]["metrics"][name];print(name,m["hits"],m["net_units"],round(100*m["roi"],5),m["max_daily_close_drawdown_units"])
 return out
if __name__=="__main__":main()
