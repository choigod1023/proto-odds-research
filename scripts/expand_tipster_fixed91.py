"""Fixed 90:10 comparison, separating previous events and changed decision times."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import requests

from matched_tipster_source import prepare, stamp, ET, KST
from compare_matched_tipsters import single
from matched_tipster_replay import recommend, select_pair, rounded_price
from verify_tipster_mlb_results import reconcile, final_tickets

TEAMS = dict(zip(
 'LA다저스 LA에인절 뉴욕메츠 뉴욕양키 디트타이 마이말린 미네트윈 밀워브루 보스레드 볼티오리 샌디파드 샌프자이 세인카디 시애매리 시카컵스 시카화이 신시레즈 애리다이 애슬레틱 애틀브레 워싱내셔 캔자로얄 콜로로키 클리가디 탬파레이 텍사레인 토론블루 피츠파이 필라필리 휴스애스'.split(),
 'LAD LAA NYM NYY DET MIA MIN MIL BOS BAL SD SF STL SEA CHC CWS CIN ARI ATH ATL WSH KC COL CLE TB TEX TOR PIT PHI HOU'.split()))


def decision(kickoff, mode):
    return kickoff-timedelta(minutes=30) if mode=='t30' else kickoff.astimezone(ET).replace(hour=12,minute=0,second=0,microsecond=0)


def recover(start, end):
    lines = subprocess.check_output(['git','log','--all','--since='+start,'--until='+end,
        '--format=%H %cI','--','docs/data/picks_v2.json'],text=True).splitlines()
    rows=[]; excluded=Counter(); hashes={}
    for sha,when,raw in read_snapshots(lines):
        hashes[sha]=hashlib.sha256(raw).hexdigest(); data=json.loads(raw)
        captured=max(stamp(when),stamp(data['generated_at']))
        for g in data.get('live',[]):
            if g.get('league')!='MLB': continue
            m=re.fullmatch(r'(\d{2})\.(\d{2})\([^)]*\) (\d{2}):(\d{2})',g.get('date',''))
            if not m or type(g.get('year')) is not int or not g.get('event_id') or g.get('home') not in TEAMS or g.get('away') not in TEAMS:
                excluded['missing_schedule_or_team']+=1;continue
            kickoff=datetime(g['year'],*map(int,m.groups()),tzinfo=KST)
            if captured>kickoff-timedelta(minutes=30):
                excluded['snapshot_after_t30']+=1;continue
            opts=[o for o in g.get('options',[]) if o.get('market')=='승패' and not o.get('label')]
            sides={o.get('선택'):o for o in opts}
            try:
                if len(opts)!=2 or set(sides)!={'홈','원정'}: raise ValueError()
                p=[float(sides[s]['최종확률']) for s in ('홈','원정')]
                odds=[float(sides[s]['배당']) for s in ('홈','원정')]
                if not all(math.isfinite(x) for x in p+odds) or min(p)<0 or max(p)>1 or abs(sum(p)-1)>.01 or min(odds)<=1: raise ValueError()
            except (KeyError,TypeError,ValueError):
                excluded['invalid_probability_or_price']+=1;continue
            rows.append(dict(id=g['event_id'],kickoff=kickoff.isoformat(),snapshot_at=captured.isoformat(),snapshot_id=sha,
                home=TEAMS[g['home']],away=TEAMS[g['away']],date=kickoff.astimezone(ET).date().isoformat(),
                model=[x/sum(p) for x in p],odds=odds,round_key=f"{g.get('year')}:{g.get('round')}",
                final_equals_market=all(sides[s]['최종확률']==sides[s].get('시장확률') for s in sides)))
    return rows,dict(commits=len(lines),snapshot_hashes=hashes,snapshot_exclusions=dict(excluded))


def read_snapshots(lines):
    # One Git process; each blob is read completely before requesting the next.
    with subprocess.Popen(['git','cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE) as proc:
        for line in lines:
            sha,when=line.split()
            proc.stdin.write((sha+':docs/data/picks_v2.json\n').encode());proc.stdin.flush()
            header=proc.stdout.readline().split()
            if len(header)!=3 or header[1]!=b'blob': raise ValueError('Missing snapshot blob')
            size=int(header[2]);raw=proc.stdout.read(size)
            if len(raw)!=size or proc.stdout.read(1)!=b'\n': raise ValueError('Truncated Git blob')
            yield sha,when,raw
        proc.stdin.close()
        if proc.wait()!=0: raise ValueError('Git snapshot reader failed')


def link(snapshots, pool, mode):
    latest={}; excluded=Counter()
    for r in snapshots:
        k=stamp(r['kickoff']); at=decision(k,mode)
        if stamp(r['snapshot_at'])<=at<=k-timedelta(minutes=30):
            if r['id'] not in latest or stamp(r['snapshot_at'])>stamp(latest[r['id']]['snapshot_at']): latest[r['id']]=r
    rows=[]
    for original in latest.values():
        r=dict(original); k=stamp(r['kickoff']); at=decision(k,mode)
        key='|'.join((r['date'],*sorted((r['home'],r['away']))))
        related=[t for t in pool if t['event']==key and abs((t['kickoff']-k).total_seconds())<=600]
        authors={}
        for t in sorted(related,key=lambda t:(t['observed'],t['id'])):
            if t['observed']<=at: authors.setdefault(t['author'],t)
        if not authors: excluded['no_tipster_before_decision']+=1;continue
        done=[t for t in related if t['outcome'] in ('W','L')]
        winners={t['side'] if t['outcome']=='W' else r['away'] if t['side']==r['home'] else r['home'] for t in done}
        y=(0 if next(iter(winners))==r['home'] else 1) if len(winners)==1 else None
        q=sum(t['side']==r['home'] for t in authors.values())/len(authors)
        r.update(event=key,at=at.isoformat(),tipster=[q,1-q],authors=len(authors),y=y,
            settled_at=max(t['settled'] for t in done).isoformat() if y is not None else None,
            tipster_evidence=[dict(id=t['id'],author=t['author'],side=t['side'],observed=t['observed'].isoformat()) for t in authors.values()])
        rows.append(r)
    counts=Counter(r['event'] for r in rows)
    unique=[r for r in rows if counts[r['event']]==1]
    excluded['ambiguous_same_day_teams']=len(rows)-len(unique)
    return sorted(unique,key=lambda r:(r['at'],r['id'])),dict(model_candidates=len(latest),exclusions=dict(excluded))


def compare_fixed(rows):
    out={}
    for name,w in [('operating',1.),('hybrid91',.9),('tipster',0.)]:
        blended=[dict(r,model=[w*p+(1-w)*q for p,q in zip(r['model'],r['tipster'])]) for r in rows]
        sides=[max(range(2),key=lambda i:r['model'][i]) for r in blended]
        out[name]=dict(single=single(blended,sides),selected=[dict(id=r['id'],side=s) for r,s in zip(rows,sides)],
            daily={d:single([r for r in blended if r['date']==d],[s for r,s in zip(rows,sides) if r['date']==d]) for d in sorted({r['date'] for r in rows})})
    return out


def input_changes(rows, previous):
    old={r['id']:r for r in previous};counts=Counter()
    for r in rows:
        if r['id'] not in old: continue
        p=old[r['id']];counts['previous_events']+=1
        for field in ('model','odds','tipster','snapshot_id'):
            changed = r[field]!=p[field] if field=='snapshot_id' else any(not math.isclose(a,b,rel_tol=0,abs_tol=1e-12) for a,b in zip(r[field],p[field]))
            counts[field+'_changed']+=changed
        def side(x): return max(range(2),key=lambda i:.9*x['model'][i]+.1*x['tipster'][i])
        counts['hybrid91_side_changed']+=side(r)!=side(p)
    return dict(counts)


def validate_official_timing(rows):
    for r in rows:
        official=stamp(r['official']['game_date'])
        # Require the same scheduled event and pregame decision even for a single candidate.
        if abs((official-stamp(r['kickoff'])).total_seconds())>600 or stamp(r['at'])>official-timedelta(minutes=30):
            raise ValueError('Official timing does not support pregame decision: '+r['id'])
        if stamp(r['snapshot_at'])>stamp(r['at']): raise ValueError('Future snapshot')
        if any(stamp(t['observed'])>stamp(r['at']) for t in r['tipster_evidence']): raise ValueError('Future tip')


def frozen_budget_tickets(recs, weekly, cutoff):
    # No reliable full settlement event stream: never recycle winnings/reserved capital.
    # A stricter monthly 50k stake ceiling guarantees the requested maximum 50k loss.
    months=Counter();weeks=Counter();used=set();tickets=[]
    for when in sorted({r['at'] for r in recs},key=stamp):
        at=stamp(when);local=at.astimezone(KST);month=local.strftime('%Y-%m');week=local.isocalendar()[:2]
        if at>cutoff or weeks[week]>=weekly or months[month]+10000>50000: continue
        available=[r for r in recs if r['at']==when and r['event'] not in used
            and stamp(r['snapshot_at'])<=at<=stamp(r['kickoff'])-timedelta(minutes=30)]
        pair=select_pair(available,'probability')
        if not pair: continue
        tickets.append(dict(at=when,month=month,ids=[r['id'] for r in pair],sides=[r['side'] for r in pair],price=rounded_price(pair)))
        months[month]+=10000;weeks[week]+=1;used.update(r['event'] for r in pair)
    return dict(ticket_log=tickets,monthly_stakes=dict(months),budget_policy='Monthly stakes <=50000; no result-dependent release or reinvestment. Stricter than a net-loss stop.')


def run(raw, prior, output):
    events=[json.loads(l) for l in raw.read_text(encoding='utf8').splitlines() if l.strip()]
    pool, tip_excluded=prepare(events)
    first=min(t['date'] for t in pool);last=max(t['date'] for t in pool)
    start=(datetime.fromisoformat(first)-timedelta(days=3)).date().isoformat()
    end=(datetime.fromisoformat(last)+timedelta(days=2)).date().isoformat()
    response=requests.get('https://statsapi.mlb.com/api/v1/schedule',params=dict(sportId=1,startDate=first,endDate=last),timeout=30)
    response.raise_for_status()
    official_source=dict(url=response.url,sha256=hashlib.sha256(response.content).hexdigest(),data=response.json())
    snapshots,audit=recover(start,end)
    old=json.loads(prior.read_text(encoding='utf8'));old_ids={r['id'] for r in old['opportunity_ledger']}
    cutoff=max(stamp(r['observed_at']) for r in events)
    results={}
    for mode in ('noon','t30'):
        rows,coverage=link(snapshots,pool,mode)
        official,settlement=reconcile(rows,old['sources']['teams']['data'],official_source['data'])
        validate_official_timing(official)
        new=[r for r in official if r['id'] not in old_ids]
        paper={}
        for name,w in [('operating',1.),('hybrid91',.9),('tipster',0.)]:
            recs=recommend([dict(r,model=[w*p+(1-w)*q for p,q in zip(r['model'],r['tipster'])]) for r in rows],'model','probability')
            original={name:dict(policies={'probability':dict(paper={str(n):frozen_budget_tickets(recs,n,cutoff) for n in (2,3)})})}
            paper[name]=dict(original=original[name],final=final_tickets(original,official)[name])
        results[mode]=dict(coverage=coverage,official_audit=settlement,total=len(official),new_events=len(new),
            comparison_all=compare_fixed(official),comparison_new=compare_fixed(new),
            comparison_previous_events=compare_fixed([r for r in official if r['id'] in old_ids]),
            input_changes_previous=input_changes(official,old['opportunity_ledger']),ledger=official,paper=paper)
    result=dict(protocol='Fixed 90% stored operating probability + 10% author vote; no weight search',
        monthly_loss_limit_krw=50000,stake_krw=10000,prior_events=len(old_ids),raw_log_rows=len(events),eligible_tips=len(pool),
        tip_exclusions=tip_excluded,raw_observed_range=[min(r['observed_at'] for r in events),max(r['observed_at'] for r in events)],
        sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (raw,prior,Path(__file__))},official_source=official_source,recovery=audit,results=results,
        limitations=['New events from old archive are not untouched future holdout. T30 changes decision timing, not just cohort size.',
          'Official archive coverage may block new dates; fail instead of dropping missing outcomes.',
          'T30 paper pairs only games simultaneously eligible at the same T30 decision, not future-known selections.',
          'Paper budget is stricter: <=50000 monthly total stakes, no result-dependent releases. Not an exact historical net-loss-stop replay.',
          'Stored operating probabilities may be market-derived; inspect final_equals_market.',
          'No claim of official historical odds availability, stable monthly income or production adoption.'])
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({m:dict(total=r['total'],new_events=r['new_events'],all={a:v['single'] for a,v in r['comparison_all'].items()},new={a:v['single'] for a,v in r['comparison_new'].items()}) for m,r in results.items()},ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--raw',type=Path,required=True);ap.add_argument('--prior',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();run(a.raw,a.prior,a.output)
