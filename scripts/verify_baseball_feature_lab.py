"""Independent saved-coefficient and Decimal settlement verification; no fitter import."""
from collections import defaultdict
from datetime import datetime,timedelta
from decimal import Decimal,ROUND_DOWN
from pathlib import Path
import argparse,hashlib,json,math


def read(path):return [json.loads(s) for s in path.read_text(encoding='utf8').splitlines() if s]


def probability(config,row,offset):
    if config is None:return offset
    z=[max(-6,min(6,(row['features'][n]-c)/s)) for n,c,s in zip(config['names'],config['center'],config['scale'])]
    x=list(z)
    if config['bundle']=='groups':
        for g in config['groups']:x.extend(v*row['groups'][g] for v in z)
    if config['bundle']=='nonlinear':x.extend(v*abs(v) for v in z)
    if config['link']=='intercept':x=[1.]+x
    eta=sum(v*b for v,b in zip(x,config['beta']))
    if config['link']=='market_offset':eta+=math.log(offset/(1-offset))
    return 1/(1+math.exp(-eta)) if eta>=0 else math.exp(eta)/(1+math.exp(eta))


def settlement(rows,ps,keep=None):
    keep=[True]*len(rows) if keep is None else keep
    wins=pushes=net=n=0
    for r,p,k in zip(rows,ps,keep):
        if not k:continue
        n+=1;home=p>=.5
        if r['hg']==r['ag']:pushes+=1;continue
        correct=home==(r['hg']>r['ag']);wins+=int(correct)
        t=r.get('target');prices=t['prices'] if t else r['historical_prices'];order=t['order'] if t else ['home','away']
        net+=(int((Decimal(str(prices[order.index('home' if home else 'away')]))*10000).to_integral_value(rounding=ROUND_DOWN))-10000) if correct else -10000
    return n,wins,pushes,net


def mask(name,rows,ps):
    market=[r['market_probability'] for r in rows]
    if name=='all':return [True]*len(rows)
    if name=='agreement':return [(p>=.5)==(m>=.5) for p,m in zip(ps,market)]
    base=market if name.startswith('market') else ps;t=int(name[-2:])/100
    return [max(v,1-v)>=t for v in base]


def assert_metrics(metric,settled,total):
    n,wins,pushes,net=settled
    assert (metric['n'],metric['wins'],metric['pushes'],metric['net_krw'])==(n,wins,pushes,net)
    assert abs(metric['roi']-net/(10000*n))<1e-10
    assert abs(metric['accuracy']-wins/(n-pushes))<1e-12 and abs(metric['coverage']-n/total)<1e-12


def verify(folder):
    report=json.loads((folder/'results.json').read_text(encoding='utf8'));rows=read(folder/'predictions.jsonl');facts=read(folder/'mlb-feature-facts.jsonl')
    assert len(rows)==len({r['gamePk'] for r in rows})==116
    for n,digest in report['input_sha256'].items():assert hashlib.sha256((folder/n).read_bytes().replace(b'\r\n',b'\n')).hexdigest()==digest
    for r in facts:assert datetime.fromisoformat(r['snapshot_at'])<=datetime.fromisoformat(r['start_at'])-timedelta(minutes=10)
    assert report['train_dates'][1]<report['validation_dates'][0]<=report['validation_dates'][1]<min(r['date'] for r in rows)
    models=report['models'];expected={
        'logloss':min(models,key=lambda n:(models[n]['validation']['logloss'],models[n]['validation']['brier'],n)),
        'accuracy':min(models,key=lambda n:(-models[n]['validation']['accuracy'],models[n]['validation']['brier'],n))}
    assert expected==report['selected']
    replay=checked=0
    for n,model in models.items():
        ps=[probability(report['fit_params'][n]['final_fit'],r,r['market_probability']) for r in rows]
        assert all(abs(p-r['probabilities'][n])<1e-12 for p,r in zip(ps,rows));replay+=len(rows)
        assert_metrics(model['evaluation'],settlement(rows,ps),len(rows));checked+=1
    baseline=[r['existing91_probability'] for r in rows]
    for obj,name in expected.items():
        p=[r['probabilities'][name] for r in rows];config=report['fit_params'][name]['final_fit']
        gs=report['gates'][obj];valid=[g for g,m in gs.items() if m['validation']['n']>=20 and m['validation']['coverage']>=.5]
        selected=min(valid,key=lambda g:(-gs[g]['validation']['accuracy'],gs[g]['validation']['brier'],-gs[g]['validation']['coverage'],g))
        assert selected==report['selected_gates'][obj]
        transfer=[probability(config,r,r['existing91_probability']) for r in rows] if config is None or config['link']=='market_offset' else [.9*b+.1*a for b,a in zip(baseline,p)]
        for suffix,ps,keep in [('all',p,[True]*len(rows)),('selected_gate',p,mask(selected,rows,p)),('existing91_transfer',transfer,[True]*len(rows))]:
            label=obj+':'+suffix;m=report['policies'][label]
            assert_metrics(m['actual'],settlement(rows,ps,keep),len(rows));assert_metrics(m['matched_existing91'],settlement(rows,baseline,keep),len(rows));checked+=1
    movement=read(folder/'price-movement-facts.jsonl')
    for r in movement:
        for name in ['first','last']:assert datetime.fromisoformat(r[name]['observed_at'])<datetime.fromisoformat(r[name]['start_at'])-timedelta(minutes=10)
    movement_report=json.loads((folder/'price-movement-audit.json').read_text(encoding='utf8'))
    for league,parts in movement_report['results'].items():
        batch=[r for r in movement if r['league']==league]
        assert 'movement_direction' not in parts['all']
        assert parts['any_nonzero_move']['movement_direction']['n']==sum(abs(r['delta_home_probability'])>1e-9 for r in batch)
    outputs={f.name:hashlib.sha256(f.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for f in folder.iterdir() if f.suffix in ['.json','.jsonl'] and f.name!='verified-sha256.json'}
    (folder/'verified-sha256.json').write_text(json.dumps(outputs,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(dict(ok=True,fixtures=len(rows),probabilities_replayed=replay,independent_settlements=checked,movement_fixtures=len(movement))))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);verify(p.parse_args().folder)
