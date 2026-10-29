"""Independently settle published probabilities without importing the fitter."""
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
import argparse
import hashlib
import json


def verify(folder, replay=False):
    result=json.loads((folder/'results.json').read_text(encoding='utf8'))
    for name,digest in result['input_sha256'].items():
        assert hashlib.sha256((folder/name).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest
    rows=[json.loads(s) for s in (folder/'predictions.jsonl').read_text(encoding='utf8').splitlines()]
    finals=[json.loads(s) for s in (folder/'mlb-official-final.jsonl').read_text(encoding='utf8').splitlines()]
    ambiguous=[json.loads(s) for s in (folder/'mlb-ambiguous-suspended.jsonl').read_text(encoding='utf8').splitlines()]
    ids={r['gamePk'] for r in finals}
    assert len(ids)==len(finals) and not ids.intersection(r['gamePk'] for r in ambiguous)
    assert all(r['game_type'] in {'R','F','D','L','W'} for r in finals)
    assert len({(r['league'],r['fixture_id']) for r in rows})==len(rows)==434
    checked=0
    for league, experiment in result['leagues'].items():
        cohort=[r for r in rows if r['league']==league]
        val=experiment['validation']
        eligible=[c for c,v in val.items() if v.get('eligible',True)]
        assert experiment['selected']['hit']==min(eligible,key=lambda c:(-val[c]['accuracy'],val[c]['brier'],c))
        assert experiment['selected']['brier']==min(eligible,key=lambda c:(val[c]['brier'],-val[c]['accuracy'],c))
        def settle(probabilities):
            wins=pushes=net=0
            for r,p in zip(cohort,probabilities):
                assert 0<=p<=1
                side='home' if p>=.5 else 'away'
                if r['hg']==r['ag']:
                    pushes+=1;continue
                winner='home' if r['hg']>r['ag'] else 'away'
                if side==winner:
                    wins+=1
                    net+=int((Decimal(str(r['prices'][r['order'].index(side)]))*10000).to_integral_value(rounding=ROUND_DOWN))-10000
                else:net-=10000
            return wins,pushes,net
        policies={}
        for baseline,field in [('market','market_home_probability'),('market90_vote10','existing91_home_probability')]:
            policies[baseline]=[r[field] for r in cohort]
        policies['tipster_vote']=[r['vote_counts'].get('home',0)/r['voters'] for r in cohort]
        for name in val:
            ps=[r['new_probabilities'][name] for r in cohort]
            wins,pushes,net=settle(ps)
            m=experiment['all_candidate_evaluation'][name]
            assert (wins,pushes,net)==(m['wins'],m['pushes'],m['net_krw'])
            checked+=1
        for reason,name in experiment['selected'].items():
            p=[r['new_probabilities'][name] for r in cohort]
            policies[reason+':pure']=p
            policies[reason+':market90_new10']=[.9*r['market_home_probability']+.1*x for r,x in zip(cohort,p)]
            policies[reason+':existing91_90_new10']=[.9*r['existing91_home_probability']+.1*x for r,x in zip(cohort,p)]
        for policy,ps in policies.items():
            wins,pushes,net=settle(ps);m=experiment['policies'][policy]
            assert (wins,pushes,net)==(m['wins'],m['pushes'],m['net_krw'])
            assert abs(m['roi']-net/(10000*len(cohort)))<1e-10
            assert abs(m['accuracy']-wins/(len(cohort)-pushes))<1e-10
            checked+=1
    audit=[json.loads(s) for s in (folder/'feature-audit.jsonl').read_text(encoding='utf8').splitlines()]
    assert len(audit)==868
    for a in audit:
        for field in ['latest_team_date','latest_lineup_date']:
            assert not a.get(field) or a[field]<a['date']
    for models in result['fit_catalog'].values():
        for cutoff,m in models.items():
            assert m['train_last_date'][:4]<=cutoff and m['success']
    replayed=0
    if replay:
        import numpy as np
        from backtest_baseball_league_fit import build_features, key, predict
        history=[json.loads(s) for s in (folder/'history.jsonl').read_text(encoding='utf8').splitlines()]
        for league,experiment in result['leagues'].items():
            cohort=[r for r in rows if r['league']==league]
            for name in set(experiment['selected'].values()):
                window=int(name.split('-w')[1].split('-')[0])
                features={key(r['row']):r for r in build_features(history,league,window)}
                for year in sorted({int(r['date'][:4]) for r in cohort}):
                    batch=[r for r in cohort if int(r['date'][:4])==year]
                    model=result['fit_catalog'][league+':'+name][str(year-1)]
                    calculated=predict(model,[features[key(r)] for r in batch])
                    np.testing.assert_allclose(calculated,[r['new_probabilities'][name] for r in batch],rtol=0,atol=1e-12)
                    replayed+=len(batch)
    print(json.dumps(dict(fixtures=len(rows),independent_settlements=checked,feature_sides=len(audit),ok=True)))
    if replay:print(json.dumps(dict(saved_parameter_probability_replays=replayed,ok=True)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--replay',action='store_true');args=p.parse_args()
    verify(args.folder,args.replay)
