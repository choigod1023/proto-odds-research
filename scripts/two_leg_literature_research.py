"""Frozen, local-only calibration/ensemble and conditional two-leg experiment.

Consumes normalized, hashed research frames; never imports production or calls
network services. A chronological historical replay is not a fresh holdout.
"""
from __future__ import annotations

import argparse
import copy
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
import gzip
import hashlib
import itertools
import json
import math
from pathlib import Path
import random

KST = timezone(timedelta(hours=9))
TEMPERATURES = (.75, 1., 1.25, 1.5, 2.)
WEIGHTS = (0., .1, .25, .5, .75, 1.)
MODELS = ('market', 'external_raw_direction_reference', 'saved_final_reference',
          'operating_unique_vote_market_reference',
          'fixed_market90_external10', 'market_prior10_actual_votes',
          'temperature_market', 'temperature_fixed90', 'validation_selected_calibrated_pool',
          'league_shrunk_calibrated_pool', 'recent90d_league_shrunk_pool')
RULES = ('forced2', 'conditional2', 'conditional2_strict')
BUDGET = 12000


def dt(value):
    if not isinstance(value,str) or not value:
        raise ValueError('Timestamp string required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timezone required')
    return parsed.astimezone(KST)


def read(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def write(path, value, lines=False):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'wt', encoding='utf-8', newline='\n') as f:
        if lines:
            for row in value:
                f.write(json.dumps(row, ensure_ascii=False, separators=(',', ':'))+'\n')
        else:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write('\n')


def vector(values):
    if any(v is None or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
        return None
    total = sum(values)
    if abs(total-1) > .002:
        return None
    return [v/total for v in values]


def admissible_votes(source, audit):
    documents=defaultdict(list)
    for row in source.get('raw_forecasts', []):
        claimed=row.get('source_reported_published_at')
        if claimed:
            try:
                if dt(claimed)>=dt(source['kickoff']):
                    audit['known_late_publication_forecasts_excluded']+=1
                    continue
            except (ValueError,TypeError):
                audit['publication_time_not_proven']+=1
        else:audit['publication_time_not_proven']+=1
        key=(row.get('document_sha256') or row.get('source_sha256') or
             str((row.get('source'),row.get('author'),row.get('source_url'))))
        documents[key].append(row)
    counts=Counter()
    for rows in documents.values():
        sides={r.get('side') for r in rows}
        if len(sides)!=1:
            audit['conflicting_document_groups_excluded']+=1
            continue
        audit['duplicate_document_forecasts_excluded']+=len(rows)-1
        side=next(iter(sides))
        if side not in ('home','away','draw'):
            audit['unknown_vote_direction_excluded']+=1
            continue
        counts[side]+=1
    return counts


def claimed_time_frames(raw):
    """Separate sensitivity: respect asserted posting times, still no receipt proof.

    Only complete final electorates of actually timestamped pre-T30 forecasts;
    archive prices remain unverified at that time. Never inspect match results.
    """
    out=[];audit=Counter()
    for source in raw:
        if not source['cohort'].startswith('retrospective'):continue
        accepted=[]
        for row in source.get('raw_forecasts', []):
            try:published=dt(row['source_reported_published_at'])
            except (KeyError,ValueError,TypeError):
                audit['unknown_claimed_time_forecasts_excluded']+=1
                continue
            if published>dt(source['kickoff'])-timedelta(minutes=30):
                audit['not_preT30_claimed_forecasts_excluded']+=1
                continue
            accepted.append((published,row))
        if not accepted:
            audit['events_without_timestamped_preT30_forecasts']+=1
            continue
        f=copy.deepcopy(source)
        f['cohort']+='__claimed_time_only'
        f['raw_forecasts']=[r for p,r in accepted]
        f['available_at']=max(dt(source['available_at']),max(p for p,r in accepted)).isoformat()
        f['availability_proven']=False
        f['available_at_semantics']='claimed publication only, not receipt or executable quote proof'
        out.append(f)
    audit['events']=len(out)
    return out,dict(audit)


def prepare(raw):
    """Keep all event revisions, including empty/model-missing invalidations."""
    result, audit = [], Counter()
    for source in raw:
        f = {k: source.get(k) for k in ('cohort', 'event_id', 'snapshot_id', 'league', 'home', 'away',
                                       'kickoff', 'available_at', 'quote_observed_at', 'availability_proven')}
        f['tipster'] = source['cohort'].startswith('retrospective')
        f['voters'] = source.get('voters')
        votes=admissible_votes(source,audit) if f['tipster'] else None
        if f['tipster']:
            f['voters']=sum(votes.values())
            if not f['voters']:audit['events_without_admissible_tipster_vote']+=1
        f['products'] = []
        grouped = defaultdict(list)
        for c in source['choices']:
            if any(c.get(k) in (None, '') for k in ('issue_year', 'round', 'game_no')):
                audit['missing_issued_identity_choices'] += 1
                continue
            key = (c['issue_year'], c['round'], str(c['game_no']), c['market'], str(c.get('line')))
            grouped[key].append(c)
        seen_family = set()
        for key, options in sorted(grouped.items(), key=lambda x: (x[0][0],x[0][1],int(x[0][2]),x[0][3:])):
            if len(options) not in (2, 3):
                audit['invalid_roster_size'] += 1
                continue
            external = 'vote_support' if f['tipster'] else 'saved_score_shadow'
            market = vector([c['scores'].get('market') for c in options])
            ext = (vector([votes[c.get('side', c.get('label'))]/f['voters'] for c in options])
                   if f['tipster'] and f['voters'] else None) if f['tipster'] else vector([c['scores'].get(external) for c in options])
            final=vector([c['scores'].get('saved_final_reference') for c in options])
            if market is None or ext is None:
                audit['missing_common_probability_roster'] += 1
                continue
            if not f['tipster'] and final is None:
                audit['missing_common_final_probability_roster']+=1
                continue
            if not f['tipster'] and not all('saved_score_shadow' in c.get('probability_models', []) for c in options):
                audit['score_unit_contract_not_verified'] += 1
                continue
            # Retrospective duplicate aliases are resolved by issued identity,
            # never by which price or result produces the best return.
            family = (key[3], key[4])
            if f['tipster'] and family in seen_family:
                audit['retrospective_later_alias_rosters_skipped'] += 1
                continue
            seen_family.add(family)
            fullkey = str((source['snapshot_id'], key))
            product = dict(cohort=f['cohort'], event_id=f['event_id'], snapshot_id=f['snapshot_id'],
                           league=f['league'], home=f['home'], away=f['away'], kickoff=f['kickoff'],
                           available_at=f['available_at'], day=dt(f['kickoff']).date().isoformat(),
                           tipster=f['tipster'], voters=f['voters'], issue=list(key[:2]),
                           market=key[3], line=key[4], game_no=key[2],
                           product_id=hashlib.sha256(fullkey.encode()).hexdigest()[:24],
                           market_p=market, external_p=ext,
                           final_p=final,
                           odds=[c['odds'] for c in options],
                           choices=[{k: c.get(k) for k in ('offer_id', 'choice_id', 'selection', 'label', 'hit', 'refund', 'result_status')} for c in options])
            product['void'] = all(c.get('refund') for c in options)
            hits = [c.get('hit') for c in options]
            product['target'] = hits.index(True) if not product['void'] and None not in hits and sum(hits)==1 else None
            f['products'].append(product)
        result.append(f)
    audit['frames'] = len(result)
    audit['common_rosters_all_revisions'] = sum(len(f['products']) for f in result)
    return result, dict(audit)


def available(frame, time):
    if dt(frame['available_at']) > time or dt(frame['kickoff']) < time + timedelta(minutes=30):
        return False
    if not frame.get('home') or not frame.get('away') or not frame.get('league'):
        return False
    if not frame['tipster']:
        quote = frame.get('quote_observed_at')
        if quote is None or not 0 <= (time-dt(quote)).total_seconds() <= 7200:
            return False
    return True


def forecast_rosters(frames):
    latest = {}
    for f in frames:
        if dt(f['available_at']) <= dt(f['kickoff'])-timedelta(minutes=30):
            key = (f['cohort'], f['event_id'], f['kickoff'])
            if key not in latest or (dt(f['available_at']), f['snapshot_id']) > (dt(latest[key]['available_at']), latest[key]['snapshot_id']):
                latest[key] = f
    out = []
    for f in latest.values():
        if available(f, dt(f['kickoff'])-timedelta(minutes=30)):
            out.extend(f['products'])
    return sorted(out, key=lambda x: (x['kickoff'], x['event_id'], x['product_id']))


def temperature(p, value):
    powered = [max(v, 1e-8)**(1/value) for v in p]
    total = sum(powered)
    return [v/total for v in powered]


def pooled(row, weight, temp=1):
    p = [(1-weight)*a+weight*b for a, b in zip(row['market_p'], row['external_p'])]
    return temperature(p, temp) if temp != 1 else p


def loss(rows, weight, temp, cutoff, recent=False):
    counts = Counter(r['event_id'] for r in rows)
    numerator = denominator = 0.
    for r in rows:
        if r['target'] is None:
            continue
        w = 1/counts[r['event_id']]
        if recent:
            age = max(0, (cutoff-dt(r['kickoff'])).total_seconds()/86400)
            w *= 2**(-age/90)
        numerator -= w*math.log(max(pooled(r, weight, temp)[r['target']], 1e-8))
        denominator += w
    return numerator/denominator if denominator else math.inf


def fit(train, validation, train_cutoff, test_cutoff, recent=False):
    train = [r for r in train if r['target'] is not None]
    validation = [r for r in validation if r['target'] is not None]
    result = {'train_n':len(train), 'validation_n':len(validation), 'recent':recent,
              'train_events':len({r['event_id'] for r in train}),
              'validation_events':len({r['event_id'] for r in validation}),
              'train_ids':[r['product_id'] for r in train], 'validation_ids':[r['product_id'] for r in validation],
              'temps':{}, 'weight':None, 'leagues':{}}
    if result['train_events'] < 30:
        return result
    for w in WEIGHTS:
        result['temps'][str(w)] = min(TEMPERATURES, key=lambda t: (loss(train,w,t,train_cutoff,recent),abs(t-1),t))
    if result['validation_events'] < 10:
        return result
    result['weight'] = min(WEIGHTS, key=lambda w: (loss(validation,w,result['temps'][str(w)],test_cutoff,recent),w))
    for league in sorted({r['league'] for r in train+validation}):
        tr = [r for r in train if r['league']==league]
        va = [r for r in validation if r['league']==league]
        tr_n=len({r['event_id'] for r in tr});va_n=len({r['event_id'] for r in va})
        if tr_n<30 or va_n<10:
            result['leagues'][league] = {'fallback_global':True, 'train_n':len(tr), 'validation_n':len(va)}
            continue
        local_temps = {str(w):min(TEMPERATURES,key=lambda t:(loss(tr,w,t,train_cutoff,recent),abs(t-1),t)) for w in WEIGHTS}
        best = min(WEIGHTS, key=lambda w:(loss(va,w,local_temps[str(w)],test_cutoff,recent),w))
        n = va_n
        weight = n/(n+50)*best+50/(n+50)*result['weight']
        global_temp = result['temps'][str(result['weight'])]
        temp = tr_n/(tr_n+100)*local_temps[str(best)]+100/(tr_n+100)*global_temp
        result['leagues'][league] = dict(fallback_global=False, train_n=len(tr), validation_n=len(va),
                                         local_weight=best, weight=weight, temperature=temp)
    return result


def predict(row, model, fitted, recent_fit):
    if model=='market': return row['market_p']
    if model=='operating_unique_vote_market_reference':
        return row['market_p'] if row['tipster'] else None
    if model=='external_raw_direction_reference': return row['external_p']
    if model=='saved_final_reference': return row['final_p']
    if model=='fixed_market90_external10': return pooled(row,.1)
    if model=='market_prior10_actual_votes':
        if not row['tipster'] or not row['voters']: return None
        return [(row['voters']*v+10*m)/(row['voters']+10) for m,v in zip(row['market_p'],row['external_p'])]
    if model.startswith('temperature_'):
        w = 0. if model=='temperature_market' else .1
        temp = fitted['temps'].get(str(w))
        return pooled(row,w,temp) if temp else None
    params = recent_fit if model=='recent90d_league_shrunk_pool' else fitted
    weight = params['weight']
    if weight is None: return None
    temp = params['temps'][str(weight)]
    if model!='validation_selected_calibrated_pool':
        local = params['leagues'].get(row['league'],{})
        if not local.get('fallback_global', True):
            weight,temp = local['weight'],local['temperature']
    return pooled(row,weight,temp)


def price(prices):
    value = math.prod(Decimal(str(x)) for x in prices)
    return value if len(prices)==1 else value.quantize(Decimal('.01'),rounding=ROUND_DOWN).quantize(Decimal('.1'),rounding=ROUND_CEILING)


def settle(legs, singles=False):
    if any(c.get('hit') is None and not c.get('refund') for c in legs): return None
    if singles:
        return float(sum((Decimal(BUDGET)/2*(1 if c.get('refund') else Decimal(str(c['odds'])) if c['hit'] else 0) for c in legs), Decimal(0))-BUDGET)
    if any(not c['hit'] and not c.get('refund') for c in legs): return -BUDGET
    return float(Decimal(BUDGET)*price([1 if c.get('refund') else c['odds'] for c in legs])-BUDGET)


def pick_pair(frames, issue, model, rule, fitted, recent_fit):
    candidates = []
    for f in frames:
        options = []
        for r in f['products']:
            if r['issue'] != issue: continue
            p = predict(r,model,fitted,recent_fit)
            if p is None: continue
            if f['tipster'] and model=='external_raw_direction_reference' and rule!='forced2':
                continue  # Actual vote support does not certify EV.
            for i, probability in enumerate(p):
                if model=='operating_unique_vote_market_reference':
                    votes=r['external_p']; best=max(votes)
                    if votes.count(best)!=1 or votes[i]!=best: continue
                odds = r['odds'][i]
                if odds < 1.01 or probability <= 0: continue
                threshold = 1.03 if rule=='conditional2_strict' else 1.
                if rule!='forced2' and (probability < .55 or probability*odds <= threshold): continue
                c = dict(r['choices'][i], odds=odds, probability=probability, event_id=r['event_id'],
                         product_id=r['product_id'], snapshot_id=r['snapshot_id'], home=r['home'],away=r['away'],
                         kickoff=r['kickoff'], available_at=r['available_at'],issue=r['issue'],game_no=r['game_no'],market=r['market'],line=r['line'])
                options.append(c)
        if options:
            candidates.append(min(options,key=lambda c:(-c['probability'],c['product_id'],str(c.get('selection') or c.get('label')))))
    candidates.sort(key=lambda c:(-c['probability'],c['event_id'],c['product_id']))
    best,score = None,-1
    for a,b in itertools.combinations(candidates[:20],2):
        if a['event_id']==b['event_id']: continue
        teams=[str(x[k]).strip().casefold() for x in (a,b) for k in ('home','away')]
        if len(set(teams))<4: continue
        rank=a['probability']*b['probability']
        if rank>score: best,score=[a,b],rank
    return best


def ticket_sessions(frames, test_days, models, fitted, recent_fit):
    groups = defaultdict(list)
    for f in frames:
        day=dt(f['kickoff']).date().isoformat()
        if day in test_days: groups[(f['league'],day)].append(f)
    out=[]
    for (league,day), revisions in sorted(groups.items()):
        revisions.sort(key=lambda f:(dt(f['available_at']),f['snapshot_id']))
        checkpoints=sorted({datetime.fromtimestamp(math.ceil(dt(f['available_at']).timestamp()/1800)*1800,KST) for f in revisions})
        states,seen,idx={},set(),0
        issues=sorted({tuple(p['issue']) for f in revisions for p in f['products']})
        for checkpoint in checkpoints:
            while idx<len(revisions) and dt(revisions[idx]['available_at'])<=checkpoint:
                f=revisions[idx]; states[(f['event_id'],f['kickoff'])]=f;idx+=1
            usable=[f for f in states.values() if available(f,checkpoint)]
            for issue in issues:
                if issue in seen or pick_pair(usable,list(issue),'market','forced2',fitted,recent_fit) is None: continue
                seen.add(issue)
                policies={}
                for model in models:
                    for rule in RULES:
                        if model=='external_raw_direction_reference' and revisions[0]['tipster'] and rule!='forced2':continue
                        key=model+'|'+rule
                        legs=pick_pair(usable,list(issue),model,rule,fitted,recent_fit)
                        entry=dict(model=model,rule=rule,selected=legs is not None,stake=BUDGET if legs else 0,
                                   net=settle(legs) if legs else 0,legs=legs or [],
                                   quoted_odds=float(price([x['odds'] for x in legs])) if legs else None,
                                   same_legs_single_net=settle(legs,True) if legs else 0,
                                   hit=all(x['hit'] or x.get('refund') for x in legs) if legs and all(x.get('hit') is not None or x.get('refund') for x in legs) else None)
                        policies[key]=entry
                out.append(dict(league=league,day=day,issue=list(issue),placement=checkpoint.isoformat(),policies=policies,
                                common_results_complete=all(x['net'] is not None for x in policies.values())))
    return out


def forecast_metrics(rows,model,fitted,recent_fit):
    valid=[(r,predict(r,model,fitted,recent_fit)) for r in rows if r['target'] is not None]
    valid=[(r,p) for r,p in valid if p is not None]
    tied=0
    if model=='operating_unique_vote_market_reference':
        tied=sum(r['external_p'].count(max(r['external_p']))>1 for r,p in valid)
        valid=[(r,p) for r,p in valid if r['external_p'].count(max(r['external_p']))==1]
    if not valid:return dict(markets=0,events=0)
    count=Counter(r['event_id'] for r,p in valid)
    totals=defaultdict(float)
    for r,p in valid:
        w=1/count[r['event_id']]; y=r['target']
        pred=max(range(len(p)),key=lambda i:p[i])
        if model=='operating_unique_vote_market_reference':
            votes=r['external_p']; best=max(votes)
            if votes.count(best)!=1:
                continue
            pred=votes.index(best)
        totals['logloss']-=w*math.log(max(p[y],1e-8))
        totals['brier']+=w*sum((v-(i==y))**2 for i,v in enumerate(p))
        totals['accuracy']+=w*(pred==y)
        totals['weight']+=w
    if totals['weight']==0:return dict(markets=0,events=0)
    return dict(markets=len(valid),events=len(count),accuracy_pct=100*totals['accuracy']/totals['weight'],
                brier=totals['brier']/totals['weight'],logloss=totals['logloss']/totals['weight'],
                support_sha256=hashlib.sha256('\n'.join(sorted(r['product_id'] for r,p in valid)).encode()).hexdigest(),
                skipped_vote_ties=tied,
                probability_semantics='market vector; selection is unique vote, not market argmax' if model=='operating_unique_vote_market_reference' else 'research probability/reference vector; unverified calibration')


def policy_metrics(sessions,key):
    complete=[s for s in sessions if s['common_results_complete'] and key in s['policies']]
    selected=[s for s in complete if s['policies'][key]['selected']]
    if not complete:return dict(opportunities=0,tickets=0,roi_pct=None)
    profits=[s['policies'][key]['net'] for s in selected]
    net=sum(profits);daily=defaultdict(float);monthly=defaultdict(float)
    streak=maxstreak=0
    for s in selected:
        profit=s['policies'][key]['net'];daily[s['day']]+=profit;monthly[s['day'][:7]]+=profit
        streak=streak+1 if profit<0 else 0;maxstreak=max(maxstreak,streak)
    equity=peak=dd=0
    for day in sorted(daily):
        equity+=daily[day];peak=max(peak,equity);dd=max(dd,peak-equity)
    return dict(opportunities=len(complete),pending_opportunities=len(sessions)-len(complete),tickets=len(selected),
                ticket_dates=len(daily),coverage_pct=100*len(selected)/len(complete),net_krw=net,
                roi_pct=100*net/(len(selected)*BUDGET) if selected else None,
                session_budget_return_pct=100*net/(len(complete)*BUDGET),
                hit_pct=100*sum(s['policies'][key]['hit'] for s in selected)/len(selected) if selected else None,
                component_single_roi_pct=100*sum(s['policies'][key]['same_legs_single_net'] for s in selected)/(len(selected)*BUDGET) if selected else None,
                daily_close_drawdown_krw=dd,max_losing_streak=maxstreak,
                profitable_months=sum(v>0 for v in monthly.values()),months=len(monthly))


def bootstrap(sessions, contender, reference):
    blocks=defaultdict(list)
    placed=[]
    for s in sessions:
        if not s['common_results_complete'] or contender not in s['policies'] or reference not in s['policies']:continue
        a,b=s['policies'][contender],s['policies'][reference]
        blocks[s['day']].append(a['net']-b['net'])
        if a['selected'] and b['selected']:placed.append((a['net']-b['net'])/BUDGET)
    result=dict(dates=len(blocks),common_placed_sessions=len(placed),common_placed_delta_roi_pp=100*sum(placed)/len(placed) if placed else None)
    if len(blocks)<10:
        result['ci95_cash_inclusive_delta_pp']=None;return result
    rng=random.Random(20261030); vals=[];values=list(blocks.values())
    for _ in range(1000):
        sample=rng.choices(values,k=len(values))
        vals.append(100*sum(sum(x)for x in sample)/(BUDGET*sum(len(x) for x in sample)))
    vals.sort();result['ci95_cash_inclusive_delta_pp']=[vals[24],vals[974]]
    return result


def forecast_delta(rows, model, fitted, recent_fit):
    paired=[]
    for r in rows:
        if r['target'] is None:continue
        p=predict(r,model,fitted,recent_fit)
        q=predict(r,'fixed_market90_external10',fitted,recent_fit)
        if p is None or q is None:continue
        votes=r['external_p']
        if model=='operating_unique_vote_market_reference' and votes.count(max(votes))!=1:continue
        pred=votes.index(max(votes)) if model=='operating_unique_vote_market_reference' else max(range(len(p)),key=p.__getitem__)
        ref=max(range(len(q)),key=q.__getitem__);y=r['target']
        paired.append((r,p,q,pred,ref,y))
    counts=Counter(r['event_id'] for r,*_ in paired);blocks=defaultdict(lambda:[0.,0.,0.,0.])
    for r,p,q,pred,ref,y in paired:
        w=1/counts[r['event_id']];b=blocks[r['day']]
        b[0]+=w*((pred==y)-(ref==y))
        b[1]+=w*(sum((v-(i==y))**2 for i,v in enumerate(p))-sum((v-(i==y))**2 for i,v in enumerate(q)))
        b[2]+=w*(math.log(max(q[y],1e-8))-math.log(max(p[y],1e-8)))
        b[3]+=w
    if not blocks:return dict(markets=0,events=0,dates=0)
    values=list(blocks.values());total=sum(x[3]for x in values)
    means=[sum(x[i]for x in values)/total for i in range(3)]
    result=dict(markets=len(paired),events=len(counts),dates=len(blocks),accuracy_delta_pp=100*means[0],
                brier_delta=means[1],logloss_delta=means[2],ci95=None)
    if len(blocks)>=10:
        rng=random.Random(20261030);samples=[[],[],[]]
        for _ in range(1000):
            chosen=rng.choices(values,k=len(values));den=sum(x[3]for x in chosen)
            for i in range(3):samples[i].append(sum(x[i]for x in chosen)/den)
        for s in samples:s.sort()
        result['ci95']={name:[samples[i][24]*(100 if i==0 else 1),samples[i][974]*(100 if i==0 else 1)]
                         for i,name in enumerate(('accuracy_delta_pp','brier_delta','logloss_delta'))}
    return result


def run(frames, outdir):
    rosters=forecast_rosters(frames)
    records=[];predictions=[];all_sessions=[];fits=[]
    for cohort in sorted({f['cohort'] for f in frames}):
        rf=[f for f in frames if f['cohort']==cohort]
        rr=[r for r in rosters if r['cohort']==cohort]
        dates=sorted({r['day'] for r in rr})
        if len(dates)<3:continue
        for fractions in ((.6,.2,.2),(.5,.2,.3)):
            a=max(1,int(len(dates)*fractions[0]));b=max(a+1,int(len(dates)*(fractions[0]+fractions[1])))
            if b>=len(dates):continue
            train_days,validation_days,test_days=dates[:a],dates[a:b],dates[b:]
            first_val=min(dt(f['available_at']) for f in rf if dt(f['kickoff']).date().isoformat() in validation_days)
            first_test=min(dt(f['available_at']) for f in rf if dt(f['kickoff']).date().isoformat() in test_days)
            train=[r for r in rr if r['day'] in train_days and dt(r['kickoff'])<first_val-timedelta(hours=48)]
            va=[r for r in rr if r['day'] in validation_days and dt(r['kickoff'])<first_test-timedelta(hours=48)]
            test=[r for r in rr if r['day'] in test_days]
            fitted=fit(train,va,first_val,first_test)
            recent=fit(train,va,first_val,first_test,True)
            models=[m for m in MODELS if any(predict(r,m,fitted,recent) is not None for r in test)]
            split='-'.join(str(round(f*100)) for f in fractions)
            fits.append(dict(cohort=cohort,split=split,date_range=[dates[0],dates[-1]],train_days=train_days,validation_days=validation_days,test_days=test_days,
                             result_receipt_times_verified=False,strict_asof_training_certified=False,
                             first_validation_decision=first_val.isoformat(),first_test_decision=first_test.isoformat(),train=fitted,recent=recent,
                             test_markets=len(test),test_events=len({r['event_id']for r in test}),models=models))
            sessions=ticket_sessions(rf,set(test_days),models,fitted,recent)
            for s in sessions:s.update(cohort=cohort,split=split)
            all_sessions.extend(sessions)
            for league in ['ALL']+sorted({r['league'] for r in test}):
                subset=[r for r in test if league=='ALL' or r['league']==league]
                ss=[s for s in sessions if league=='ALL' or s['league']==league]
                for model in models:
                    metric=forecast_metrics(subset,model,fitted,recent)
                    policies={rule:policy_metrics(ss,model+'|'+rule) for rule in RULES
                              if any(model+'|'+rule in s['policies']for s in ss)}
                    records.append(dict(cohort=cohort,split=split,league=league,model=model,forecast=metric,policies=policies,
                                        common_unique_vote_forecast=forecast_metrics([r for r in subset if r['external_p'].count(max(r['external_p']))==1],model,fitted,recent) if subset and subset[0]['tipster'] else None,
                                        paired_forecast_vs_fixed90=forecast_delta(subset,model,fitted,recent),
                                        uncertainty_vs_fixed90_forced2=bootstrap(ss,model+'|conditional2','fixed_market90_external10|forced2')))
            for r in test:
                for model in models:
                    p=predict(r,model,fitted,recent)
                    if p is not None:
                        predictions.append(dict(cohort=cohort,split=split,model=model,product_id=r['product_id'],event_id=r['event_id'],
                                                league=r['league'],kickoff=r['kickoff'],p=p,target=r['target'],void=r['void']))
    write(outdir/'normalized-frames.jsonl.gz',frames,True)
    write(outdir/'forecast-rosters.jsonl.gz',rosters,True)
    write(outdir/'heldout-predictions.jsonl.gz',predictions,True)
    write(outdir/'ticket-sessions.jsonl.gz',all_sessions,True)
    write(outdir/'fits.json',fits)
    write(outdir/'results.json',records)
    return dict(forecast_rosters=len(rosters),result_rows=len(records),fits=len(fits),sessions=len(all_sessions))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--inputs',nargs='+',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--claimed-time-only',action='store_true',help='Separate retrospective sensitivity respecting asserted publication times, not receipt proof')
    args=parser.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    raw=[];sources=[]
    for name in args.inputs:
        path=Path(name);raw.extend(read(path))
        sources.append(dict(path=path.as_posix(),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    timing_audit=None
    if args.claimed_time_only:
        raw,timing_audit=claimed_time_frames(raw)
        write(out/'claimed-time-audit.json',timing_audit)
    frames,audit=prepare(raw)
    counts=run(frames,out)
    write(out/'manifest.json',dict(inputs=sources,preparation=audit,counts=counts,production_changed=False,
                                   claimed_time_sensitivity=args.claimed_time_only,claimed_time_audit=timing_audit,
                                   evidence_scope='retrospective chronological replay; result receipt timestamps unverified',
                                   result_receipt_times_verified=False,strict_asof_training_certified=False,
                                   code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    print(json.dumps(counts))


if __name__=='__main__':main()
