"""Independent Decimal settlement, model application, and offline clean replay."""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
from collections import defaultdict
from contextlib import redirect_stdout
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import socket
import tempfile
from unittest.mock import patch

import numpy as np

import mlb_lineup_nonlinear as m
from mlb_lineup_nonlinear_collect import OUT, read_rows, write_json


def independent_metrics(rows, policy):
    ordered=sorted(rows,key=lambda r:(r['start_at'],r['gamePk']))
    profits=[];correct=pushes=changed=0;ll=[];br=[]
    for r in ordered:
        p=r['probabilities'].get(policy,r.get('candidate_probabilities',{}).get(policy,r['probabilities']['baseline']))
        home=p>=.5;win=(r['hg']>r['ag'])==home
        tie=r['hg']==r['ag'];pick='home' if home else 'away'
        cash=0 if tie else int((Decimal('10000')*(Decimal(str(r['prices'][int(not home)]))-1)).quantize(
            Decimal('1'),rounding=ROUND_HALF_UP)) if win else -10000
        correct+=int(win and not tie);pushes+=int(tie)
        changed+=int(home!=(r['probabilities']['baseline']>=.5));profits.append(cash)
        target=int(r['hg']>r['ag'])
        ll.append(-math.log(max(p if target else 1-p,1e-12)));br.append((p-target)**2)
        if policy in r['ledger']:
            assert r['ledger'][policy]==dict(pick=pick,correct=int(win and not tie),push=int(tie),net_krw=cash)
    wealth=peak=drawdown=0
    for net in profits:
        wealth+=net;peak=max(peak,wealth);drawdown=max(drawdown,peak-wealth)
    return dict(n=len(rows),correct=correct,pushes=pushes,net_krw=wealth,
                roi=wealth/(10000*len(rows)),hit=correct/(len(rows)-pushes),
                max_drawdown_krw=drawdown,changed_picks=changed,logloss=sum(ll)/len(ll),brier=sum(br)/len(br))


def canonical_hash(path):
    if path.suffix=='.gz':
        rows=read_rows(path)
        data=json.dumps(rows,sort_keys=True,separators=(',',':')).encode()
    elif path.suffix=='.jsonl':
        data=json.dumps(read_rows(path),sort_keys=True,separators=(',',':')).encode()
    else:
        data=json.dumps(json.loads(path.read_text(encoding='utf8')),sort_keys=True,separators=(',',':')).encode()
    return hashlib.sha256(data).hexdigest()


def load_artifact(path):
    return read_rows(path) if path.suffix in ['.gz','.jsonl'] else json.loads(path.read_text(encoding='utf8'))


def compare_artifacts(a,b,location='root'):
    if isinstance(a,dict):
        assert a.keys()==b.keys(),location
        return max([0.]+[compare_artifacts(v,b[k],location+'.'+k) for k,v in a.items()])
    if isinstance(a,list):
        assert len(a)==len(b),location
        return max([0.]+[compare_artifacts(v,w,location+f'[{i}]') for i,(v,w) in enumerate(zip(a,b))])
    if isinstance(a,float):
        np.testing.assert_allclose(a,b,atol=1e-12,rtol=0,err_msg=location)
        return abs(a-b)
    assert a==b,(location,a,b)
    return 0.


def verify():
    predictions=read_rows(OUT/'predictions.jsonl')
    frozen=json.loads((OUT/'frozen-selection.json').read_text(encoding='utf8'))
    results=json.loads((OUT/'results.json').read_text(encoding='utf8'))
    inputs=read_rows(OUT/'inputs.jsonl.gz');by_pk={r['gamePk']:r for r in inputs}
    assert len(predictions)==346 and len({r['gamePk'] for r in predictions})==346
    assert len(by_pk)==len(inputs)==906
    assert frozen['excluded_validation_overlap_ids']==[]
    checked_groups=0
    for key,policies in results['groups'].items():
        cohort,scope,period=key.split('/')
        split='2026-07-01' if cohort=='old116' else '2026-08-10'
        rows=[r for r in predictions if r['cohort']==cohort and (scope=='full_fallback' or r['announced'])]
        if period=='before_split':rows=[r for r in rows if r['date']<split]
        elif period=='split_onward':rows=[r for r in rows if r['date']>=split]
        elif period.startswith('month_'):rows=[r for r in rows if r['date'].startswith(period[6:])]
        for policy,reported in policies.items():
            independently=independent_metrics(rows,policy)
            for k,v in independently.items():
                if k in ['n','correct','pushes','net_krw','max_drawdown_krw','changed_picks']:
                    assert reported[k]==v,(key,policy,k,reported[k],v)
                else:np.testing.assert_allclose(reported[k],v,atol=1e-12,rtol=0,err_msg=f'{key} {policy} {k}')
            checked_groups+=1
    vals=read_rows(OUT/'validation-predictions.jsonl')
    for n in m.CANDIDATES:
        p=np.array([r['candidate_probabilities'][n] for r in vals]);y=np.array([r['y'] for r in vals])
        s=m.scores(y,p)
        for k,v in s.items():np.testing.assert_allclose(frozen['validation'][n][k],v,atol=1e-12,rtol=0)
    assert m.select_heads(frozen['validation'])==frozen['heads']
    # Rebuild previous-season league xwOBA and EB from preserved rows, independently of feature code.
    savant={}
    for year in [2023,2024,2025]:
        for kind in ['batter','pitcher']:
            rr=json.loads((OUT/f'sources/savant-{year}-{kind}.json').read_text(encoding='utf8'))['rows']
            valid=[r for r in rr if r.get('est_woba') not in [None,'']]
            mean=sum(float(r['pa'])*float(r['est_woba']) for r in valid)/sum(float(r['pa']) for r in valid)
            savant[year,kind]=(mean,{int(r['player_id']):r for r in rr})
    for inp in inputs:
        season=inp['proof']['prior_season'];assert season==int(inp['start_at'][:4])-1
        assert inp['snapshot_at']<=inp['cutoff_at']<inp['start_at']
        for side in ['home','away']:
            proof=inp['proof']['sides'][side]
            assert len(set(proof['lineup_order']))==9
            for kind,persons,shrink in [('batter',proof['batters'],150),('pitcher',[proof['starter']],300)]:
                mean,table=savant[season,kind]
                for player in persons:
                    row=table.get(player['id'],{})
                    n=float(row.get('pa') or 0)
                    expected=(n*float(row.get('est_woba') or mean)+shrink*mean)/(n+shrink)
                    np.testing.assert_allclose(player['rates'][0],expected,atol=1e-12,rtol=0)
    diagnostics=dict(validation_home_wins=sum(r['y'] for r in vals),candidate_scores={},coverage={},
                     selected_probability_ranges={},no_operating_model=True)
    for n in m.CANDIDATES:
        diagnostics['candidate_scores'][n]=dict(validation=frozen['validation'][n],
            evaluation={f'{c}/{scope}':independent_metrics([r for r in predictions if r['cohort']==c and
                       (scope=='full_fallback' or r['announced'])],n)
                for c in ['old116','external230'] for scope in ['verified','full_fallback']})
    for role in ['train','validation','evaluation','external']:
        rs=[r for r in inputs if r['role']==role]
        people=[p for r in rs for side in ['home','away'] for p in r['proof']['sides'][side]['batters']]
        pitchers=[r['proof']['sides'][side]['starter'] for r in rs for side in ['home','away']]
        diagnostics['coverage'][role]=dict(games=len(rs),batter_slots=len(people),pitcher_slots=len(pitchers),
            batter_prior_missing=sum(p['prior_exposure']==0 for p in people),
            batter_savant_missing=sum(p['savant_pa']==0 for p in people),
            pitcher_prior_missing=sum(p['prior_exposure']==0 for p in pitchers),
            pitcher_savant_missing=sum(p['savant_pa']==0 for p in pitchers))
    for c in ['old116','external230']:
        rs=[r for r in predictions if r['cohort']==c and r['announced']]
        probs=[r['probabilities']['primary'] for r in rs]
        diagnostics['selected_probability_ranges'][c]=dict(min=min(probs),max=max(probs),
                                                          home_picks=sum(p>=.5 for p in probs),n=len(rs))
    # Diagnostic only: announced games always home; unavailable games retain the SAME baseline fallback.
    # No candidate, fitted model, selected name or prediction artifact is changed.
    controls=[]
    for r in predictions:
        p=1. if r['announced'] else r['probabilities']['baseline']
        control=dict(r,probabilities=dict(r['probabilities'],home_constant_diagnostic=p))
        assert (p>=.5)==(r['probabilities']['primary']>=.5)
        controls.append(control)
    home_comparison={}
    for c in ['old116','external230']:
        split='2026-07-01' if c=='old116' else '2026-08-10'
        for scope in ['verified','full_fallback']:
            rs=[r for r in controls if r['cohort']==c and (r['announced'] or scope=='full_fallback')]
            for period,prs in dict(all=rs,before_split=[r for r in rs if r['date']<split],
                                  split_onward=[r for r in rs if r['date']>=split]).items():
                hm=independent_metrics(prs,'home_constant_diagnostic')
                pr=independent_metrics(prs,'primary')
                keys=['n','correct','pushes','net_krw','roi','hit','max_drawdown_krw','changed_picks']
                assert {k:hm[k] for k in keys}=={k:pr[k] for k in keys}
                home_comparison[f'{c}/{scope}/{period}']={k:hm[k] for k in keys}
    diagnostics['home_constant_control']=dict(post_evaluation_diagnostic_only=True,
          definition='p=1 on announced games; same market baseline fallback on unavailable games',
          all_picks_and_integer_profit_identical_to_primary=True,
          matching_metrics=home_comparison,
          excludes_probability_scores='logloss/Brier differ from primary probabilities; only direction/settlement coincide')
    write_json(OUT/'diagnostics.json',diagnostics)
    # Fresh output location, preserved sources only, and sockets forbidden for an actual offline rerun.
    names=['inputs.jsonl.gz','validation-predictions.jsonl','predictions.jsonl','frozen-selection.json','results.json']
    expected={name:canonical_hash(OUT/name) for name in names}
    with tempfile.TemporaryDirectory(prefix='offline-replay-',dir=OUT) as directory:
        dest=Path(directory)
        shutil.copytree(OUT/'sources',dest/'sources')
        shutil.copyfile(OUT/'external-snapshots.jsonl.gz',dest/'external-snapshots.jsonl.gz')
        stdout=io.StringIO()
        with patch.object(socket,'socket',side_effect=RuntimeError('network prohibited in replay')),redirect_stdout(stdout):
            m.run(dest)
        replay={name:canonical_hash(dest/name) for name in names}
        differences={name:compare_artifacts(load_artifact(OUT/name),load_artifact(dest/name),name) for name in names}
        (OUT/'offline-replay.log').write_text(stdout.getvalue(),encoding='utf8',newline='\n')
        # Flip ALL evaluation outcomes, preserving every announcement/price/history input.
        # The selected names, fitted coefficients, features, probabilities and picks must not move.
        def changed_read(path):
            rows=read_rows(path)
            if path.name=='mlb-snapshots.jsonl.gz':
                for r in rows:
                    if r['role']=='evaluation':
                        r['home_score'],r['away_score']=r['away_score'],r['home_score']
                        r['target']['hg'],r['target']['ag']=r['target']['ag'],r['target']['hg']
            elif path.name=='mlb-external-features.jsonl':
                for r in rows:r['hg'],r['ag']=r['ag'],r['hg']
            return rows
        with patch.object(socket,'socket',side_effect=RuntimeError('network prohibited in replay')),\
                patch.object(m,'read_rows',side_effect=changed_read),redirect_stdout(io.StringIO()):
            m.run(dest)
        compare_artifacts(load_artifact(OUT/'frozen-selection.json'),load_artifact(dest/'frozen-selection.json'))
        compare_artifacts(load_artifact(OUT/'inputs.jsonl.gz'),load_artifact(dest/'inputs.jsonl.gz'))
        mutated=read_rows(dest/'predictions.jsonl')
        for a,b in zip(predictions,mutated):
            assert a['gamePk']==b['gamePk']
            assert (a['hg'],a['ag'])==(b['ag'],b['hg'])
            compare_artifacts(a['probabilities'],b['probabilities'])
            compare_artifacts(a['candidate_probabilities'],b['candidate_probabilities'])
            assert {n:s['pick'] for n,s in a['ledger'].items()}=={n:s['pick'] for n,s in b['ledger'].items()}
    summary=dict(passed=True,independent_group_policy_checks=checked_groups,verified_games=len(inputs),
                 evaluation_games=len(predictions),candidate_count=12,socket_blocked_offline_replay=True,
                 canonical_output_sha256=expected,independent_savant_eb=True,
                 replay_canonical_sha256=replay,replay_max_float_absolute_delta=differences,
                 all_346_evaluation_outcomes_flipped_invariance_passed=True,
                 home_constant_diagnostic_picks_and_integer_profit_match=True,
                 floating_tolerance=dict(atol=1e-12,rtol=0),integer_pick_profit_comparison='exact')
    write_json(OUT/'verification.json',summary)
    print(json.dumps(summary),flush=True)


if __name__=='__main__':verify()
