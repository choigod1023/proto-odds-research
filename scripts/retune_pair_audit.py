"""Independent paired settlement and hit/odds decomposition for frozen policies."""
from collections import defaultdict
from decimal import Decimal,ROUND_DOWN
import math


def audit(rows):
    n=len(rows);wins=[0,0];pushes=0;net=[0,0];daily=[defaultdict(int),defaultdict(int)]
    gained=lost=changes=0;gained_payout=lost_payout=0;brier=[0.,0.];loss=[0.,0.]
    for row in rows:
        base,new=row['baseline_probability'],row['candidate_probability']
        if any(not isinstance(p,(int,float)) or not math.isfinite(p) or not 0<=p<=1 for p in [base,new]):raise ValueError('invalid_probability')
        prices=row['prices'];order=row.get('order',['home','away'])
        if len(prices)!=2 or set(order)!={'home','away'} or any(not math.isfinite(p) or p<=1 for p in prices):raise ValueError('invalid_price')
        sides=['home' if p>=.5 else 'away' for p in [base,new]];changes+=int(sides[0]!=sides[1])
        if row['hg']==row['ag']:pushes+=1;continue
        winner='home' if row['hg']>row['ag'] else 'away';correct=[s==winner for s in sides]
        payouts=[int((Decimal(str(prices[order.index(s)]))*10000).to_integral_value(rounding=ROUND_DOWN)) for s in sides]
        for i,p in enumerate([base,new]):
            wins[i]+=int(correct[i]);profit=payouts[i]-10000 if correct[i] else -10000
            net[i]+=profit;daily[i][row['date']]+=profit
            y=int(winner=='home');brier[i]+=(p-y)**2;clipped=min(1-1e-9,max(1e-9,p));loss[i]-=math.log(clipped if y else 1-clipped)
        if correct[1] and not correct[0]:gained+=1;gained_payout+=payouts[1]
        if correct[0] and not correct[1]:lost+=1;lost_payout+=payouts[0]
    if net[1]-net[0]!=gained_payout-lost_payout:raise AssertionError('different picks/prices/cohort or invalid settlement')
    metrics=[]
    for i in [0,1]:
        balance=peak=drawdown=0
        for day in sorted(daily[i]):balance+=daily[i][day];peak=max(peak,balance);drawdown=max(drawdown,peak-balance)
        metrics.append(dict(n=n,wins=wins[i],pushes=pushes,accuracy=wins[i]/(n-pushes) if n>pushes else None,
            roi=net[i]/(n*10000) if n else None,net_krw=net[i],brier=brier[i]/(n-pushes) if n>pushes else None,
            logloss=loss[i]/(n-pushes) if n>pushes else None,daily_close_max_drawdown_krw=drawdown))
    return dict(baseline=metrics[0],candidate=metrics[1],pick_changes=changes,
        decomposition=dict(gained_wins=gained,lost_wins=lost,gained_payout_krw=gained_payout,lost_payout_krw=lost_payout,
            net_improvement_krw=gained_payout-lost_payout,
            interpretation='ROI difference equals new-only winning payouts minus baseline-only winning payouts; same-game fixed stake'))
