# Adapted from prior local tipster research; reused without changing selection rules.
"""Offline end-to-end selection replay. Development evidence, never promotion.

Retains unresolved games BEFORE recommendation/ticket selection. Fixed policies,
no winner selected for deployment. Tipster shares are uncalibrated vote scores.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
import hashlib
import importlib.util
from itertools import combinations
import json
import math
from pathlib import Path

KST = timezone(timedelta(hours=9))
ARMS = ('model', 'tipster', 'hybrid75_25')
POLICIES = ('probability', 'edge', 'pair_edge')
STAKE, LIMIT = 10000, 50000


def stamp(s):
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        raise ValueError('Timezone required')
    return t


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def recommend(rows, arm, policy):
    out = []
    for r in rows:
        p = r['model'] if arm=='model' else r['tipster']
        if arm=='hybrid75_25':
            p = [.75*a+.25*b for a,b in zip(r['model'], r['tipster'])] if r['tipster'] else None
        if p is None:
            continue
        # Same thresholds, no upper odds cap. Choose among eligible sides.
        eligible = [i for i in range(len(p)) if p[i]>=.55 and r['odds'][i]>=1.5]
        if not eligible:
            continue
        side = max(eligible, key=lambda i: (p[i] if policy=='probability' else p[i]*r['odds'][i]-1, -i))
        edge = p[side]*r['odds'][side]-1
        if policy!='probability' and edge<=0:
            continue
        out.append(dict(r, side=side, probability=p[side], price=r['odds'][side], edge=edge))
    return out


def rounded_price(pair):
    product = Decimal(str(pair[0]['price'])) * Decimal(str(pair[1]['price']))
    return float(product.quantize(Decimal('.01'), rounding=ROUND_DOWN).quantize(Decimal('.1'), rounding=ROUND_CEILING))


def select_pair(rows, policy):
    pairs = [p for p in combinations(rows,2) if p[0]['event']!=p[1]['event']
             and p[0].get('round_key')==p[1].get('round_key')]
    if not pairs:
        return []
    def key(pair):
        if policy=='pair_edge':
            value = math.prod(x['probability'] for x in pair)*rounded_price(pair)-1
        else:
            value = sum(x['probability'] if policy=='probability' else x['edge'] for x in pair)
        return (-value, tuple(sorted(x['id'] for x in pair)))
    return list(min(pairs, key=key))


def replay_tickets(recs, policy, weekly, cutoff):
    months, weeks, pending, tickets, used = defaultdict(int), Counter(), [], [], set()
    net = peak = drawdown = 0
    def settle(now):
        nonlocal net, peak, drawdown
        for t in sorted(pending[:], key=lambda x: stamp(x['settled_at']) if x['settled_at'] else datetime.max.replace(tzinfo=KST)):
            if not t['settled_at'] or stamp(t['settled_at'])>now:
                continue
            t['net'] = int(round(t['price']*STAKE))-STAKE if t['hit'] else -STAKE
            months[t['month']] += t['net']; net += t['net']
            peak = max(peak,net); drawdown = max(drawdown,peak-net)
            pending.remove(t)
    for when in sorted({r['at'] for r in recs}, key=stamp):
        now = stamp(when)
        if now>cutoff:
            continue
        settle(now)
        local = now.astimezone(KST); month = local.strftime('%Y-%m'); week = local.isocalendar()[:2]
        available = [r for r in recs if r['at']==when and r['event'] not in used
                     and stamp(r['snapshot_at'])<=now<=stamp(r['kickoff'])-timedelta(minutes=30)]
        if weeks[week]>=weekly or months[month]-STAKE*(len(pending)+1)<-LIMIT:
            continue
        pair = select_pair(available,policy)
        if not pair:
            continue
        # Outcomes enter ONLY after pair selection. Unknowns reserve exposure.
        losses = [stamp(r['settled_at']) for r in pair if r['y'] is not None and r['side']!=r['y'] and r['settled_at']]
        settled = min(losses) if losses else max(stamp(r['settled_at']) for r in pair) if all(r['settled_at'] for r in pair) else None
        ticket = dict(at=when, month=month, ids=[r['id'] for r in pair], sides=[r['side'] for r in pair],
                      price=rounded_price(pair), hit=all(r['y']==r['side'] for r in pair),
                      settled_at=settled.isoformat() if settled else None,
                      reserved_budget_before=LIMIT+months[month]-STAKE*len(pending))
        tickets.append(ticket); pending.append(ticket); weeks[week]+=1; used.update(r['event'] for r in pair)
    settle(cutoff)
    assert all(v>=-LIMIT for v in months.values())
    n = sum('net' in t for t in tickets)
    return dict(tickets=len(tickets),settled=n,pending=len(pending),net_krw=net,
                roi_pct=100*net/(STAKE*n) if n else None,pending_stake_krw=STAKE*len(pending),
                worst_case_net_krw=net-STAKE*len(pending),max_drawdown_krw=drawdown,
                monthly_net=dict(months),ticket_log=tickets)


def compare(rows, cutoff=None):
    result = {}
    for arm in ARMS:
        result[arm] = {}
        for policy in POLICIES:
            recs = recommend(rows,arm,policy)
            settled = [r for r in recs if r['y'] is not None]
            hits = sum(r['side']==r['y'] for r in settled)
            profit = sum(r['price']*(r['side']==r['y'])-1 for r in settled)
            result[arm][policy] = dict(opportunities=len(rows),recommendations=len(recs),skipped=len(rows)-len(recs),
                settled=len(settled),hits=hits,hit_rate_pct=100*hits/len(settled) if settled else None,
                single_roi_pct=100*profit/len(settled) if settled else None,
                picks=[dict(id=r['id'],side=r['side'],score=r['probability'],price=r['price']) for r in recs],
                paper={str(w):replay_tickets(recs,policy,w,cutoff) for w in (2,3)} if cutoff else None)
    return result
