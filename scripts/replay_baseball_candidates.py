"""Reproduce frozen baseball candidates locally; no fitting or live claims."""
import argparse
from collections import Counter,defaultdict
from decimal import Decimal,ROUND_HALF_UP
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import mlb_frozen_candidate as mlb
import kbo_frozen_candidate as kbo


def settle(side,prices,hg,ag):
    if hg==ag:return 0,0,1
    hit=(hg>ag)==(side=='home')
    money=int((Decimal(str(prices[side]))*10000).quantize(Decimal(1),rounding=ROUND_HALF_UP))-10000 if hit else -10000
    return int(hit),money,0


def summary(rows):
    groups={}
    for name in ('candidate','baseline','market'):
        daily=defaultdict(int);hits=net=voids=0
        for r in rows:
            h,p,v=r[name];hits+=h;net+=p;voids+=v;daily[r['date']]+=p
        equity=peak=dd=0
        for day in sorted(daily):
            equity+=daily[day];peak=max(peak,equity);dd=max(dd,peak-equity)
        groups[name]=dict(n=len(rows),hits=hits,voids=voids,net_won=net,
            roi=net/(len(rows)*10000) if rows else None,
            hit_rate=hits/(len(rows)-voids) if len(rows)>voids else None,
            daily_close_drawdown_won=dd,kickoff_day_net_won=dict(sorted(daily.items())))
    return groups


def replay():
    data=ROOT/'data/model_candidates'
    choices=[];source_hashes={};max_error=0
    for league,name in [('MLB','mlb/parity-fixtures.jsonl.gz'),('KBO','kbo/archived-reproduction.jsonl.gz')]:
        raw=(data/name).read_bytes();source_hashes[name]=hashlib.sha256(raw).hexdigest()
        for row in map(json.loads,gzip.decompress(raw).decode().splitlines()):
            if league=='MLB':
                result=mlb.infer_mlb_candidate_local(row['snapshot'],decision_at=row['decision_at'],
                    matchup=row['matchup'],odds=row['odds'])
                assert result['status']=='available',result
                side=result['selection'];p=result['probability_home']
                expected=row['expected']['probability_home'];lab=row['labels']
                prices={s:row['odds'][s] for s in ('home','away')}
                date=lab['date'];cohort=lab['cohort'];baseline_p=lab['probabilities']['baseline'];market_p=lab['probabilities']['market_only']
                assert side==lab['ledger']['primary']['pick']
            else:
                result=kbo.infer_archived(row,source_signature=kbo.artifact()['archive_sources'],partition=row['role'])
                assert result['status']=='available',result
                side=result['side'];p=result['home_probability'];expected=row['expected']['probability']
                prices=dict(zip(row['order'],row['prices']))
                date=row['date'];cohort=row['role'];baseline_p=row['reference_home_probability'];market_p=row['market_home_probability']
                assert side==row['expected']['side']
            error=abs(p-expected);max_error=max(max_error,error);assert error<=1e-12
            # Outcomes are read only AFTER all three selections are fixed.
            baseline='home' if baseline_p>=.5 else 'away';market='home' if market_p>=.5 else 'away'
            hg,ag=(lab['hg'],lab['ag']) if league=='MLB' else (row['hg'],row['ag'])
            choice=dict(league=league,cohort=cohort,date=date,candidate_side=side,baseline_side=baseline,
                        candidate=settle(side,prices,hg,ag),baseline=settle(baseline,prices,hg,ag),
                        market=settle(market,prices,hg,ag),candidate_home_probability=p)
            expected_money=lab['ledger']['primary']['net_krw'] if league=='MLB' else row['expected']['profit_krw']
            assert choice['candidate'][1]==expected_money
            choices.append(choice)
    reports={}
    for league,cohort in sorted({(r['league'],r['cohort']) for r in choices}):
        rs=[r for r in choices if (r['league'],r['cohort'])==(league,cohort)]
        boundary={('MLB','old116'):'2026-07-01',('MLB','external230'):'2026-08-10',
                  ('KBO','evaluation'):'2026-07-01'}.get((league,cohort))
        reports[league+'/'+cohort]=dict(summary=summary(rs),
            candidate_side_counts=dict(Counter(r['candidate_side'] for r in rs)),
            different_picks=sum(r['candidate_side']!=r['baseline_side'] for r in rs),
            from_date=min(r['date'] for r in rs),to_date=max(r['date'] for r in rs),
            fixed_periods=({period:summary([r for r in rs if (r['date']<boundary)==before])
                for period,before in [('before_split',True),('split_onward',False)]} if boundary else {}),
            fixed_split_date=boundary)
    return dict(version='frozen-baseball-bridge-replay-v1',source_sha256=source_hashes,
        max_abs_probability_error=max_error,decision_and_money_matches=len(choices),
        monthly_cap_applied=False,stake_won=10000,operating_model_reproduced=False,
        new_training_or_tuning=False,
        limitations=['Reproduction of previously inspected research sets; not a fresh holdout.',
                     'MLB archived quote receipt times are not independently verified.',
                     'KBO historical availability uses the recorded prior-day assumption, not contemporary proof.',
                     'Baseline is archived research 9:1 for old116/KBO, market-only for external194; not operating vote.'],
        cohorts=reports,choices=choices)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();report=replay()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:{'n':v['summary']['candidate']['n'],'candidate_roi':v['summary']['candidate']['roi'],
                         'baseline_roi':v['summary']['baseline']['roi'],'different_picks':v['different_picks']}
                      for k,v in report['cohorts'].items()},ensure_ascii=False))
