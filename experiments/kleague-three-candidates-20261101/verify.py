"""Independent settlement and closed-form gate/FL replay; no refitting."""
from collections import defaultdict
from decimal import Decimal
from datetime import date,timedelta
import hashlib,json,math
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]


def bayes_poisson_replay(state,row):
    """Independent score-sum integration, without model code or Skellam CDF."""
    teams=state['teams'];n=len(teams);draws=np.array(state['draws']);assert draws.shape==(128,2*n+3)
    np.testing.assert_allclose(draws.mean(0),state['map'],atol=1e-12,rtol=0)
    a=draws[:,2:2+n];a=a-a.mean(1)[:,None]
    d=draws[:,2+n:2+2*n];d=d-d.mean(1)[:,None]
    def effects(team):
        if team in teams:return a[:,teams.index(team)],d[:,teams.index(team)]
        token=json.dumps([20261101,str(row['id']),str(team)],ensure_ascii=False,separators=(',',':')).encode('utf8')
        seed=int.from_bytes(hashlib.sha256(token).digest()[:16],'big')
        x=np.random.default_rng(seed).normal(size=(64,2))/math.sqrt(10);x=np.r_[x,-x]
        return x[:,0],x[:,1]
    ah,dh=effects(row['home_id']);aa,da=effects(row['away_id'])
    w=1/(1+np.exp(-draws[:,-1]))
    th=np.clip(row['dc_lh']*np.exp(draws[:,0]+draws[:,1]+ah+da),.01,10)
    ta=np.clip(row['dc_la']*np.exp(draws[:,0]+aa+dh),.01,10)
    lh=(1-w)*th+w*row['market_lh'];la=(1-w)*ta+w*row['market_la']
    goals=np.arange(81);lf=np.array([math.lgamma(int(k)+1) for k in goals])
    hp=np.exp(-lh[:,None]+goals*np.log(lh[:,None])-lf)
    ap=np.exp(-la[:,None]+goals*np.log(la[:,None])-lf)
    assert min(hp.sum(1))>1-1e-12 and min(ap.sum(1))>1-1e-12
    home=(hp*(np.cumsum(ap,1)-ap)).sum(1)
    draw=(hp*ap).sum(1)
    away=(ap*(np.cumsum(hp,1)-hp)).sum(1)
    return np.array([home.mean(),draw.mean(),away.mean()])


def verify():
    result=json.loads((HERE/'results.json').read_text(encoding='utf8'))
    ledger=[json.loads(s) for s in (HERE/'ledger.jsonl').read_text(encoding='utf8').splitlines()]
    states=json.loads((HERE/'states.json').read_text(encoding='utf8'))
    rows=[json.loads(s) for s in (HERE/'oof.jsonl').read_text(encoding='utf8').splitlines()]
    byid={r['id']:r for r in rows}
    previous={r['id']:r for r in [json.loads(s) for s in (ROOT/'experiments/kleague-roi-diagnostic-20261101/ledger.jsonl').read_text(encoding='utf8').splitlines()]}
    assert len(ledger)==len(previous)==227 and len({r['id'] for r in ledger})==227
    assert {r['id'] for r in ledger}==set(previous)
    assert not result['adopted'] and not result['price_receipt_verified'] and not result['formal_validation']
    for info in states:
        cutoff=(date.fromisoformat(info['reference_day'])-timedelta(days=7)).isoformat()
        expected={r['id'] for r in rows if r['day']<=cutoff}
        assert len(info['training_ids'])==len(set(info['training_ids']))
        assert set(info['training_ids'])==expected
        assert info['state']['training_rows']==len(expected)
        assert info['state']['cutoff_inclusive']==cutoff
    replay_error={'market':0.,'dc':0.,'moe_two_expert':0.,'fl_glm':0.,'bayes_market_goal':0.}
    for r in ledger:
        old=previous[r['id']]
        assert all(r[k]==byid[r['id']][k] for k in ('day','prices','hg','ag','y'))
        assert r['prices']==old['prices'] and r['y']==old['y']
        for name,oldname in [('market','market'),('dc','time_weighted_dc')]:
            err=max(abs(np.array(r['forecasts'][name]['p'])-np.array(old['forecasts'][oldname]['p'])))
            replay_error[name]=max(replay_error[name],float(err));assert err<1e-10
        month=r['day'][:7]+'-01'
        moe=next(s['state'] for s in states if s['model']=='moe_two_expert' and s['reference_day']==month)
        q=np.array(r['market_p']);d=np.array(r['dc_p'])
        raw=np.array([max(q)-1/3,q[1],sum(abs(q-d)),float(r['unknown_dc_team'])])
        x=np.r_[1.,(raw-moe['mean'])/moe['scale']]
        g=1/(1+math.exp(-float(x@np.array(moe['beta']))))
        p=g*q+(1-g)*d
        err=float(max(abs(p-r['forecasts']['moe_two_expert']['p'])))
        replay_error['moe_two_expert']=max(replay_error['moe_two_expert'],err);assert err<1e-10
        fl=next(s['state'] for s in states if s['model']=='fl_glm' and s['reference_day']==month)
        beta=fl['beta'];weights=np.array([float(x)**(-beta) for x in r['prices']]);p=weights/sum(weights)
        err=float(max(abs(p-r['forecasts']['fl_glm']['p'])))
        replay_error['fl_glm']=max(replay_error['fl_glm'],err);assert err<1e-10
        if beta>0:assert r['forecasts']['fl_glm']['pick']==r['forecasts']['market']['pick']
        bayes=next(s['state'] for s in states if s['model']=='bayes_market_goal' and s['reference_day']==month)
        p=bayes_poisson_replay(bayes,r)
        err=float(max(abs(p-r['forecasts']['bayes_market_goal']['p'])))
        replay_error['bayes_market_goal']=max(replay_error['bayes_market_goal'],err);assert err<1e-10
    for name,summary in result['models'].items():
        hits=bets=0;net=Decimal(0);daily=defaultdict(Decimal);monthly=defaultdict(Decimal)
        for r in ledger:
            f=r['forecasts'][name];p=f['p'];best=max(p);tops=[i for i,v in enumerate(p) if abs(v-best)<=1e-12]
            pick=tops[0] if len(tops)==1 else None
            hit=pick is not None and pick==r['y']
            n=Decimal(0) if pick is None else Decimal(r['prices'][pick])-1 if hit else Decimal(-1)
            assert pick==f['pick'] and hit==f['hit'] and n==Decimal(f['net'])
            net+=n;hits+=hit;bets+=pick is not None;daily[r['day']]+=n;monthly[r['day'][:7]]+=n
        balance=peak=dd=Decimal(0)
        for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
        assert hits==summary['hits'] and bets==summary['bets']
        assert net==Decimal(summary['net_units']) and dd==Decimal(summary['daily_close_drawdown_units'])
        assert {k:Decimal(v) for k,v in summary['monthly_net'].items()}==monthly
        assert abs(float(net/bets)-summary['roi'])<1e-12
    report=dict(evaluation_games=227,verified_settlements=227*5,
                closed_form_or_prior_baseline_probability_replays=227*4,
                independent_bayesian_poisson_sum_probability_replays=227,
                maximum_replay_error=replay_error,
                bayesian_probability_replayed=True,
                note='Bayesian saved posterior integrated independently over Poisson scores0..80; MAP and Laplace fit are not independently re-estimated here. Market/DC compared with prior baseline artifact. Source and input hashes are reproducibility records, not historical pregame certification.',
                ledger_sha256=hashlib.sha256((HERE/'ledger.jsonl').read_bytes()).hexdigest())
    (HERE/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(report))


if __name__=='__main__':verify()
