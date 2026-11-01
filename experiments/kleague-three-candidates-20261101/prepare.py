"""Build same-source 2024 warmup and 2025 OOF inputs; no meta-model fitting."""
from collections import defaultdict
from pathlib import Path
import csv,gzip,hashlib,importlib.util,json,sys,re
from datetime import datetime
import numpy as np
from scipy.optimize import least_squares
from scipy.stats import skellam

ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))

def parse_2024(row):
    """Separate year-scoped parser; never rewrite the frozen 2025 audit."""
    if not (row['year']=='2024' and row['sport']=='sc' and row['league']=='K리그1'
            and row['market_family']=='승무패' and not row['market_label'] and row['n_way']=='3'):
        return None
    home=re.fullmatch(r'(.+?)\s+(\d+)',row['home'])
    away=re.fullmatch(r'(\d+)\s+(.+)',row['away'])
    clock=re.fullmatch(r'(\d{2})\.(\d{2})\([^)]*\)\s+(\d{2}):(\d{2})',row['date_text'])
    if not home or not away or not clock:raise ValueError('unparseable 2024 eligible raw row')
    dt=datetime(2024,*map(int,clock.groups()))
    return dict(home=home[1],away=away[2],home_score=int(home[2]),away_score=int(away[1]),
                day=dt.date().isoformat(),kickoff=dt.isoformat())


def save(path,obj):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8',newline='\n')


def implied_rates(p):
    def probs(logrates):
        lh,la=np.exp(logrates)
        return np.array([skellam.sf(0,lh,la),skellam.pmf(0,lh,la)])
    target=np.array(p[:2])
    opt=least_squares(lambda x:probs(x)-target,np.log([1.4,1.1]),
          bounds=(np.log([.01,.01]),np.log([10.,10.])),xtol=1e-12,ftol=1e-12,gtol=1e-12,max_nfev=200)
    err=float(np.max(np.abs(probs(opt.x)-target)))
    if not opt.success or err>1e-6:raise ValueError('market score inversion failed')
    return np.exp(opt.x).tolist(),err


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--raw',type=Path,required=True);args=parser.parse_args()
    old=ROOT/'experiments/kleague-roi-diagnostic-20261101/run.py'
    spec=importlib.util.spec_from_file_location('fixed_dc_diagnostic',old);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    source=ROOT/'experiments/kleague-roi-diagnostic-20261101/training/fixtures.jsonl'
    history,test,hashes=m.load_inputs(args.raw,source)
    if len(history)!=684 or len(test)!=227:raise ValueError('changed base cohort')
    mapping=json.loads((ROOT/'experiments/verified-cohort-20261101/kleague1/team-map.json').read_text(encoding='utf8'))
    names={k:v['domestic'] for k,v in mapping.items()};names['K18']='인천유나'
    official=[json.loads(s) for s in source.read_text(encoding='utf8').splitlines() if json.loads(s)['year']==2024]
    with gzip.open(ROOT/'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz','rt',encoding='utf8') as f:
        norm=[r for r in map(json.loads,f) if r['league']=='K리그1' and r['day'].startswith('2024-')]
    index=defaultdict(list)
    for r in norm:index[(r['day'],r['home'],r['away'])].append(r)
    raw_index=defaultdict(list)
    with args.raw.open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            if (r:=parse_2024(row)) is not None:
                raw_index[(r['day'],r['home'],r['away'])].append((r,row))
    warm=[];crosswalk=[]
    for f in official:
        key=(f['day'],names[f['home_team_id']],names[f['away_team_id']]);n=index[key];raw=raw_index[key]
        if len(n)!=1 or len(raw)!=1:raise ValueError('2024 oriented fixture identity missing/ambiguous: '+str(key))
        n=n[0];r,row=raw[0]
        if (r['home_score'],r['away_score'])!=(f['scores']['home'],f['scores']['away']):raise ValueError('2024 score conflict')
        if r['kickoff']!=f['kickoff'] or n['kickoff']!=f['kickoff']:raise ValueError('2024 local clock conflict')
        if [float(x) for x in row['odds'].split(',')]!=n['odds'] or row['is_void']!='False':raise ValueError('2024 price/void conflict')
        model=m.official(f);y=0 if model['hg']>model['ag'] else 1 if model['hg']==model['ag'] else 2
        if n['y']!=y:raise ValueError('2024 result mismatch')
        warm.append({**model,'home':n['home'],'away':n['away'],'prices':[str(x) for x in n['odds']],'y':y})
        crosswalk.append(dict(official_id=model['id'],domestic_id=n['id'],day=model['day'],
            home_id=model['home_id'],away_id=model['away_id'],home=n['home'],away=n['away'],identity='unique_date_oriented_team_local_clock; score checked after identity'))
    if len(warm)!=228 or len({r['id'] for r in warm})!=228:raise ValueError('2024 coverage mismatch')
    features=[];states={}
    for split,evaluation in [('meta_history_2024',warm),('evaluation_2025',test)]:
        rows,ss=m.forecasts(history,evaluation);states[split]=ss
        for r in rows:
            rates,err=implied_rates(r['forecasts']['market']['p'])
            features.append(dict(id=r['id'],split=split,day=r['day'],home_id=r['home_id'],away_id=r['away_id'],
                hg=r['hg'],ag=r['ag'],y=r['y'],prices=r['prices'],market_p=r['forecasts']['market']['p'],dc_p=r['forecasts']['time_weighted_dc']['p'],
                dc_lh=r['model_detail']['lambda_home'],dc_la=r['model_detail']['lambda_away'],dc_rho=next(s['rho'] for s in ss if s['reference_day'][:7]==r['fit_month']),
                unknown_dc_team=bool(r['model_detail']['unknown_teams']),market_lh=rates[0],market_la=rates[1],market_inversion_error=err,
                dc_reference=r['fit_month']+'-01',dc_training_cutoff=r['training_cutoff']))
    features.sort(key=lambda r:(r['day'],r['id']))
    (HERE/'oof.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in features),encoding='utf8',newline='\n')
    save(HERE/'base-states.json',states);save(HERE/'2024-crosswalk.json',crosswalk)
    save(HERE/'input-manifest.json',dict(base_hashes=hashes,features_sha256=hashlib.sha256((HERE/'oof.jsonl').read_bytes()).hexdigest(),
        rows=len(features),warmup=228,evaluation=227,maximum_market_inversion_error=max(r['market_inversion_error'] for r in features),
        identity_note='2025 curated stable IDs plus explicit K18=인천유나; 2024 all228 verified by date, oriented names, local clock, then score/price. No future performance field used for identity.',
        price_receipt_verified=False,tipster_inputs_available=False,formal_validation=False))
    print(json.dumps({'rows':len(features),'warmup':228,'evaluation':227,'maximum_market_inversion_error':max(r['market_inversion_error'] for r in features)}))


if __name__=='__main__':main()
