"""Settle every recorded pick independently and audit chronological boundaries."""
from decimal import Decimal
import argparse
import hashlib
import json
from pathlib import Path


def verify(folder):
    result=json.loads((folder/'results.json').read_text(encoding='utf8'))
    model_code=Path(__file__).with_name('kleague_decay_backtest.py').read_bytes().replace(b'\r\n',b'\n')
    assert hashlib.sha256(model_code).hexdigest()==result['model_code_sha256_lf']
    for name,digest in result['input_hashes_lf'].items():
        assert hashlib.sha256((folder/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest
    rows=[json.loads(line) for line in (folder/'history.jsonl').read_text(encoding='utf8').splitlines()]
    lookup={(r['league'],r['date'],r['home'],r['away']):r for r in rows}
    assert len(lookup)==len(rows)
    targets=[json.loads(line) for line in (folder/'targets.jsonl').read_text(encoding='utf8').splitlines()]
    targetmap={t['fixture_id']:t for t in targets}
    assert len(targetmap)==len(result['ledger'])==result['primary_cohort_n']
    for row in result['ledger']:
        h=lookup[row['league'],row['date'],row['home'],row['away']]
        assert (h['hg'],h['ag'])==(row['hg'],row['ag'])
        source=row['player_feature']['latest_source_date']
        assert source is None or source<row['date']
        t=targetmap[row['fixture_id']]
        assert row['prices']==t['prices']
        assert row['models']['market90_tipster10']['pick']==t['picks']['market90_vote10']
        for family in ['team_dc','projected_xi_dc']:
            selected=result['selected_decay_from_validation'][family+':'+row['league']]
            assert row['models']['validation_selected_'+family]==row['models'][selected]
    for league,models in result['comparisons'].items():
        batch=[r for r in result['ledger'] if league=='all' or r['league']==league]
        for name,metrics in models.items():
            wins=0;net=Decimal(0)
            for row in batch:
                p=row['models'][name]['probabilities']
                assert abs(sum(p)-1)<1e-10 and all(0<v<1 for v in p)
                pick=row['models'][name]['pick']
                truth='home' if row['hg']>row['ag'] else 'away' if row['hg']<row['ag'] else 'draw'
                won=pick==truth;wins+=won
                net+=Decimal(str(row['prices'][['home','draw','away'].index(pick)]))*(1 if won else 0)-1
            assert wins==metrics['wins'] and len(batch)==metrics['n']
            assert abs(float(net/len(batch))-metrics['roi'])<1e-12
            assert int(net*10000)==metrics['net_per_10000_krw']
    fits=0
    for logs in result['fit_logs'].values():
        for stage,batch in logs.items():
            for log in batch:
                assert log['latest_training_date']<log['cutoff']
                assert log['cutoff'].startswith('2025' if stage=='validation' else '2026')
                assert log['converged'] and 0<log['effective_n']<=log['n']+1e-9
                fits+=1
    for chosen,model in result['selected_decay_from_validation'].items():
        family,league=chosen.split(':')
        eligible=[n for n,v in result['validation'].items() if n.startswith(family+'_') and v[league]['n']>=20]
        assert model==min(eligible,key=lambda n:(result['validation'][n][league]['log_loss'],n))
    assert result['monthly_loss_limit_applied'] is False
    return dict(fixtures=len(result['ledger']),models=len(result['comparisons']['all']),
        verified_optimization_runs=fits,independent_flat_stake_settlement=True,
        chronology_and_past_validation_selection=True,monthly_loss_cap_applied=False,
        live_pregame_lineup_versions_proven=False,independent_holdout=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--folder',type=Path,required=True)
    args=p.parse_args()
    checked=verify(args.folder)
    (args.folder/'verification.json').write_text(json.dumps(checked,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(checked))
