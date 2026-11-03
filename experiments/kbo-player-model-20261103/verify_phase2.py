"""Independent scalar replay of fixed forecasts and archived-price settlements."""
import collections,copy,gzip,hashlib,importlib.util,json,math,subprocess,sys
from decimal import Decimal
from pathlib import Path
from scipy.optimize import brentq
P=Path(__file__).resolve().parent/'phase2'
def rows(name):
    with gzip.open(P/name,'rt',encoding='utf8') as f:return [json.loads(x) for x in f if x.strip()]
raw=rows('features.jsonl.gz');labels=rows('test-labels.jsonl.gz');pred=json.loads((P/'predictions.json').read_text(encoding='utf8'));results=json.loads((P/'results.json').read_text(encoding='utf8'))
fixtures={r['fixture_id']:r for r in raw if r['season']==2026};ys={l['fixture_id']:l for l in labels};assert set(fixtures)==set(ys) and len(ys)==726
rates={};error=0;checks=0

def shin(prices):
    x=[1/float(v) for v in prices];total=sum(x)
    if total<=1:return [v/total for v in x]
    def vec(z):return [(math.sqrt(z*z+4*(1-z)*p*p/total)-z)/(2*(1-z)) for p in x]
    z=brentq(lambda z:sum(vec(z))-1,1e-15,.9,xtol=1e-14);p=vec(z);return [v/sum(p) for v in p]

for mode,items in pred['predictions'].items():
    ledger=results['archived_scenario_ledger'][mode];stored={x['fixture_id']:x for x in ledger};hits=0;net=Decimal(0);ll=[];bs=[]
    for forecast in items:
        ident=forecast['fixture_id'];r=fixtures[ident];y=ys[ident];h=y['home_score'];a=y['away_score'];cls=0 if h>a else 1 if h==a else 2
        p=forecast['probabilities_3way'];v=[p['home'],p['draw'],p['away']];ll.append(-math.log(v[cls]));bs.append(sum((x-int(i==cls))**2 for i,x in enumerate(v)))
        market=r['market']
        if market is None:assert ident not in stored;continue
        assert ident in stored and market['kind']=='full_game_2way' and market['settlement_rule']=='draw_void_refund'
        x=stored[ident];pv=[forecast['probabilities_2way']['home'],forecast['probabilities_2way']['away']];mv=shin([market['selections']['home'],market['selections']['away']]);winner='home' if h>a else 'away' if a>h else 'draw'
        for key,probs in [(mode,pv),('market',mv)]:
            chosen=('home','away')[max(range(2),key=lambda i:probs[i])];win=chosen==winner;value=Decimal(0) if winner=='draw' else Decimal(str(market['selections'][chosen]))-1 if win else Decimal(-1)
            f=x['forecasts'][key];assert f['pick']==chosen and f['hit']==win and f['void']==(winner=='draw') and Decimal(f['net'])==value
            error=max(error,max(abs(z-w) for z,w in zip(probs,f['probabilities'])));checks+=1
            if key==mode:hits+=win;net+=value
    reported=results['archived_price_scenario']['models'][mode]['candidate'];diag=results['verified_results']['diagnostics'][mode]
    assert reported['bets']==len(stored) and reported['hits']==hits and Decimal(reported['net_units'])==net
    assert abs(diag['all_final_3way_logloss']-sum(ll)/len(ll))<1e-12 and abs(diag['all_final_3way_brier']-sum(bs)/len(bs))<1e-12
    rates[mode]={'n':len(stored),'hits':hits,'net_units':str(net)}
assert error<1e-10
# The frozen code was executed with CRLF source bytes on Windows. Reconstruct
# those bytes from the pre-fit Git commit to verify them on any host.
for key,path in [('model','experiments/kbo-player-model-20261103/model.py'),('run','experiments/kbo-player-model-20261103/run.py'),('devig','src/devig.py')]:
    blob=subprocess.check_output(['git','show','5cb48043:'+path]);crlf=blob.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
    assert hashlib.sha256(crlf).hexdigest()==pred['code_sha256'][key]
# Perturb only held-out labels and confirm scoring cannot mutate frozen forecasts.
spec=importlib.util.spec_from_file_location('kbo_score_poison_check',P.parent/'run.py')
runner=importlib.util.module_from_spec(spec);sys.modules[spec.name]=runner;spec.loader.exec_module(runner)
before=hashlib.sha256(json.dumps(pred,sort_keys=True).encode()).hexdigest();poison=copy.deepcopy(labels)
for label in poison:label['home_score'],label['away_score']=label['away_score']+7,label['home_score']
runner.report(raw,pred,poison,assume_prior_calendar_day=True,archived_price_scenario=True)
assert hashlib.sha256(json.dumps(pred,sort_keys=True).encode()).hexdigest()==before
priced=[(r,ys[r['fixture_id']]) for r in fixtures.values() if r['market'] is not None]
counts=collections.Counter('draw' if y['home_score']==y['away_score'] else 'non_draw' for r,y in priced)
output={'source_predictions_sha256':hashlib.sha256((P/'predictions.json').read_bytes()).hexdigest(),'results_sha256':hashlib.sha256((P/'results.json').read_bytes()).hexdigest(),'fixtures':len(fixtures),'price_matched':len(priced),'priced_results':dict(counts),'total_draws':sum(y['home_score']==y['away_score'] for y in ys.values()),'price_date_range':[min(r['event_date'] for r,y in priced),max(r['event_date'] for r,y in priced)],'independent_decimal_settlements':checks,'poisoned_test_labels':len(poison),'predictions_unchanged_after_poison':True,'max_probability_error':error,'frozen_code_reconstructed_from_git_commit':'5cb48043 CRLF verified','models':rates,'limits':'Forecasts and scores are retrospective; archive price and final result are not independently authenticated.'}
(P/'verification.json').write_text(json.dumps(output,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf8',newline='\n');print(json.dumps(output,ensure_ascii=False))
