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


def collect(recovery, replay, teams, events):
    ledger, games, excluded, commits = recovery.recover()
    pool, tip_excluded = replay.prepare(events)
    latest = {}
    byid = {g['event_id']: g for g in games}
    for r in ledger:
        k = stamp(r['kickoff'])
        at = k.astimezone(replay.ET).replace(hour=12, minute=0, second=0, microsecond=0)
        if stamp(r['captured_at']) <= at <= k-timedelta(minutes=30):
            if r['event_id'] not in latest or stamp(r['captured_at']) > stamp(latest[r['event_id']]['captured_at']):
                latest[r['event_id']] = r
    rows = []
    for eid, r in latest.items():
        g = byid[eid]
        if g['home'] not in teams or g['away'] not in teams:
            continue
        home, away = teams[g['home']], teams[g['away']]
        k = stamp(r['kickoff'])
        at = k.astimezone(replay.ET).replace(hour=12, minute=0, second=0, microsecond=0)
        key = '|'.join((at.date().isoformat(), *sorted((home, away))))
        related = [x for x in pool if x['event'] == key and abs((x['kickoff']-k).total_seconds()) <= 600]
        authors = {}
        for tip in sorted(related, key=lambda x: (x['observed'], x['id'])):
            if tip['observed'] <= at:
                authors.setdefault(tip['author'], tip)
        opts = {o['selection']: o for o in r['features']['options']}
        try:
            p = [float(opts[s]['final_probability']) for s in ('홈','원정')]
            odds = [float(opts[s]['odds']) for s in ('홈','원정')]
            if any(not math.isfinite(v) for v in p+odds) or min(p)<0 or max(p)>1 or abs(sum(p)-1)>.01 or min(odds)<=1:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            continue
        done = [x for x in related if x['outcome'] in ('W','L')]
        winners = {x['side'] if x['outcome']=='W' else away if x['side']==home else home for x in done}
        winner = next(iter(winners)) if len(winners)==1 else None
        rows.append(dict(id=eid, event=key, date=at.date().isoformat(), sport='bs', league='MLB',
            round_key=f"{g.get('year')}:{g.get('round')}",
            at=at.isoformat(), kickoff=k.isoformat(), snapshot_at=r['captured_at'], snapshot_id=r['snapshot_id'],
            home=home, away=away, odds=odds, model=[v/sum(p) for v in p],
            tipster=[sum(x['side']==home for x in authors.values())/len(authors),
                     sum(x['side']==away for x in authors.values())/len(authors)] if authors else None,
            authors=len(authors), y=(0 if winner==home else 1) if winner else None,
            settled_at=max(x['settled'] for x in done).isoformat() if winner else None,
            result_source='publisher_not_official'))
    # Date/team key cannot distinguish doubleheaders: retain neither ambiguous event.
    counts = Counter(r['event'] for r in rows)
    unique = [r for r in rows if counts[r['event']]==1]
    return unique, dict(commits=commits, recovered=len(ledger), model_candidates=len(latest),
        ambiguous_event_rows=len(rows)-len(unique), extraction_excluded=excluded, tipster_excluded=tip_excluded)


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


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--research-root',type=Path,required=True); ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args(); root=a.research_root
    paths=[root/'recover-hybrid-history-20261025/scripts/recover_hybrid_history.py',
           root/'tipster-replay-20261025/scripts/replay_tipster_opinions.py',
           root/'model-tournament-history-20261025/scripts/compare_saved_tipster_models.py',
           Path('data/raw/picksters/tailslips_pick_events.jsonl'),
           root/'multisport-hybrid-20261025/experiments/multisport-fixed-hybrid-20261025/results.json']
    recovery,replay,pair=[module(n,p) for n,p in zip(('recovery','replay','pair'),paths[:3])]
    events=[json.loads(s) for s in paths[3].read_text(encoding='utf8').splitlines()]
    rows,audit=collect(recovery,replay,pair.TEAMS,events)
    cutoff=max(stamp(r['observed_at']) for r in events)
    paired=[r for r in rows if r['authors']>=1]
    strict=[r for r in rows if r['authors']>=3]
    other=json.loads(paths[4].read_text(encoding='utf8'))['ledger']
    normalized=[dict(id=r['url'],sport=r['sport'],model=r['probability'],
        tipster=[float(i==r['choices']['tipster_only']) for i in range(len(r['probability']))],
        odds=r['odds'],y=r['y']) for r in other]
    report=dict(status='development_only_no_promotion',monthly_loss_limit_krw=LIMIT,stake_krw=STAKE,
        protocol=['Fixed p>=.55, odds>=1.50, no upper cap; probability/positive-edge/rounded-pair-edge selectors.',
          'One ticket/day at noon ET; weekly2/3; KST placement month; all pending exposure reserved.',
          'Pair edge uses independence as an unverified assumption; scores not calibrated probabilities.',
          'Historical quote carry-forward and publisher grades not independently certified.',
          'One-author and three-author cohorts reported separately; no outcome filter before picking.',
          'Multisport backfill is a convenience sample with reconstructed baseline; no trustworthy ticket timing.',
          'Tiny, previously explored partial months; no winning policy promoted.'],
        hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths+[Path(__file__)]},
        audit=audit,coverage=dict(all_model=len(rows),one_author=len(paired),three_authors=len(strict),
                                unresolved_one_author=sum(r['y'] is None for r in paired)),
        mlb_one_author=compare(paired,cutoff),mlb_three_authors=compare(strict,cutoff),
        all_model_opportunities_diagnostic=compare(rows,cutoff)['model'],
        multisport_backfill={s:compare([r for r in normalized if r['sport']==s]) for s in ('bs','bk','sc','vl')},
        opportunity_ledger=rows)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf8') as f:json.dump(report,f,ensure_ascii=False,indent=2)
    print(json.dumps(report['coverage']))
    for arm,policies in report['mlb_one_author'].items():
        for policy,r in policies.items():
            print(arm,policy,r['recommendations'],r['single_roi_pct'],
                  {w:{k:t[k] for k in ('tickets','settled','net_krw','roi_pct','pending','max_drawdown_krw')} for w,t in r['paper'].items()})


if __name__=='__main__':main()
