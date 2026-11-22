"""Assumption-labelled retrospective replay; not a production-model backtest.

Normalize stake suffix only. Never fill missing pregame odds from results.
All eligible candidates, including unresolved ones, participate in selection.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
KST = timezone(timedelta(hours=9))
STAKE, LIMIT = 10000, 50000
MLB = set('ARI ATL BAL BOS CHC CWS CIN CLE COL DET HOU KC LAA LAD MIA MIL MIN NYM NYY OAK ATH PHI PIT SD SEA SF STL TB TEX TOR WSH'.split())


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('timezone required')
    return result


def canonical(row):
    selection = re.sub(r'\s+\d+(?:\.\d+)?u$', '', row.get('selection', ''), flags=re.I)
    return (row.get('slate_date'), row.get('team_a'), row.get('team_b'),
            row.get('slug'), row.get('market_type'), selection.casefold())


def prepare(events):
    first, finals = {}, defaultdict(list)
    for r in sorted(events, key=lambda r: stamp(r['observed_at'])):
        first.setdefault(r['pick_id'], r)
        if r.get('result') in ('W', 'L', 'P', 'V') and not r.get('is_parlay_leg'):
            finals[canonical(r)].append(r)
    excluded, pool, seen = Counter(), [], set()
    for r in first.values():
        if not (r.get('event_type') == 'first_observed' and r.get('identity_version') == 2
                and r.get('eligible_pre_event') is True and r.get('game_started') is False
                and r.get('result') is None and not r.get('is_parlay_leg')
                and not r.get('is_baseline')):
            excluded['not_pregame_single'] += 1
            continue
        selection = canonical(r)[-1].upper()
        match = re.fullmatch(r'([A-Z]+) ML ([+-]\d{3,4})', selection)
        if (not match or r.get('market_type') != 'moneyline'
                or r.get('team_a') not in MLB or r.get('team_b') not in MLB
                or match[1] not in (r['team_a'], r['team_b'])):
            excluded['not_supported_full_game_MLB_moneyline'] += 1
            continue
        odds = int(match[2])
        if abs(odds) < 100 or r.get('american_odds') != odds:
            excluded['invalid_first_odds'] += 1
            continue
        time_match = re.search(r'\b(\d{1,2}:\d{2})\s+(AM|PM)\s+ET\b', r['game_header'])
        try:
            kickoff = datetime.strptime(r['slate_date']+' '+time_match[1]+' '+time_match[2],
                                       '%Y-%m-%d %I:%M %p').replace(tzinfo=ET)
            observed = stamp(r['observed_at'])
            if observed > kickoff-timedelta(minutes=30):
                raise ValueError('late')
        except (TypeError, ValueError):
            excluded['missing_schedule_or_after_t30'] += 1
            continue
        key = canonical(r)
        if key in seen:
            excluded['duplicate_first_identity'] += 1
            continue
        seen.add(key)
        records = finals[key]
        # Old results on the same displayed date and conflicting grades are ambiguous.
        status, outcome, settled = 'unmatched', None, None
        if records:
            if any(stamp(x['observed_at']) <= max(observed, kickoff) for x in records):
                status = 'ambiguous_earlier_result'
            elif len({x['result'] for x in records}) != 1:
                status = 'conflicting_results'
            else:
                candidate = min(stamp(x['observed_at']) for x in records)
                if candidate <= kickoff+timedelta(hours=36):
                    status, outcome, settled = 'relinked_publisher_result', records[0]['result'], candidate
                else:
                    status = 'result_too_late'
        pool.append(dict(id=r['pick_id'], author=r['slug'], date=r['slate_date'],
            event='|'.join((r['slate_date'], *sorted((r['team_a'], r['team_b'])))),
            side=match[1], observed=observed, kickoff=kickoff,
            odds=1+odds/100 if odds>0 else 1+100/-odds,
            outcome=outcome, settled=settled, linkage=status))
    return pool, dict(excluded)


def opportunities(pool, now, policy):
    # Ambiguity must be known at the decision time, not discovered in future rows.
    schedules = defaultdict(set)
    for r in pool:
        if r['observed'] <= now:
            schedules[r['event']].add(r['kickoff'])
    available = [r for r in pool if r['observed'] <= now <= r['kickoff']-timedelta(minutes=30)
                 and r['kickoff'].astimezone(ET).date() == now.astimezone(ET).date()
                 and len(schedules[r['event']]) == 1]
    # Each author gets at most one vote per event; freeze earliest observation.
    authors = {}
    for r in sorted(available, key=lambda r: (r['observed'],r['id'])):
        authors.setdefault((r['event'],r['author']),r)
    games = defaultdict(list)
    for r in authors.values():
        games[r['event']].append(r)
    choices = []
    for event, rows in games.items():
        counts = Counter(r['side'] for r in rows)
        if policy == 'consensus':
            if len(rows)<3 or len(set(counts.values()))<len(counts):
                continue
            side = max(counts, key=counts.get)
        elif policy == 'lower_odds':
            side = min(rows,key=lambda r:(r['odds'],r['side'],r['id']))['side']
        else:
            raise ValueError('unknown policy')
        # First available quote, not best odds selected retrospectively.
        quote = min((r for r in rows if r['side']==side),key=lambda r:(r['observed'],r['id']))
        choices.append(dict(quote, votes=counts[side], total_votes=len(rows), share=counts[side]/len(rows)))
    if policy == 'consensus':
        choices.sort(key=lambda r:(-r['share'],-r['votes'],r['odds'],r['event']))
    else:
        choices.sort(key=lambda r:(r['odds'],r['event']))
    return choices


def payout(legs):
    if any(r['outcome']=='L' for r in legs):
        return 0
    product = Decimal(1)
    for r in legs:
        product *= Decimal(str(r['odds'])) if r['outcome']=='W' else Decimal(1)
    return int(product.quantize(Decimal('.01'),rounding=ROUND_DOWN)
               .quantize(Decimal('.1'),rounding=ROUND_CEILING)*STAKE)


def simulate(pool, cutoff, policy, weekly, hour=12):
    if weekly not in (2,3):
        raise ValueError('weekly must be 2 or 3')
    months, weeks, pending, tickets, used = defaultdict(int), Counter(), [], [], set()
    net = peak = drawdown = 0
    def settle(now):
        nonlocal net,peak,drawdown
        for ticket in sorted(pending[:],key=lambda t:t['_settled'] or datetime.max.replace(tzinfo=ET)):
            if ticket['_settled'] is None or ticket['_settled']>now:
                continue
            pending.remove(ticket)
            profit = payout(ticket['_legs'])-STAKE
            ticket['net'] = profit
            months[ticket['month']] += profit
            net += profit
            peak = max(peak,net)
            drawdown = max(drawdown,peak-net)
    dates = sorted({r['date'] for r in pool})
    for day in dates:
        now = datetime.fromisoformat(day).replace(hour=hour,tzinfo=ET)
        if now>cutoff:
            continue
        settle(now)
        local = now.astimezone(KST)
        month, week = local.strftime('%Y-%m'), local.isocalendar()[:2]
        if weeks[week]>=weekly or months[month]-STAKE*(len(pending)+1)<-LIMIT:
            continue
        choices = [r for r in opportunities(pool,now,policy) if r['event'] not in used]
        if len(choices)<2:
            continue
        legs = choices[:2]
        # Never filter candidate picks using availability of future results.
        loss_times = [r['settled'] for r in legs if r['outcome']=='L']
        settled = min(loss_times) if loss_times else max(r['settled'] for r in legs) if all(r['settled'] for r in legs) else None
        ticket = dict(at=now.isoformat(),month=month,ids=[r['id'] for r in legs],
            events=[r['event'] for r in legs],sides=[r['side'] for r in legs],
            odds=[r['odds'] for r in legs],stake=STAKE,
            available_budget_before=LIMIT+months[month]-STAKE*len(pending),
            _legs=legs,_settled=settled)
        pending.append(ticket)
        tickets.append(ticket)
        weeks[week]+=1
        used.update(r['event'] for r in legs)
    settle(cutoff)
    for t in tickets:
        t.pop('_legs')
        t['settled_observed_at'] = t.pop('_settled')
    settled_n = sum('net' in t for t in tickets)
    assert all(v>=-LIMIT for v in months.values())
    return dict(tickets=len(tickets),settled=settled_n,pending=len(pending),
        net_krw=net,pending_stake_krw=STAKE*len(pending),
        worst_case_net_krw=net-STAKE*len(pending),
        roi_pct=100*net/(STAKE*settled_n) if settled_n else None,
        monthly_net_by_placement=dict(months),max_drawdown_krw=drawdown,ticket_log=tickets)


def run(events):
    pool, excluded = prepare(events)
    cutoff = max(stamp(r['observed_at']) for r in events)
    # Conservative descriptive single-pick audit excludes ambiguous game schedules.
    # This retrospective quality filter is NOT used for ticket selection.
    schedules = defaultdict(set)
    for r in pool:
        schedules[r['event']].add(r['kickoff'])
    settled = [r for r in pool if r['outcome'] and len(schedules[r['event']])==1]
    decisive = [r for r in settled if r['outcome'] in ('W','L')]
    pnl = sum(r['odds']-1 if r['outcome']=='W' else -1 if r['outcome']=='L' else 0 for r in settled)
    results = {policy:{str(w):simulate(pool,cutoff,policy,w) for w in (2,3)}
               for policy in ('lower_odds','consensus')}
    return dict(mode='retrospective_relinked_publisher_simulation_NOT_model_incremental_ROI',
        events=len(events),cutoff=cutoff,pregame_ml_candidates=len(pool),excluded=excluded,
        linkage=dict(Counter(r['linkage'] for r in pool)),
        single_flat_stake=dict(n=len(settled),unique_events=len({r['event'] for r in settled}),decisive=len(decisive),
            wins=sum(r['outcome']=='W' for r in decisive),
            hit_rate_pct=100*sum(r['outcome']=='W' for r in decisive)/len(decisive) if decisive else None,
            roi_pct=100*pnl/len(settled) if settled else None),
        primary_noon_ET=results,
        timing_sensitivity={str(h):{p:simulate(pool,cutoff,p,3,h) for p in results} for h in (10,14)},
        monthly_loss_limit_krw=LIMIT,stake_krw=STAKE,model_incremental_roi=None,
        protocol=['First observed full-game ML only; exact original odds retained.',
            'Stake suffix removed for tentative result join; same date/teams/author/market/odds.',
            'Source schedule and publisher results, NOT independently official verified.',
            'No sport field: whitelist MLB abbreviations in MLB source; inferred classification.',
            'Candidates include unresolved picks; each author earliest vote per event.',
            'Two distinct games at noon ET; KST placement month/week, once per day.',
            'Consensus: >=3 authors, no tie; rank share, votes, lower odds, event id.',
            'Lower odds control is NOT our production model.',
            '10/14 ET sensitivity declared without selecting best result for deployment.',
            'Foreign quoted odds under hypothetical Proto rounding; actual purchasability unverified.',
            'Monthly loss budget reserves ALL pending stakes; no +50000 profit stop.',
            'Short partial months, correlated picks and retrospective mapping; no future profit claim.'])


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    raw=args.input.read_bytes()
    result=run([json.loads(s) for s in raw.decode('utf-8').splitlines() if s.strip()])
    result['input_sha256']=hashlib.sha256(raw).hexdigest()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2,default=lambda x:x.isoformat())
    print(json.dumps({k:v for k,v in result.items() if k not in ('primary_noon_ET','timing_sensitivity','protocol')},default=str))
    for p, arms in result['primary_noon_ET'].items():
        for w,r in arms.items():
            print(p,w,json.dumps({k:v for k,v in r.items() if k!='ticket_log'}))
