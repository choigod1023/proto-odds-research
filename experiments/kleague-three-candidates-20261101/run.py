"""Compare frozen offline candidates on one shared, embargoed 2025 cohort."""
from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal
import hashlib,importlib,json,subprocess,sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[2];HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
CANDIDATES={'bayes_market_goal':'src.research_market_bayes',
            'moe_two_expert':'src.research_moe_two_expert','fl_glm':'src.research_fl_glm'}
PREDICTION_FIELDS=('id','day','home_id','away_id','prices','market_p','dc_p','dc_lh','dc_la','dc_rho',
                   'unknown_dc_team','market_lh','market_la','market_inversion_error','dc_reference','dc_training_cutoff')


def digest(path):return hashlib.sha256(Path(path).read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def save(path,obj):
    Path(path).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8',newline='\n')


def selection(p):
    p=np.asarray(p,dtype=float)
    if p.shape!=(3,) or not np.isfinite(p).all() or min(p)<=0 or abs(p.sum()-1)>1e-9:
        raise ValueError('invalid candidate probability')
    winners=np.flatnonzero(abs(p-max(p))<=1e-12)
    return int(winners[0]) if len(winners)==1 else None


def metrics(rows,model):
    daily=defaultdict(Decimal);monthly=defaultdict(Decimal);hits=bets=0;total=Decimal(0)
    changed=0
    for r in rows:
        f=r['forecasts'][model];n=Decimal(f['net']);total+=n;daily[r['day']]+=n;monthly[r['day'][:7]]+=n
        bets+=f['pick'] is not None;hits+=f['hit'];changed+=f['pick']!=r['forecasts']['market']['pick']
    balance=peak=dd=Decimal(0)
    for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
    p=np.array([r['forecasts'][model]['p'] for r in rows]);y=np.array([r['y'] for r in rows])
    return dict(games=len(rows),bets=bets,hits=hits,accuracy=hits/bets if bets else None,
                coverage=bets/len(rows),net_units=str(total),roi=float(total/bets) if bets else None,
                logloss=float(-np.log(p[np.arange(len(rows)),y]).mean()),
                brier=float(((p-np.eye(3)[y])**2).sum(1).mean()),changed_picks_vs_market=changed,
                daily_close_drawdown_units=str(dd),monthly_net={k:str(v) for k,v in sorted(monthly.items())})


def bootstrap(rows,model):
    blocks=defaultdict(lambda:np.zeros(4))
    for r in rows:
        a=r['forecasts']['market'];b=r['forecasts'][model];week=date.fromisoformat(r['day']).isocalendar()[:2]
        blocks[week]+=np.array([1,float(Decimal(b['net'])-Decimal(a['net'])),int(b['hit'])-int(a['hit']),
                                np.log(b['p'][r['y']])-np.log(a['p'][r['y']])])
    matrix=np.array(list(blocks.values()));rng=np.random.default_rng(20261101)
    sample=matrix[rng.integers(0,len(matrix),size=(10000,len(matrix)))].sum(1)
    return dict(weeks=len(matrix),roi_delta_per_opportunity_ci95=np.quantile(sample[:,1]/sample[:,0],[.025,.975]).tolist(),
                accuracy_delta_per_opportunity_ci95=np.quantile(sample[:,2]/sample[:,0],[.025,.975]).tolist(),
                logloss_improvement_ci95=np.quantile(sample[:,3]/sample[:,0],[.025,.975]).tolist(),
                classification='descriptive reused-cohort block bootstrap, not best-of-three confirmation')


def freeze_paths():
    return [HERE/x for x in ('protocol.json','oof.jsonl','input-manifest.json','run.py')]+[
        ROOT/'src'/x for x in ('research_market_bayes.py','research_moe_two_expert.py','research_fl_glm.py',
                              'research_kleague_dc.py','devig.py')]


def check_freeze(registration):
    for path in freeze_paths():
        data=subprocess.check_output(['git','show',registration+':'+path.relative_to(ROOT).as_posix()],cwd=ROOT)
        if data.replace(b'\r\n',b'\n')!=path.read_bytes().replace(b'\r\n',b'\n'):
            raise ValueError('unregistered code or inputs: '+str(path.relative_to(ROOT)))


def evaluate(rows,modules):
    history=[r for r in rows if r['split']=='meta_history_2024']
    test=sorted((r for r in rows if r['split']=='evaluation_2025'),key=lambda r:(r['day'],r['id']))
    if len(history)!=228 or len(test)!=227 or len({r['id'] for r in rows})!=455:raise ValueError('cohort changed')
    ledger=[];states=[]
    for month in sorted({r['day'][:7] for r in test}):
        reference=month+'-01';cutoff=(date.fromisoformat(reference)-timedelta(days=7)).isoformat()
        training=[r for r in rows if r['day']<=cutoff]
        for name,module in modules.items():
            state=module.fit(training,reference)
            if state['training_rows']!=len(training) or state['cutoff_inclusive']!=cutoff:raise ValueError('candidate training contract')
            states.append(dict(model=name,reference_day=reference,training_ids=[r['id'] for r in training],state=state))
        monthly={s['model']:s['state'] for s in states if s['reference_day']==reference}
        for row in (r for r in test if r['day'].startswith(month)):
            features={k:row[k] for k in PREDICTION_FIELDS}
            forecast={'market':(row['market_p'],{}),'dc':(row['dc_p'],{})}
            for name,module in modules.items():forecast[name]=module.predict(monthly[name],features)
            item={**row,'meta_reference':reference,'meta_training_cutoff':cutoff,'forecasts':{}}
            for name,(p,detail) in forecast.items():
                pick=selection(p);hit=pick is not None and pick==row['y']
                net=Decimal(0) if pick is None else Decimal(row['prices'][pick])-1 if hit else Decimal(-1)
                item['forecasts'][name]=dict(p=[float(x) for x in p],pick=pick,hit=hit,net=str(net),detail=detail)
            ledger.append(item)
        print(json.dumps(dict(month=month,training=len(training),evaluated=sum(r['day'].startswith(month) for r in test))),flush=True)
    return ledger,states


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--registration',required=True);args=parser.parse_args()
    check_freeze(args.registration)
    manifest=json.loads((HERE/'input-manifest.json').read_text(encoding='utf8'))
    if hashlib.sha256((HERE/'oof.jsonl').read_bytes()).hexdigest()!=manifest['features_sha256']:raise ValueError('feature byte hash mismatch')
    rows=[json.loads(s) for s in (HERE/'oof.jsonl').read_text(encoding='utf8').splitlines()]
    modules={name:importlib.import_module(path) for name,path in CANDIDATES.items()}
    ledger,states=evaluate(rows,modules)
    (HERE/'ledger.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n' for r in ledger),encoding='utf8',newline='\n')
    save(HERE/'states.json',states)
    result=dict(classification='exploratory_2025_reused_not_validation',registration_commit=args.registration,
                evaluation_games=len(ledger),range=[ledger[0]['day'],ledger[-1]['day']],
                models={name:metrics(ledger,name) for name in ('market','dc',*CANDIDATES)},
                uncertainty={name:bootstrap(ledger,name) for name in CANDIDATES},
                source_lf_sha256={p.relative_to(ROOT).as_posix():digest(p) for p in freeze_paths()},
                price_receipt_verified=False,tipster_expert=False,true_9_to_1_comparison=False,
                formal_validation=False,adopted=False,monthly_wallet_loss_cap_changed=False,
                accounting='All full-game 3-way prices, equal-unit singles, no monthly stop; NOT personal wallet replay')
    save(HERE/'results.json',result)
    print(json.dumps(result['models'],ensure_ascii=True))


if __name__=='__main__':main()
