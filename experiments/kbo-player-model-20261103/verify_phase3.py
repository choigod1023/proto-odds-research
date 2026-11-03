"""Independent Decimal/Brent replay and phase2 invariance checks; offline only."""
import argparse,collections,gzip,hashlib,json,math
from decimal import Decimal
from pathlib import Path
from scipy.optimize import brentq
P=Path(__file__).resolve().parent;data=json.loads((P/'phase3/overlay-results.json').read_text(encoding='utf8'));old=json.loads((P/'phase2/results.json').read_text(encoding='utf8'));frozen=json.loads((P/'phase2/predictions.json').read_text(encoding='utf8'))
with gzip.open(P/'phase2/test-labels.jsonl.gz','rt',encoding='utf8') as f:labels={x['fixture_id']:x for x in map(json.loads,f)}
models=('market','team','starter','lineup','bullpen','starter_lineup','player');pred={name:{x['fixture_id']:x for x in xs} for name,xs in frozen['predictions'].items()};checked=0;maxerr=0

def shin(values):
    q=[1/float(v) for v in values];s=sum(q)
    if s<=1:return [v/s for v in q]
    def probabilities(z):return [(math.sqrt(z*z+4*(1-z)*v*v/s)-z)/(2*(1-z)) for v in q]
    z=brentq(lambda z:sum(probabilities(z))-1,1e-15,.9,xtol=1e-14);v=probabilities(z);return [x/sum(v) for x in v]

for cohort,lines in data['ledgers'].items():
    for name in models:
        profit=Decimal(0);hits=voids=0
        for row in lines:
            label=labels[row['fixture_id']];h=label['home_score'];a=label['away_score'];winner='home' if h>a else 'away' if a>h else 'draw';prices=row['price_source']['prices']
            if name=='market':p=shin([prices['home'],prices['away']])
            else:
                fv=pred[name][row['fixture_id']]['probabilities_2way'];p=[fv['home'],fv['away']]
            selected=('home','away')[max(range(2),key=lambda i:p[i])];void=winner=='draw';hit=selected==winner and not void;net=Decimal(0) if void else Decimal(str(prices[selected]))-1 if hit else Decimal(-1)
            got=row['forecasts'][name];assert got['pick']==selected and got['hit']==hit and got['void']==void and Decimal(got['net'])==net
            maxerr=max(maxerr,max(abs(a-b) for a,b in zip(p,got['probabilities'])));profit+=net;hits+=hit;voids+=void;checked+=1
        stored=data['results']['cohorts'][cohort]['models'][name];assert stored['hits']==hits and stored['voids']==voids and Decimal(stored['net_units'])==profit
        assert not lines or abs(stored['roi_including_refunds']-float(profit/len(lines)))<1e-12
assert maxerr<1e-10
strata_path=P/'phase3/source-strata.json';strata=json.loads(strata_path.read_text(encoding='utf8'))
for cohort,lines in data['ledgers'].items():
    for origin in ('original_archived','official_restored'):
        subset=[r for r in lines if r['price_source']['origin']==origin];summary=strata[cohort][origin]
        assert summary['n']==len(subset)
        for name in models:
            hits=sum(r['forecasts'][name]['hit'] for r in subset)
            net=sum((Decimal(r['forecasts'][name]['net']) for r in subset),Decimal(0))
            assert summary['models'][name]['hits']==hits and Decimal(summary['models'][name]['net_units'])==net
extended={r['fixture_id']:r for r in data['ledgers']['extended_day_team_source_confirmed']}
for name in pred:
    before=old['archived_scenario_ledger'][name];assert len(before)==354
    for r in before:
        x=extended[r['fixture_id']];assert x['price_source']['origin']=='original_archived'
        for model in (name,'market'):
            a=r['forecasts'][model];b=x['forecasts'][model]
            assert a['pick']==b['pick'] and a['hit']==b['hit'] and a['void']==b['void'] and Decimal(a['net'])==Decimal(b['net'])
            assert max(abs(q-w) for q,w in zip(a['probabilities'],b['probabilities']))<1e-12
original_prices={r['fixture_id'] for r in extended.values() if r['price_source']['origin']=='original_archived'};assert len(original_prices)==354
source=Path('C:/Users/user/Documents/Codex/kbo-official-odds-20261103/experiments/kbo-official-odds-20261103')
raw=(source/'restored-prices.jsonl').read_bytes();assert hashlib.sha256(raw).hexdigest()=='d9fc2f04fabec1fa41d29521aeafdb96e676747e59723396f36ba5c88a9b8117'
restored=[json.loads(line) for line in raw.splitlines()];assert len(restored)==373
restored_draws=sum(r['score'].split(':')[0]==r['score'].split(':')[1] for r in restored);unknown_clock=sum(not r['source_kickoff_matched'] for r in restored)
assert restored_draws==56 and unknown_clock==249
out={'phase2_results_sha256':hashlib.sha256((P/'phase2/results.json').read_bytes()).hexdigest(),'phase2_predictions_sha256':hashlib.sha256((P/'phase2/predictions.json').read_bytes()).hexdigest(),'overlay_results_sha256':hashlib.sha256((P/'phase3/overlay-results.json').read_bytes()).hexdigest(),'official_restored_all_years':len(restored),'official_restored_draws_all_years':restored_draws,'official_without_source_clock_all_years':unknown_clock,'source_strata_sha256':hashlib.sha256(strata_path.read_bytes()).hexdigest(),'source_strata_match':True,'independent_decimal_settlements':checked,'maximum_probability_error':maxerr,'original_354_identical_in_all_six_models_and_market':True,'source_condition':'retrospective price availability not witnessed','scope':'offline replay, evidence authentication remains independent review'}
parser=argparse.ArgumentParser();parser.add_argument('--output');args=parser.parse_args()
if args.output:
    with Path(args.output).open('x',encoding='utf8',newline='\n') as f:json.dump(out,f,indent=2,sort_keys=True);f.write('\n')
print(json.dumps(out))
