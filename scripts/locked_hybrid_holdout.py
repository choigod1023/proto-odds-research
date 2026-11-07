"""Locked 75:25 research comparison. No production promotion or wager execution."""
import argparse
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path

ARMS=('production','tipster','hybrid75_25')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def freeze(development, raw, locked_at):
    if datetime.fromisoformat(locked_at).tzinfo is None:
        raise ValueError('Lock time must include timezone')
    rows=development['paired_rows']
    if not rows:
        raise ValueError('Development exclusions required')
    return dict(version=1,locked_at=locked_at,production_weight=.75,tipster_weight=.25,
        development_sha256=digest(raw),excluded_events=sorted({r['event'] for r in rows}),
        excluded_ids=sorted({r['event_id'] for r in rows}),
        development_last_date=max(r['date'] for r in rows),
        monthly_loss_limit_krw=50000,stake_krw=10000,weekly_ticket_limits=[2,3],
        selection='same-event side score >=0.5 chooses home; otherwise away',
        paper_selection='two distinct events; lower odds first; noon ET; reserve pending exposure',
        promotion='none; independent review required',
        scope='MLB two-way full-game moneyline only')


def scores(row):
    p,t=row['arms']['production'],row['arms']['tipster']
    return dict(production=p,tipster=t,hybrid75_25=.75*p+.25*t)


def metrics(rows,arm):
    if not rows:
        return dict(n=0,wins=0,roi_pct=None,hit_pct=None,brier=None)
    wins=profit=brier=0
    for r in rows:
        p=scores(r)[arm]; home=p>=.5; won=home==r['home_won']
        wins+=won
        profit+=r['odds'][0 if home else 1]-1 if won else -1
        brier+=(p-r['home_won'])**2
    return dict(n=len(rows),wins=wins,hit_pct=100*wins/len(rows),
                roi_pct=100*profit/len(rows),brier=brier/len(rows))


def evaluate(data,lock):
    if (lock['production_weight'],lock['tipster_weight'],lock['monthly_loss_limit_krw'])!=(.75,.25,50000):
        raise ValueError('Locked policy mismatch')
    all_rows=data['paired_rows']
    counts=Counter(r['event'] for r in all_rows)
    excluded=Counter(); groups=dict(retrospective_unseen=[],prospective=[])
    for r in all_rows:
        if r['event'] in lock['excluded_events'] or r['event_id'] in lock['excluded_ids']:
            excluded['development_event']+=1;continue
        if r['date']<=lock['development_last_date']:
            excluded['not_later_than_development']+=1;continue
        if counts[r['event']]!=1:
            excluded['duplicate_event']+=1;continue
        try:
            at,snapshot,start=(datetime.fromisoformat(r[k]) for k in ('at','snapshot_at','kickoff'))
            if any(x.tzinfo is None for x in (at,snapshot,start)) or not snapshot<=at<=start-timedelta(minutes=30):
                raise ValueError()
            if r['authors']<3 or type(r['home_won']) is not bool or len(r['odds'])!=2:
                raise ValueError()
            if any(not math.isfinite(x) or x<=1 for x in r['odds']):raise ValueError()
            if any(not math.isfinite(x) or not 0<=x<=1 for x in scores(r).values()):raise ValueError()
        except (ValueError,KeyError,TypeError):
            excluded['invalid_or_nonpregame_record']+=1;continue
        group='prospective' if snapshot>=datetime.fromisoformat(lock['locked_at']) else 'retrospective_unseen'
        groups[group].append(r)
    return dict(status='awaiting_independent_records' if not any(groups.values()) else 'research_only',
        candidates_fixed=3,weights=dict(production=.75,tipster=.25),excluded=dict(excluded),
        groups={name:{arm:metrics(rows,arm) for arm in ARMS} for name,rows in groups.items()},
        eligible_events={name:[r['event'] for r in rows] for name,rows in groups.items()},
        monthly_loss_limit_krw=50000,production_changed=False,
        two_leg_status='not_evaluated; no claim from single-pick ROI',
        limitations=['No automatic promotion or tuning from holdout outcomes.',
          'Retrospective unseen means absent from development file, not proof nobody previously viewed it.',
          'Pregame provenance, official results and odds availability must be independently verified.',
          'Input contract: MLB two-way full-game pairs from the audited recovery pipeline.',
          'No monthly profit projection or full-month stability claim.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--lock',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--freeze-at',help='Only for initial immutable lock creation using development input')
    a=p.parse_args(); raw=a.input.read_bytes(); data=json.loads(raw)
    if a.freeze_at:
        lock=freeze(data,raw,a.freeze_at)
        a.lock.parent.mkdir(parents=True,exist_ok=True)
        with a.lock.open('x',encoding='utf8') as f:json.dump(lock,f,ensure_ascii=False,indent=2)
    lock_raw=a.lock.read_bytes();lock=json.loads(lock_raw)
    result=evaluate(data,lock)
    result.update(input_sha256=digest(raw),lock_sha256=digest(lock_raw))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
