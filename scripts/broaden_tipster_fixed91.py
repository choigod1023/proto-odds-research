"""Fixed-weight temporal expansion using authenticated read-only ledger exports."""
import argparse
from collections import Counter,defaultdict
from datetime import datetime,timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import random
import sys
import requests

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_decision import event_id,_stable_id
from matches import clean_team
from matched_tipster_source import prepare,stamp,ET,KST
from expand_tipster_fixed91 import (TEAMS,link,compare_fixed,validate_official_timing,
                                   frozen_budget_tickets)
from matched_tipster_replay import recommend
from verify_tipster_mlb_results import reconcile,final_tickets


def read_verified(path):
    manifest=json.loads(path.with_suffix('.manifest.json').read_text())
    raw=path.read_bytes()
    if not manifest.get('verified_complete') or hashlib.sha256(raw).hexdigest()!=manifest['sha256']:
        raise ValueError('Unverified export')
    rows=[json.loads(line) for line in raw.splitlines()]
    if len(rows)!=manifest['rows']: raise ValueError('Row count mismatch')
    return rows,manifest


def fixtures_by_id(fixtures):
    mapping={}
    for r in fixtures:
        if r['market_family']!='승패': continue
        home,away=clean_team(r['home']),clean_team(r['away'])
        if home not in TEAMS or away not in TEAMS: continue
        game=dict(sport=r['sport'],year=int(r['year']),date=r['date_text'],
                  league=r['league'],home=home,away=away,
                  source_event_id=r.get('source_event_id'),match_id=r.get('match_id'))
        key=event_id(game)
        parts=re.fullmatch(r'(\d{2})\.(\d{2})\([^)]*\) (\d{2}):(\d{2})',game['date'])
        if not parts: continue
        kickoff=datetime(game['year'],*map(int,parts.groups()),tzinfo=KST)
        value=dict(id=key,home=TEAMS[home],away=TEAMS[away],kickoff=kickoff.isoformat(),
                   date=kickoff.astimezone(ET).date().isoformat(),round_key=f"{r['year']}:{r['round']}",
                   offer_candidates=sorted(set(mapping.get(key,{}).get('offer_candidates',[])+[(int(r['round']),str(r['game_no']))])))
        if key in mapping and any(mapping[key][k]!=value[k] for k in ('home','away','kickoff')):
            raise ValueError('Fixture identity conflict')
        # Round is not in the event hash. Conflicting reissue round blocks pairing.
        if key in mapping and mapping[key]['round_key']!=value['round_key']:
            value['round_key']=None
        mapping[key]=value
    return mapping


def historical_round(fixture,options):
    # The archived offer ID itself commits to round and game number. Later
    # reissues cannot change which candidate reproduces this exact hash.
    matched=[]
    for o in options:
        candidates={rnd for rnd,number in fixture['offer_candidates']
                    if o.get('selection_id') and o.get('offer_id')==_stable_id('off',o['selection_id'],rnd,number)}
        if len(candidates)!=1: return None
        matched.append(next(iter(candidates)))
    return str(matched[0]) if len(set(matched))==1 else None


def recover_ledger(predictions,fixtures):
    lookup=fixtures_by_id(fixtures);rows=[];excluded=Counter()
    for p in predictions:
        fixture=lookup.get(p['event_id'])
        if fixture is None: excluded['not_in_MLB_fixture_map']+=1;continue
        if abs((stamp(p['kickoff'])-stamp(fixture['kickoff'])).total_seconds())>1:
            excluded['kickoff_mismatch']+=1;continue
        try:
            capture=max(stamp(p[k]) for k in ('captured_at','as_of','market_observed_at'))
            opts=[o for o in p['options'] if o['market']=='승패' and not o.get('label')]
            sides={o['selection']:o for o in opts}
            if len(opts)!=2 or set(sides)!={'홈','원정'}: raise ValueError()
            probs=[float(sides[s]['final_probability']) for s in ('홈','원정')]
            odds=[float(sides[s]['odds']) for s in ('홈','원정')]
            if not all(math.isfinite(x) for x in probs+odds) or min(probs)<0 or max(probs)>1 or abs(sum(probs)-1)>.01 or min(odds)<=1: raise ValueError()
        except (TypeError,KeyError,ValueError): excluded['invalid_prediction']+=1;continue
        if capture>stamp(p['kickoff'])-timedelta(minutes=30):
            excluded['captured_after_T30']+=1;continue
        verified_round=historical_round(fixture,opts)
        rows.append(dict(fixture,round_key=f"{stamp(p['kickoff']).astimezone(KST).year}:{verified_round}" if verified_round else None,
                         round_provenance='archived_offer_id_exact_hash' if verified_round else 'unverified_excluded_from_pairs',
                         archived_offers=[{k:o.get(k) for k in ('offer_id','selection_id')} for o in opts],
                         snapshot_at=capture.isoformat(),snapshot_id=p['snapshot_id'],
                         record_hash=p['record_hash'],ledger_sequence=p['ledger_sequence'],
                         model=[v/sum(probs) for v in probs],odds=odds,
                         final_equals_market=all(sides[s]['final_probability']==sides[s].get('market_probability') for s in sides)))
    return rows,dict(fixture_ids=len(lookup),prediction_records=len(predictions),
                     usable_snapshots=len(rows),exclusions=dict(excluded))


def evaluate(rows):
    result=compare_fixed(rows)
    for name,w in [('operating',1.),('hybrid91',.9),('tipster',0.)]:
        b=[dict(r,model=[w*p+(1-w)*q for p,q in zip(r['model'],r['tipster'])]) for r in rows]
        if rows:
            result[name]['brier_home']=sum((r['model'][0]-int(r['y']==0))**2 for r in b)/len(b)
            result[name]['log_loss']=sum(-math.log(max(1e-15,r['model'][r['y']])) for r in b)/len(b)
        recs=[r for r in recommend(b,'model','probability') if r['round_key'] is not None]
        simulations={name:dict(policies={'probability':dict(paper={str(n):frozen_budget_tickets(recs,n,stamp('2026-10-01T00:00:00Z')) for n in (2,3)})})}
        result[name]['conservative_paper']=final_tickets(simulations,rows)[name]['probability']
        result[name]['paper_budget_basis']='Purchase-month total stakes <=50000 KRW; no profit recycling. Not settlement-month loss or exact net-loss-stop replay.'
    return result


def paired_day_delta(rows):
    days=defaultdict(list)
    for r in rows:
        operating=max(range(2),key=lambda i:r['model'][i])
        hybrid=max(range(2),key=lambda i:.9*r['model'][i]+.1*r['tipster'][i])
        delta=(r['odds'][hybrid] if hybrid==r['y'] else 0)-(r['odds'][operating] if operating==r['y'] else 0)
        days[r['date']].append(delta)
    if not days: return None
    groups=list(days.values());rng=random.Random(20261025);samples=[]
    for _ in range(5000):
        selected=rng.choices(groups,k=len(groups))
        samples.append(100*sum(sum(g) for g in selected)/sum(len(g) for g in selected))
    samples.sort()
    return dict(days=len(groups),roi_delta_percentage_points=100*sum(sum(g) for g in groups)/len(rows),
                descriptive_day_bootstrap_95pct=[samples[124],samples[4874]],
                hybrid_better_days=sum(sum(g)>1e-10 for g in groups),
                operating_better_days=sum(sum(g)<-1e-10 for g in groups),
                tied_days=sum(abs(sum(g))<=1e-10 for g in groups),
                caveat='Descriptive uncertainty only, not corrected for earlier model selection or serial dependence.')


def run(directory,prior,output):
    events,em=read_verified(directory/'tipsters-complete.jsonl')
    predictions,pm=read_verified(directory/'predictions.jsonl')
    fixtures,fm=read_verified(directory/'fixtures.jsonl')
    om=None
    if (directory/'odds-fixtures.manifest.json').exists():
        extra,om=read_verified(directory/'odds-fixtures.jsonl');fixtures+=extra
    pool,tip_exclusions=prepare(events)
    snapshots,audit=recover_ledger(predictions,fixtures)
    rows,coverage=link(snapshots,pool,'t30')
    if not rows: raise ValueError('No matching events')
    sources={}
    for name,endpoint,params in [('teams','teams',dict(sportId=1)),('schedule','schedule',dict(sportId=1,startDate=min(r['date'] for r in rows),endDate=max(r['date'] for r in rows)))]:
        response=requests.get('https://statsapi.mlb.com/api/v1/'+endpoint,params=params,timeout=45)
        response.raise_for_status();sources[name]=dict(url=response.url,sha256=hashlib.sha256(response.content).hexdigest(),data=response.json())
    official,settlement=reconcile(rows,sources['teams']['data'],sources['schedule']['data'])
    validate_official_timing(official)
    previous=json.loads(prior.read_text(encoding='utf8'))['results']['t30']['ledger']
    oldids={r['id'] for r in previous}
    new=[r for r in official if r['id'] not in oldids]
    later=[r for r in official if r['date']>max(x['date'] for x in previous)]
    expanded=sorted(previous+new,key=lambda r:(r['at'],r['id']))
    validate_official_timing(expanded)
    result=dict(protocol='Fixed 90:10; all games T-30, one earliest eligible vote per author; no weight tuning.',
                export_manifests=dict(events=em,predictions=pm,fixtures=fm,odds_fixtures=om),sources=sources,
                prior_sha256=hashlib.sha256(prior.read_bytes()).hexdigest(),recovery=audit,coverage=coverage,
                eligible_tips=len(pool),tip_exclusions=tip_exclusions,official_audit=settlement,
                total=len(official),new_events=len(new),later_events=len(later),
                dates=sorted({r['date'] for r in official}),authors=len({t['author'] for r in official for t in r['tipster_evidence']}),
                final_equals_market=sum(r['final_equals_market'] for r in official),
                comparison_all=evaluate(official),comparison_new=evaluate(new),comparison_later=evaluate(later),ledger=official,
                expanded_total=len(expanded),expanded_ledger=expanded,comparison_expanded=evaluate(expanded),
                later_paired_delta=paired_day_delta(later),expanded_paired_delta=paired_day_delta(expanded),
                expansion_rule='Keep previous 64 frozen rows unchanged; append only previously unseen IDs. Overlap is not counted twice.',
                limitations=['Historical extension is not untouched prospective validation.',
                  'Author votes are not calibrated probabilities and authors may copy each other.',
                  'Fixture results were used only for identity; outcomes come from official MLB API after picks were fixed.',
                  'Monthly total stakes <=50000, no recycling, stricter than net-loss stop; not monthly income evidence.',
                  'Stored probabilities may be identical to market probabilities; not a claim of trained-model superiority.',
                  'Production ledger retention/coverage and fixture mapping can limit representativeness. No production promotion.'])
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:result[k] for k in ('total','expanded_total','new_events','later_events','dates','authors','final_equals_market','recovery','coverage')},ensure_ascii=False))
    for group in ('comparison_all','comparison_new','comparison_later','comparison_expanded'):
        print(group,json.dumps({k:v['single'] for k,v in result[group].items()}))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--directory',type=Path,required=True);ap.add_argument('--prior',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();run(a.directory,a.prior,a.output)
