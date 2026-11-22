"""Recover pregame model artifacts from Git, conservatively dated by commit time.

Dependencies are explicit local research checkouts; their hashes are recorded.
Does not alter the frozen ledger or operational files.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import math


def pair_primary(ledger,games,pool,replay,teams):
    """Three-arm comparison requires production probability, not optional score model."""
    byid={g['event_id']:g for g in games}
    latest={}
    excluded=Counter()
    for r in ledger:
        k=replay.stamp(r['kickoff'])
        at=k.astimezone(replay.ET).replace(hour=12,minute=0,second=0,microsecond=0)
        if replay.stamp(r['captured_at'])<=at<=k-timedelta(minutes=30):
            old=latest.get(r['event_id'])
            if old is None or replay.stamp(old['captured_at'])<replay.stamp(r['captured_at']):
                latest[r['event_id']]=r
    rows=[]
    for eid,r in latest.items():
        g=byid[eid]
        if g['home'] not in teams or g['away'] not in teams:
            excluded['team_mapping']+=1;continue
        home,away=teams[g['home']],teams[g['away']]
        k=replay.stamp(r['kickoff']); at=k.astimezone(replay.ET).replace(hour=12,minute=0,second=0,microsecond=0)
        date=at.date().isoformat();event='|'.join((date,*sorted((home,away))))
        related=[x for x in pool if x['event']==event and abs((x['kickoff']-k).total_seconds())<=600]
        authors={}
        for x in sorted(related,key=lambda x:(x['observed'],x['id'])):
            if x['observed']<=at:authors.setdefault(x['author'],x)
        if len(authors)<3:
            excluded['fewer_than_three_authors']+=1;continue
        opts={o['selection']:o for o in r['features']['options']}
        try:
            p=float(opts['홈']['final_probability']);q=float(opts['원정']['final_probability'])
            odds=[float(opts[s]['odds']) for s in ('홈','원정')]
            if not all(math.isfinite(v) for v in [p,q,*odds]) or not 0<=p<=1 or not 0<=q<=1 or abs(p+q-1)>.01 or min(odds)<=1:
                raise ValueError()
        except (TypeError,ValueError,KeyError):
            excluded['invalid_production_probability_or_odds']+=1;continue
        done=[x for x in related if x['outcome'] in ('W','L')]
        winners={x['side'] if x['outcome']=='W' else away if x['side']==home else home for x in done}
        if len(winners)!=1:
            excluded['missing_or_conflicting_outcome']+=1;continue
        rows.append(dict(event_id=eid,event=event,date=date,home=home,away=away,at=at.isoformat(),
            snapshot_at=r['captured_at'],snapshot_id=r['snapshot_id'],kickoff=k.isoformat(),odds=odds,
            authors=len(authors),home_won=next(iter(winners))==home,
            arms=dict(production=p,tipster=sum(x['side']==home for x in authors.values())/len(authors)),
            settled_at=max(x['settled'] for x in done).isoformat(),result_source='publisher_not_official'))
    return sorted(rows,key=lambda x:x['event']),dict(excluded),len(latest)


def module(path, name):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def kickoff(game):
    m=re.fullmatch(r'(\d{2})\.(\d{2})\([^)]*\) (\d{2}):(\d{2})',game['date'])
    if not m:
        raise ValueError('unrecognized kickoff')
    return datetime(game['year'],*map(int,m.groups()),tzinfo=timezone(timedelta(hours=9)))


def recover():
    lines=subprocess.check_output(['git','log','--all','--since=2026-08-28',
        '--until=2026-09-06','--format=%H %cI','--','docs/data/picks_v2.json'],text=True).splitlines()
    ledger=[]
    games={}
    excluded=Counter()
    for line in lines:
        sha,when=line.split()
        captured=datetime.fromisoformat(when)
        d=json.loads(subprocess.check_output(['git','show',sha+':docs/data/picks_v2.json']))
        generated=datetime.fromisoformat(d['generated_at'].replace('Z','+00:00'))
        for g in d.get('live',[]):
            if g.get('league')!='MLB' or not g.get('event_id'):
                continue
            try:
                start=kickoff(g)
            except (ValueError,KeyError):
                excluded['unparseable_kickoff']+=1
                continue
            if max(generated,captured)>start-timedelta(minutes=30):
                excluded['not_pregame']+=1
                continue
            options=[]
            for o in g.get('options',[]):
                if o.get('market')=='승패' and not o.get('label'):
                    options.append(dict(market='승패',label='',selection=o.get('선택'),odds=o.get('배당'),
                        final_probability=o.get('최종확률'),market_probability=o.get('시장확률'),
                        score_model_probability=o.get('모델확률')))
            if len(options)!=2:
                excluded['missing_two_way_options']+=1
                continue
            games[g['event_id']]=g
            ledger.append(dict(event_id=g['event_id'],captured_at=max(generated,captured).isoformat(),
                as_of=generated.isoformat(),kickoff=start.isoformat(),snapshot_id=sha,
                features=dict(options=options)))
    return ledger,list(games.values()),dict(excluded),len(lines)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--replay',type=Path,required=True)
    p.add_argument('--paired',type=Path,required=True)
    p.add_argument('--search',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    replay=module(a.replay,'recovery_replay')
    pair=module(a.paired,'recovery_pair')
    hybrid=module(a.search,'recovery_search')
    ledger,games,exc,n=recover()
    tip_path=Path('data/raw/picksters/tailslips_pick_events.jsonl')
    pool,_=replay.prepare([json.loads(s) for s in tip_path.read_text(encoding='utf8').splitlines()])
    rows,excluded,count=pair_primary(ledger,games,pool,replay,pair.TEAMS)
    ranking=sorted([dict(spec=s,metrics=hybrid.measure(rows,s)) for s in hybrid.candidates()],
        key=lambda r: (-(r['metrics']['roi_pct'] or 0),r['spec']['name']))
    two_leg={}
    # Fixed baselines/50:50 only, never backselect best historical combination.
    specs=[hybrid.candidates()[0],hybrid.candidates()[1],
           dict(name='fixed50',kind='linear',weight=.5,shrink=0,gate=False)]
    for spec in specs:
        choices=[]
        for r in rows:
            home=hybrid.probability(r,spec)>=.5
            choices.append(dict(id=r['event_id'],event=r['event'],author=spec['name'],
                side=r['home'] if home else r['away'],date=r['date'],
                observed=replay.stamp(r['at']),kickoff=replay.stamp(r['kickoff']),
                odds=r['odds'][0 if home else 1],outcome='W' if home==r['home_won'] else 'L',
                settled=replay.stamp(r['settled_at'])))
        if rows:
            cutoff=max(replay.stamp(r['settled_at']) for r in rows)
            two_leg[spec['name']]={str(w):replay.simulate(choices,cutoff,'lower_odds',w) for w in (2,3)}
    result=dict(commits_scanned=n,recovered_snapshots=len(ledger),model_events=count,
        extraction_exclusions=exc,pairing_exclusions=excluded,paired_n=len(rows),paired_rows=rows,
        guarded_search=hybrid.search(rows),exploratory_IN_SAMPLE_ranking=ranking,
        exploratory_fixed_two_leg=two_leg,
        hashes={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in (a.replay,a.paired,a.search,tip_path)},
        limitations=['Git commit timestamps are repository evidence, not independent timestamp certification.',
          'Conservative capture=max(commit timestamp, artifact generated_at). All snapshots pregame.',
          'Noon quote availability is not proven; publisher outcomes not independently official verified.',
          'Resolved paired subset has missingness bias; no population or future ROI claim.',
          'In-sample ranking is retrospective exploration, NOT a deployable winner.',
          'Monthly loss limit remains 50000 KRW; no production changes.'])
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2,default=str)
    print(json.dumps({k:v for k,v in result.items() if k not in ('paired_rows','hashes','exploratory_IN_SAMPLE_ranking','exploratory_fixed_two_leg')},ensure_ascii=False))
    print('top5',ranking[:5])
    print('baselines',[(s['name'],hybrid.measure(rows,s)) for s in specs])
    print('paper',{s:{w:{k:v for k,v in x.items() if k!='ticket_log'} for w,x in ws.items()} for s,ws in two_leg.items()})


if __name__=='__main__':main()
