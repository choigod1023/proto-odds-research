"""Independent cash-book, saved-state replay and strict temporal poison checks."""
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import socket
from copy import deepcopy

import numpy as np
from model import State, Engine, timestamp, due, CONFIGS
from run import read, FREEZE, LEAGUES, holm

HERE=Path(__file__).resolve().parent


def verify(output):
    original_socket=socket.socket
    socket.socket=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network prohibited in offline replay'))
    try:
        results=json.loads((output/'results.json').read_text(encoding='utf-8'))
        rows=read(output/'predictions.jsonl.gz')
        states=json.loads((output/'trained-states.json').read_text(encoding='utf-8'))
        for league_states in states.values():
            for state in league_states.values():
                assert np.linalg.eigvalsh(np.asarray(state['covariance'])).min()>-1e-7
        groups=defaultdict(list)
        frozen_count=0
        for r in rows:
            if r['actual_votes']:
                groups[r['league'],r['model'],r['mode']].append(r)
            if r['mode']=='frozen':
                s=State.restore(states[r['league']][r['model']])
                p,_=s.predict(r['home'],r['away'],timestamp(r['decision_at']))
                assert p==r['candidate']['p'], 'frozen forecast not reproducible from saved training state'
                frozen_count+=1
        legs=0
        for summary in results['actual_vote_2026']:
            rs=groups[summary['league'],summary['model'],summary['mode']]
            for role, expected in summary['metrics'].items():
                got=[r for r in rs if r[role] is not None]
                wins=live=0
                cash=Decimal(0)
                logsum=bsum=0.
                for r in got:
                    v=r[role]
                    win=v['pick']==r['outcome']
                    payout=Decimal(1) if v['void'] else Decimal(str(r['odds'][v['pick']])) if win else Decimal(0)
                    cash+=payout-1
                    assert int((payout-1)*100)==v['profit_cents']
                    if not v['void']:
                        live+=1;wins+=int(win)
                        logsum-=math.log(max(v['p'][r['outcome']],1e-12))
                        bsum+=sum((float(p)-int(i==r['outcome']))**2 for i,p in enumerate(v['p']))
                    legs+=1
                assert expected['bets']==len(got) and expected['nonvoid']==live and expected['wins']==wins
                if got:
                    assert abs(float(cash/len(got))-expected['roi'])<1e-12
                if live:
                    assert wins/live==expected['accuracy']
                    if expected['logloss'] is not None:
                        assert abs(logsum/live-expected['logloss'])<1e-12
                        assert abs(bsum/live-expected['brier'])<1e-12
        # Rebuild *all* training states with all 2026 labels swapped.
        history=read(HERE/'inputs/history.jsonl.gz')
        poison=deepcopy(history)
        for r in poison:
            if r['date'][:4]=='2026':r['hg'],r['ag']=r['ag'],r['hg']
        state_checks=0
        causal_checks=0
        for league in LEAGUES:
            for name,kind,q in CONFIGS:
                engine=Engine(poison,league,kind,q)
                engine.advance(FREEZE)
                assert engine.state.fingerprint()==State.restore(states[league][name]).fingerprint()
                state_checks+=1
            picked=results['selected'][league]
            if picked:
                rs=groups[league,picked,'prequential']
                if rs:
                    t=max(rs,key=lambda r:r['decision_at'])
                    at=timestamp(t['decision_at'])
                    future_poison=deepcopy(history)
                    for r in future_poison:
                        if due(r)>at:r['hg'],r['ag']=r['ag'],r['hg']
                    _,kind,q=next(c for c in CONFIGS if c[0]==picked)
                    a=Engine(history,league,kind,q);b=Engine(future_poison,league,kind,q)
                    assert a.predict(t['home'],t['away'],at)==b.predict(t['home'],t['away'],at)
                    causal_checks+=1
        # Exact discordance tail via direct combinatorial summation, independent
        # of scipy.stats.binomtest used in the main experiment.
        ps=[]
        for c in results['contrasts']:
            rs=groups[c['league'],c['model'],c['mode']]
            better=sum(r['candidate']['hit']>r[c['baseline']]['hit'] for r in rs)
            worse=sum(r['candidate']['hit']<r[c['baseline']]['hit'] for r in rs)
            n=better+worse
            p=sum(math.comb(n,k) for k in range(better,n+1))/2**n if n else 1.
            assert abs(p-c['p'])<1e-12
            ps.append(p)
        order=sorted(range(len(ps)),key=lambda i:ps[i]);corrected=[1.]*len(ps);last=0.
        for j,i in enumerate(order):
            last=max(last,min(1.,(len(ps)-j)*ps[i]));corrected[i]=last
        assert all(abs(c['p_holm80']-p)<1e-12 for c,p in zip(results['contrasts'],corrected))
        validation={'independent_actual_price_legs':legs,'frozen_probability_replay':frozen_count,
            'all_test_label_poison_training_states_unchanged':state_checks,
            'causal_unavailable_future_poison_checks':causal_checks,'holm80_independent':True,
            'network_blocked':True,'metric_tolerance':1e-12}
        (output/'independent-verification.json').write_text(json.dumps(validation,indent=2)+'\n',encoding='utf-8',newline='\n')
        print(json.dumps(validation))
    finally:
        socket.socket=original_socket


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=HERE/'results');verify(p.parse_args().output)
