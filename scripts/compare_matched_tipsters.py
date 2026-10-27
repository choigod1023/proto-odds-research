"""Actual tipster/model comparison on matched evidence, not an odds-only proxy.

Fixed descriptive weights; no winner chosen on these already explored data.
The MLB publisher grades are not independently verified official results.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

from matched_tipster_source import prepare, stamp
from matched_tipster_replay import recommend, replay_tickets

# Always model weight first, tipster vote weight second. Never optimize on results.
WEIGHTS = (1., .9, .75, .5, .25, 0.)


def validate(row):
    k = len(row['model'])
    if k not in (2,3) or len(row['tipster']) != k or len(row['odds']) != k:
        raise ValueError('Dimension mismatch')
    for name in ('model', 'tipster'):
        p = row[name]
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in p) or not math.isclose(sum(p),1,abs_tol=1e-6):
            raise ValueError('Invalid score vector')
    if any(not math.isfinite(x) or x <= 1 for x in row['odds']):
        raise ValueError('Invalid odds')
    if row['y'] is not None and (type(row['y']) is not int or row['y'] not in range(k)):
        raise ValueError('Invalid result')


def rebuild(ledger, events):
    pool, excluded = prepare(events)
    rows, missing = [], []
    for source in ledger:
        row = dict(source)
        when, kickoff = stamp(row['at']), stamp(row['kickoff'])
        related = [t for t in pool if t['event'] == row['event'] and
                   abs((t['kickoff'] - kickoff).total_seconds()) <= 600]
        authors = {}
        for t in sorted(related, key=lambda t:(t['observed'],t['id'])):
            if t['observed'] <= when:
                authors.setdefault(t['author'],t)
        if not authors:
            missing.append(row['id']); continue
        if stamp(row['snapshot_at']) > when or when >= kickoff:
            raise ValueError('Model unavailable at decision time')
        votes = list(authors.values())
        q = [sum(t['side']==side for t in votes)/len(votes) for side in (row['home'],row['away'])]
        if row['authors'] != len(votes) or any(abs(a-b)>1e-12 for a,b in zip(q,row['tipster'])):
            raise ValueError('Saved tipster votes differ from raw first observations')
        done = [t for t in related if t['outcome'] in ('W','L')]
        winners = {t['side'] if t['outcome']=='W' else row['away'] if t['side']==row['home'] else row['home'] for t in done}
        winner = next(iter(winners)) if len(winners)==1 else None
        y = (0 if winner==row['home'] else 1) if winner else None
        if y != row['y']:
            raise ValueError('Saved outcome differs from raw publisher grades')
        row['tipster'] = q
        row['tipster_evidence'] = [dict(pick_id=t['id'],author=t['author'],side=t['side'],
             observed_at=t['observed'].isoformat(),kickoff=t['kickoff'].isoformat(),
             publisher_grade=t['outcome'],linkage=t['linkage']) for t in votes]
        validate(row); rows.append(row)
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate event')
    return rows, dict(raw_events=len(events),eligible_tipster_picks=len(pool),raw_exclusions=excluded,
                     model_opportunities=len(ledger),no_available_tipster=len(missing),
                     unmatched_model_ids=missing,matched=len(rows),unresolved=sum(r['y'] is None for r in rows),
                     authors=len({t['author'] for r in rows for t in r['tipster_evidence']}),
                     dates=sorted({r['date'] for r in rows}))


def single(rows, picks):
    known = [(r,s) for r,s in zip(rows,picks) if r['y'] is not None]
    wins = sum(r['y']==s for r,s in known)
    net = sum(r['odds'][s]*(r['y']==s)-1 for r,s in known)
    return dict(opportunities=len(rows),settled=len(known),pending=len(rows)-len(known),hits=wins,
                hit_rate_pct=100*wins/len(known) if known else None,
                roi_pct=100*net/len(known) if known else None,net_units=net)


def compare(rows, cutoff=None):
    for r in rows: validate(r)
    result = {}
    for w in WEIGHTS:
        blended = [dict(r,model=[w*p+(1-w)*q for p,q in zip(r['model'],r['tipster'])]) for r in rows]
        choices = [max(range(len(r['model'])),key=lambda i:r['model'][i]) for r in blended]
        policies = {}
        for policy in ('probability','edge','pair_edge'):
            picks = recommend(blended,'model',policy)
            policies[policy] = dict(recommendations=len(picks),
                single=single(picks,[r['side'] for r in picks]),
                paper={str(n):replay_tickets(picks,policy,n,cutoff) for n in (2,3)} if cutoff else None,
                selected=[dict(id=r['id'],side=r['side'],score=r['probability'],odds=r['price']) for r in picks])
        result[str(w)] = dict(model_weight=w,tipster_vote_weight=1-w,
            all_matched_single=single(blended,choices),policies=policies,
            daily={day:single([r for r in blended if r['date']==day],
                [s for r,s in zip(blended,choices) if r['date']==day]) for day in sorted({r['date'] for r in rows})},
            picks=[dict(id=r['id'],side=s) for r,s in zip(rows,choices)])
    return result


def run(selection, raw, backfill):
    report = json.loads(selection.read_text(encoding='utf8'))
    expected = [h for p,h in report['hashes'].items() if p.replace('\\','/').endswith('tailslips_pick_events.jsonl')]
    actual = hashlib.sha256(raw.read_bytes()).hexdigest()
    if expected != [actual]: raise ValueError('Raw tipster archive has changed; reassess linkage first')
    events = [json.loads(line) for line in raw.read_text(encoding='utf8').splitlines() if line.strip()]
    rows, audit = rebuild(report['opportunity_ledger'], events)
    cutoff = max(stamp(r['observed_at']) for r in events)
    archive = json.loads(backfill.read_text(encoding='utf8'))
    other = [dict(id=r['url'],date=r['date'],sport=r['sport'],league=r['league'],
             model=r['probability'],tipster=[float(i==r['choices']['tipster_only']) for i in range(len(r['probability']))],
             odds=r['odds'],y=r['y'],source_url=r['url'],timing_status=r['timing_status']) for r in archive['ledger']]
    if len({r['id'] for r in other}) != len(other): raise ValueError('Duplicate backfill article')
    return dict(status='matched_tipster_recomparison_not_promotion',
        sources={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (selection,raw,backfill,Path(__file__))},
        code_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                    (Path(__file__).with_name('matched_tipster_source.py'),Path(__file__).with_name('matched_tipster_replay.py'))},
        scope=dict(uses_tipsters=True,new_scraping=False,official_results_verified=False,untouched_holdout=False),
        audit=audit,cutoff=cutoff.isoformat(),monthly_loss_limit_krw=50000,stake_krw=10000,
        weights=list(WEIGHTS),weight_order='model : tipster vote share',
        mlb_one_author=compare(rows,cutoff),mlb_three_authors=compare([r for r in rows if r['authors']>=3],cutoff),
        retrospective_backfill={s:compare([r for r in other if r['sport']==s]) for s in ('bs','bk','sc','vl')},
        opportunity_ledger=rows,backfill_ledger=other,
        limitations=['First observed pregame tips are demonstrated only by the stored source log, not an independent timestamp authority.',
          'MLB results are publisher grades, not independently verified official scores; quote carry-forward may be stale.',
          'Backfill articles were retrieved after games; no trustworthy two-leg replay for those 16 events.',
          'Vote shares/one-hot votes are not calibrated probabilities; edge filters are sensitivity checks, not proven expected return.',
          'Already explored tiny cohorts. All weights fixed, no optimum or adoption selected.',
          'Weights are OUR MODEL first, TIPSTER second; not the market/model mixing of PR364.',
          'Monthly budget reserves all pending stakes conservatively; distinct games in same recorded round, no proof of sales availability.'])


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--selection',type=Path,required=True)
    ap.add_argument('--raw',type=Path,required=True);ap.add_argument('--backfill',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    result=run(a.selection,a.raw,a.backfill);a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result['audit'],ensure_ascii=False))
