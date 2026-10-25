"""Read-only paired historical comparison; never replaces frozen predictions.

Uses a pinned local replay implementation from research PR353. Fixed mixtures
are diagnostic uncalibrated scores, not claimed deployable probabilities.
"""
import argparse
from collections import Counter, defaultdict
from datetime import timedelta
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import subprocess

TEAMS = dict(zip(
    'LA다저스 LA에인절 뉴욕메츠 뉴욕양키 디트타이 마이말린 미네트윈 밀워브루 보스레드 볼티오리 샌디파드 샌프자이 세인카디 시애매리 시카컵스 시카화이 신시레즈 애리다이 애슬레틱 애틀브레 워싱내셔 캔자로얄 콜로로키 클리가디 탬파레이 텍사레인 토론블루 피츠파이 필라필리 휴스애스'.split(),
    'LAD LAA NYM NYY DET MIA MIN MIL BOS BAL SD SF STL SEA CHC CWS CIN ARI ATH ATL WSH KC COL CLE TB TEX TOR PIT PHI HOU'.split()))


def paper_summary(events):
    result = {}
    for account in sorted({r['account'] for r in events if r['type']=='bet'}):
        bets=[r for r in events if r['type']=='bet' and r['account']==account]
        settlements={r['ticket']:r for r in events if r['type']=='settlement' and r['account']==account}
        paid=[r for r in bets if r['ticket'] in settlements]
        pending=[r for r in bets if r['ticket'] not in settlements]
        net=sum(settlements[r['ticket']]['net'] for r in paid)
        result[account]=dict(bets=len(bets),settled=len(paid),pending=len(pending),
            settled_net_krw=net if paid else None,pending_stake_krw=sum(r['stake'] for r in pending),
            roi_pct=100*net/sum(r['stake'] for r in paid) if paid else None,
            expected_gross_edge_NOT_verified=sum((math.prod(x['probability']*x['odds'] for x in r['legs'])-1)*r['stake'] for r in bets))
    return result


def paired(ledger,games,pool,replay,decision_mode='snapshot'):
    by_id={g['event_id']:g for g in games if g.get('event_id')}
    candidates=defaultdict(list)
    for row in ledger:
        game=by_id.get(row['event_id'],{})
        if game.get('league')!='MLB' or game.get('home') not in TEAMS or game.get('away') not in TEAMS:
            continue
        at=replay.stamp(row['captured_at'])
        kickoff=replay.stamp(row['kickoff'])
        if at<=kickoff-timedelta(minutes=30) and replay.stamp(row['as_of'])<=at:
            candidates[row['event_id']].append(row)
    output,excluded=[],Counter()
    for event_id, snapshots in candidates.items():
        # Latest preserved snapshot before T-30; frozen timestamp is the cutoff
        # for all model arms and tipster votes.
        if decision_mode=='noon':
            day=replay.stamp(snapshots[0]['kickoff']).astimezone(replay.ET)
            decision=day.replace(hour=12,minute=0,second=0,microsecond=0)
            snapshots=[s for s in snapshots if replay.stamp(s['captured_at'])<=decision
                       and decision<=replay.stamp(s['kickoff'])-timedelta(minutes=30)]
            if not snapshots:
                excluded['no_snapshot_before_noon_or_game_already_started']+=1
                continue
        row=max(snapshots,key=lambda r:replay.stamp(r['captured_at']))
        game=by_id[event_id]
        at,kickoff=replay.stamp(row['captured_at']),replay.stamp(row['kickoff'])
        snapshot_at=at
        if decision_mode=='noon':
            at=decision
        home,away=TEAMS[game['home']],TEAMS[game['away']]
        date=kickoff.astimezone(replay.ET).date().isoformat()
        event='|'.join((date,*sorted((home,away))))
        tips=[r for r in pool if r['event']==event and r['observed']<=at
              and abs((r['kickoff']-kickoff).total_seconds())<=600]
        # A ten-minute schedule tolerance is explicit, not a fuzzy team match.
        authors={}
        for tip in sorted(tips,key=lambda r:(r['observed'],r['id'])):
            authors.setdefault(tip['author'],tip)
        tips=list(authors.values())
        if len(tips)<3:
            excluded['fewer_than_three_pre_snapshot_authors']+=1
            continue
        opts=[o for o in row['features']['options'] if o.get('market')=='승패'
              and not o.get('label') and o.get('selection') in ('홈','원정')]
        if len(opts)!=2 or {o['selection'] for o in opts}!={'홈','원정'}:
            excluded['missing_two_way_market']+=1
            continue
        opts={o['selection']:o for o in opts}
        try:
            values={k:float(opts['홈'][k]) for k in ('final_probability','market_probability','score_model_probability')}
            if any(not 0<=v<=1 or not math.isfinite(v) for v in values.values()):
                raise ValueError()
            for k in values:
                if abs(values[k]+float(opts['원정'][k])-1)>0.01:
                    raise ValueError()
            odds=[float(opts[s]['odds']) for s in ('홈','원정')]
            if any(not math.isfinite(v) or v<=1 for v in odds):
                raise ValueError()
        except (KeyError,TypeError,ValueError):
            excluded['missing_valid_frozen_probabilities']+=1
            continue
        # Do not let our selected tipster/model side determine the target result.
        all_results=[r for r in pool if r['event']==event and abs((r['kickoff']-kickoff).total_seconds())<=600 and r['outcome'] in ('W','L')]
        winners={r['side'] if r['outcome']=='W' else away if r['side']==home else home for r in all_results}
        if len(winners)!=1:
            excluded['unmatched_or_conflicting_publisher_outcome']+=1
            continue
        crowd=sum(r['side']==home for r in tips)/len(tips)
        model=values['final_probability']
        arms=dict(production=model,market=values['market_probability'],score_candidate=values['score_model_probability'],tipster=crowd)
        for w in (.25,.5,.75):
            arms[f'blend_{w}']=(1-w)*model+w*crowd
        output.append(dict(event_id=event_id,event=event,home=home,away=away,date=date,
            at=at.isoformat(),snapshot_at=snapshot_at.isoformat(),snapshot_age_hours=(at-snapshot_at).total_seconds()/3600,
            kickoff=kickoff.isoformat(),odds=odds,authors=len(tips),
            home_won=next(iter(winners))==home,
            settled_at=max(r['settled'] for r in all_results).isoformat(),arms=arms,
            result_source='relinked_publisher_NOT_official',snapshot_id=row.get('snapshot_id')))
    return sorted(output,key=lambda r:(r['kickoff'],r['event_id'])),dict(excluded),len(candidates)


def metrics(rows,name):
    wins=0
    profit=brier=0
    for r in rows:
        home=r['arms'][name]>=.5
        won=home==r['home_won']
        wins+=won
        profit+=r['odds'][0 if home else 1]-1 if won else -1
        brier+=(r['arms'][name]-r['home_won'])**2
    return dict(n=len(rows),wins=wins,hit_rate_pct=100*wins/len(rows) if rows else None,
        roi_pct=100*profit/len(rows) if rows else None,brier=brier/len(rows) if rows else None)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--replay-module',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--decision-mode',choices=['snapshot','noon'],default='snapshot')
    args=ap.parse_args()
    spec=importlib.util.spec_from_file_location('saved_tipster_replay',args.replay_module)
    replay=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    ledger_path=Path('data/raw/prediction_ledger/pregame.jsonl')
    tipster_path=Path('data/raw/picksters/tailslips_pick_events.jsonl')
    ledger=[json.loads(s) for s in ledger_path.read_text(encoding='utf8').splitlines()]
    events=[json.loads(s) for s in tipster_path.read_text(encoding='utf8').splitlines()]
    pool,_=replay.prepare(events)
    doc=json.loads(Path('docs/data/picks_v2.json').read_text(encoding='utf8'))
    rows,excluded,candidate_n=paired(ledger,doc['past']+doc['live'],pool,replay,args.decision_mode)
    names=['production','market','score_candidate','tipster','blend_0.25','blend_0.5','blend_0.75']
    dates=sorted({r['date'] for r in rows})
    split=max(1,len(dates)*2//3)
    train=[r for r in rows if r['date'] in dates[:split]]
    test=[r for r in rows if r['date'] in dates[split:]]
    training={n:metrics(train,n) for n in names}
    winner=max(names,key=lambda n:training[n]['roi_pct']) if train else None
    paper_ref=subprocess.check_output(['git','rev-parse','origin/codex/paper-state-v1'],text=True).strip()
    journal=json.loads(subprocess.check_output(['git','show',f'{paper_ref}:paper/journal.json']))
    paper={n:{} for n in names}
    for name in names:
        choices=[]
        for r in rows:
            home=r['arms'][name]>=.5
            choices.append(dict(id=r['event_id'],event=r['event'],author=name,
                side=r['home'] if home else r['away'],date=r['date'],
                observed=replay.stamp(r['at']),kickoff=replay.stamp(r['kickoff']),
                odds=r['odds'][0 if home else 1],outcome='W' if home==r['home_won'] else 'L',
                settled=replay.stamp(r['settled_at'])))
        cutoff=max((replay.stamp(r['settled_at']) for r in rows),default=replay.stamp('2026-09-05T00:00:00Z'))
        for w in (2,3):
            paper[name][str(w)]=replay.simulate(choices,cutoff,'lower_odds',w)
    result=dict(decision_mode=args.decision_mode,paper_journal_commit=paper_ref,paper_events=len(journal),paper_latest=journal[-1]['at'],
        actual_forward_paper=paper_summary(journal),candidate_model_events=candidate_n,paired_n=len(rows),
        exclusions=excluded,dates=dates,all_pairs={n:metrics(rows,n) for n in names},
        chronological_train=training,chronological_test={n:metrics(test,n) for n in names},
        training_selected_winner=winner,heldout_winner_result=metrics(test,winner) if winner else None,
        two_leg_paired_resolved_subset_EXPLORATORY=paper,paired_rows=rows,
        input_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ledger_path,tipster_path,Path('docs/data/picks_v2.json'),args.replay_module)},
        limitations=['Same frozen domestic odds across all seven arms.',
            'Noon mode carries forward earlier model/odds snapshot; quote availability at noon NOT proven. Snapshot ages retained.',
            'Crowd proportions and fixed mixtures are uncalibrated experimental scores.',
            'Outcome inferred from matching publisher grades; not independently verified official results.',
            'Two-leg replay restricted to matched resolved paired cohort: selection/missingness bias, NOT full opportunity replay.',
            'Two-leg selection uses lower odds among arm-chosen sides, not posthoc ROI optimization.',
            'Train/test split by ET game date; tiny/previously explored data not independent final holdout.',
            'No article rationale in TailSlips logs; causes of superiority cannot be inferred from picks alone.',
            'Existing forward-paper journal in October does not overlap Aug/Sep tipster window.',
            'No operational changes, auto promotion, or projected monthly income.'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2,default=str)
    print(json.dumps({k:v for k,v in result.items() if k not in ('paired_rows','two_leg_paired_resolved_subset_EXPLORATORY','input_hashes','limitations')},ensure_ascii=False,indent=2))
    for name,ws in paper.items():
        print(name,{w:{k:v for k,v in r.items() if k!='ticket_log'} for w,r in ws.items()})


if __name__=='__main__':
    main()
