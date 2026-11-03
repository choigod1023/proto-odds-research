"""Pinned Decimal/paired arithmetic from previously audited ranking comparison."""
import collections,math
from datetime import date
from decimal import Decimal
import numpy as np
def unique(rows):
 result={x["fixture_id"]:x for x in rows}
 if len(result)!=len(rows):raise ValueError("duplicate fixture")
 return result

def winner(label):
 h,a=label["home_score"],label["away_score"]
 if label["status"]!="final" or type(h) is not int or type(a) is not int or min(h,a)<0:raise ValueError("invalid final score")
 return "home" if h>a else "away" if h<a else "draw"

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
