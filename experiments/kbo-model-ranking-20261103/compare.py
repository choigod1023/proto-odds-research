"""Audit existing frozen KBO forecasts; no collection, fitting or model changes."""
import collections,functools,gzip,hashlib,json,math,subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parent
ROOT=P.parents[1]
OLD="d08ccb708033fb6b067e84e80eae81ea3c9b4cfa"
NEW="ff2bef35988eb5add154b4bebf8b265f42792db1"
OB="experiments/kbo-player-effects-20261103/"
NB="experiments/kbo-actual-training-20261103/"
REF="matched.actual.M0_actual"
SOURCES={}
@functools.lru_cache(None)
def blob(rev,path):
 raw=subprocess.check_output(["git","-C",str(ROOT),"show",rev+":"+path])
 SOURCES[rev+":"+path]=hashlib.sha256(raw).hexdigest()
 return raw
def read(rev,path):
 raw=blob(rev,path)
 return [json.loads(x) for x in gzip.decompress(raw).splitlines()] if path.endswith(".gz") else json.loads(raw)
def unique(rows):
 result={x["fixture_id"]:x for x in rows}
 if len(result)!=len(rows):raise ValueError("duplicate fixture")
 return result
def winner(label):
 h,a=label["home_score"],label["away_score"]
 if label["status"]!="final" or type(h) is not int or type(a) is not int or min(h,a)<0:raise ValueError("invalid final score")
 return "home" if h>a else "away" if h<a else "draw"
def forecast(row):
 q=row["probabilities_2way"];v=row.get("probabilities_3way")
 if set(q)!={"home","away"} or any(not math.isfinite(x) or not 0<=x<=1 for x in q.values()) or abs(sum(q.values())-1)>1e-10:raise ValueError("invalid two-way probabilities")
 return {"q2":q,"q3":v}
def pick(f):return "away" if f["q2"]["away"]>f["q2"]["home"] else "home"
def settle(rows,fs,labels):
 net=Decimal(0);hit=void=0;daily=collections.defaultdict(Decimal);monthly=collections.defaultdict(Decimal);records=[]
 for row in rows:
  fid=row["fixture_id"];truth=winner(labels[fid]);side=pick(fs[fid]);prices=row["price_source"]["prices"]
  if set(prices)!={"home","away"} or any(Decimal(str(v))<=1 for v in prices.values()):raise ValueError("invalid full-game two-way prices")
  if row["winner"]!=truth:raise ValueError("price/result mismatch")
  z=Decimal(0) if truth=="draw" else Decimal(str(prices[side]))-1 if side==truth else Decimal(-1)
  net+=z;hit+=side==truth;void+=truth=="draw";daily[row["date"]]+=z;monthly[row["date"][:7]]+=z
  records.append({"fixture_id":fid,"date":row["date"],"winner":truth,"pick":side,"net":str(z),"hit":side==truth})
 peak=balance=dd=Decimal(0)
 for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
 n=len(rows)
 return {"n":n,"hits":hit,"refunds":void,"decisive":n-void,"decisive_accuracy":hit/(n-void) if n>void else None,"net_units":str(net),"roi":float(net/n) if n else None,"monthly_units":{k:str(v) for k,v in sorted(monthly.items())},"profitable_months":sum(v>0 for v in monthly.values()),"max_daily_close_drawdown_units":str(dd)},records

def paired(a,b):
 if [r["fixture_id"] for r in a]!=[r["fixture_id"] for r in b]:raise ValueError("paired fixture mismatch")
 groups=collections.defaultdict(lambda:np.zeros(4));changed=0
 for x,y in zip(a,b):
  g=groups[date.fromisoformat(x["date"]).isocalendar()[:2]];g[0]+=x["winner"]!="draw";g[1]+=int(x["hit"])-int(y["hit"]);g[2]+=1;g[3]+=float(Decimal(x["net"])-Decimal(y["net"]));changed+=x["pick"]!=y["pick"]
 if len(groups)<2:return {"weeks":len(groups),"changed_picks":changed,"roi_delta_ci95":None}
 arr=np.array([groups[k] for k in sorted(groups)]);rng=np.random.default_rng(20261103);boot=arr[rng.integers(0,len(arr),(5000,len(arr)))].sum(1)
 return {"weeks":len(groups),"changed_picks":changed,"roi_delta_ci95":np.quantile(boot[:,3]/boot[:,2],[.025,.975]).tolist(),"decisive_accuracy_delta_ci95":np.quantile(boot[:,1]/boot[:,0],[.025,.975]).tolist()}

def build():
 latest=read(NEW,NB+"artifacts/predictions.json");labels=unique(read(OLD,OB+"inputs/test-labels.jsonl.gz"));configs={};meta={}
 for scenario,models in latest["predictions"].items():
  for name,rows in models.items():
   key="matched."+scenario+"."+name;configs[key]={x["fixture_id"]:forecast(x) for x in rows};meta[key]={"source_commit":NEW,"evaluation_roster":scenario,"training":name,"current_service_replay":False}
 ids=set(configs[REF]);assert len(ids)==709
 earlier=read(OLD,OB+"artifacts/predictions.json")
 for scenario,models in earlier["scenarios"].items():
  for family in ("M1","M2"):
   key="prior."+scenario+"."+family;configs[key]={x["fixture_id"]:forecast(x) for x in models[family]};meta[key]={"source_commit":OLD,"evaluation_roster":scenario,"family":family,"M2_is_uncorrected_legacy":family=="M2"}
 fresh=read(OLD,OB+"inputs/old-fresh-predictions.json")["predictions"]
 for scenario in fresh[0]["settings"]:
  key="fixed_player."+scenario;configs[key]={x["fixture_id"]:forecast(x["settings"][scenario]) for x in fresh};meta[key]={"source_commit":OLD,"evaluation_roster":scenario,"one_fixed_model_different_input_scenario":True}
 for key,fs in configs.items():
  if set(fs)!=ids:raise ValueError("full probability cohort differs: "+key)
 price=read(OLD,OB+"inputs/price-ledgers.json")["ledgers"];selected={tier:[x for x in rows if x["fixture_id"] in ids] for tier,rows in price.items()}
 for tier,rows in selected.items():unique(rows)
 common=selected["extended_day_team_source_confirmed"]
 for name in common[0]["forecasts"]:
  key="ledger."+name;fs={}
  for row in common:
   f=row["forecasts"][name];q=f["probabilities"];fs[row["fixture_id"]]=forecast({"probabilities_2way":dict(zip(("home","away"),q))})
  configs[key]=fs;meta[key]={"source_commit":OLD,"evaluation_roster":"legacy_ledger_setting_not_same_roster_control","full_709_forecasts_available":False,"model":name}
 for rows in selected.values():
  if any(not {x["fixture_id"] for x in rows}<=set(v) for v in configs.values()):raise ValueError("unequal price-cohort coverage")
 rejects=unique(read(OLD,OB+"inputs/price-reject-audit.jsonl.gz"))
 for tier,rows in list(selected.items()):
  selected[tier+"_current_reject_excluded"]=[x for x in rows if x["price_source"]["origin"]=="official_restored" or rejects[x["fixture_id"]]["official_result"]["buyReject"]=="0"]
 out={"model_metadata":meta,"full_709_outcomes":{},"price_cohorts":{},"current_service_replay":False,"retrospective_not_blind":True,"new_model_fits":0,"multiple_comparisons_corrected":False,"global_all_sports_winner_proven":False,"source_hashes":SOURCES}
 for key,fs in configs.items():
  if set(fs)!=ids:continue
  hits=0;ll=br=0.
  for fid,f in fs.items():
   q=f["q3"];truth=winner(labels[fid]);hits+=max(("home","draw","away"),key=q.get)==truth;ll-=math.log(max(q[truth],1e-15));br+=sum((q[k]-(k==truth))**2 for k in ("home","draw","away"))
  out["full_709_outcomes"][key]={"n":709,"hits":hits,"accuracy":hits/709,"logloss":ll/709,"brier":br/709}
 for tier,rows in selected.items():
  metrics={};ledgers={}
  for key,fs in configs.items():metrics[key],ledgers[key]=settle(rows,fs,labels)
  equivalent=collections.defaultdict(list)
  for key,rs in ledgers.items():equivalent[hashlib.sha256(json.dumps([r["pick"] for r in rs]).encode()).hexdigest()].append(key)
  rank=sorted(metrics,key=lambda k:(-metrics[k]["roi"],-metrics[k]["decisive_accuracy"],k))
  out["price_cohorts"][tier]={"n":len(rows),"metrics":metrics,"roi_ranking":rank,"comparisons_reference_minus_candidate":{key:paired(ledgers[REF],v) for key,v in ledgers.items() if key!=REF},"identical_pick_groups":[v for v in equivalent.values() if len(v)>1],"distinct_pick_vectors":len(equivalent)}
 return out

def main():
 out=build();(P/"results.json").write_text(json.dumps(out,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf8")
 print("settings",len(out["model_metadata"]),"full-probability",len(out["full_709_outcomes"]))
 for tier,d in out["price_cohorts"].items():
  print(tier,"n",d["n"],"distinct_picks",d["distinct_pick_vectors"])
  for key in d["roi_ranking"][:4]:
   x=d["metrics"][key];print(key,x["hits"],x["decisive"],x["net_units"],round(x["roi"]*100,5),x["max_daily_close_drawdown_units"])
if __name__=="__main__":main()
