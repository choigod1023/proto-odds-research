"""Independent replay of frozen forecasts and original Decimal price ledgers."""
import collections, gzip, hashlib, json, math, subprocess
from decimal import Decimal
from pathlib import Path
P=Path(__file__).resolve().parent
MODEL_REPO="C:/Users/user/Documents/Codex/kbtmodel"
OLD_REPO="C:/Users/user/Documents/Codex/kbeffects"
PRED_COMMIT="ff2bef35988eb5add154b4bebf8b265f42792db1"
OLD_COMMIT="d08ccb708033fb6b067e84e80eae81ea3c9b4cfa"
def blob(repo,rev,path):
 return subprocess.check_output(["git","-C",repo,"show",rev+":"+path])
def main():
 raw=blob(MODEL_REPO,PRED_COMMIT,"experiments/kbo-actual-training-20261103/artifacts/predictions.json")
 pred=json.loads(raw)
 assert pred["2026_labels_read"] is False and pred["target_n"]==709
 old="experiments/kbo-player-effects-20261103/inputs/"
 labels={r["fixture_id"]:r for r in map(json.loads,gzip.decompress(blob(OLD_REPO,OLD_COMMIT,old+"test-labels.jsonl.gz")).splitlines())}
 price=json.loads(blob(OLD_REPO,OLD_COMMIT,old+"price-ledgers.json"))["ledgers"]
 result={"prediction_commit":PRED_COMMIT,"prediction_sha256":hashlib.sha256(raw).hexdigest(),"fixed_price_and_label_commit":OLD_COMMIT,"retrospective_not_blind":True,"scenarios":{}}
 for selector,models in pred["predictions"].items():
  out={};ids=None
  for name,forecasts in models.items():
   index={r["fixture_id"]:r for r in forecasts}
   assert len(index)==len(forecasts)==709
   if ids is None:ids=set(index)
   assert ids==set(index)
   correct=0;loss=brier=0.;draws=0
   for fid,r in index.items():
    lab=labels[fid];h,a=lab["home_score"],lab["away_score"];truth="home" if h>a else "away" if h<a else "draw";draws+=truth=="draw"
    q=r["probabilities_3way"];assert all(0<=v<=1 for v in q.values()) and abs(sum(q.values())-1)<1e-10
    correct+=max(("home","draw","away"),key=q.get)==truth;loss-=math.log(max(q[truth],1e-15));brier+=sum((q[k]-(k==truth))**2 for k in ("home","draw","away"))
   tiers={}
   for tier,ledger in price.items():
    net=Decimal(0);daily=collections.defaultdict(Decimal);monthly=collections.defaultdict(Decimal);n=hit=void=0
    for row in ledger:
     fid=row["fixture_id"]
     if fid not in ids:continue
     n+=1;r=index[fid];q=r["probabilities_2way"];pick="away" if q["away"]>q["home"] else "home"
     lab=labels[fid];h,a=lab["home_score"],lab["away_score"];truth="home" if h>a else "away" if h<a else "draw"
     assert row["winner"]==truth
     void+=truth=="draw";hit+=pick==truth
     z=Decimal(0) if truth=="draw" else Decimal(str(row["price_source"]["prices"][pick]))-1 if pick==truth else Decimal(-1)
     net+=z;daily[row["date"]]+=z;monthly[row["date"][:7]]+=z
    peak=balance=dd=Decimal(0)
    for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
    tiers[tier]={"n":n,"hits":hit,"refunds":void,"net_units":str(net),"roi":float(net/n),"monthly_units":{k:str(v) for k,v in sorted(monthly.items())},"max_daily_close_drawdown_units":str(dd)}
   out[name]={"n":709,"hits":correct,"draws":draws,"accuracy":correct/709,"logloss":loss/709,"brier":brier/709,"prices":tiers}
  result["scenarios"][selector]=out
 (P/"parent-settlement.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
 for s,models in result["scenarios"].items():
  for name,r in models.items():
   z=r["prices"]["extended_day_team_source_confirmed"]
   print(s,name,r["hits"],round(r["accuracy"]*100,4),z["n"],z["net_units"],round(z["roi"]*100,4),z["max_daily_close_drawdown_units"])
if __name__=="__main__":main()
