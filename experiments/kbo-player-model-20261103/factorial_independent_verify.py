"""Independent fixed-model, three-way-score, and Decimal-settlement checks."""
import gzip,json,math
from decimal import Decimal
from pathlib import Path
P=Path(__file__).resolve().parent;DATA=Path('C:/Users/user/Documents/Codex/kbodata/experiments/kbo-player-data-20261103')
NAMES=('original_projected','official_batting_only','official_starter_only','official_batting_and_starter')
def rows(path):return {x['fixture_id']:x for x in (json.loads(z) for z in gzip.decompress(path.read_bytes()).splitlines())}
def scalar(key,v):
    if v is None:return None
    if 'lineup' in key:
        usable=[h for person in v for h in person['history'] or [] if all(type(h.get(k)) is int and h[k]>=0 for k in ('ab','hit','bb')) and h['hit']<=h['ab']];den=sum(h['ab']+h['bb'] for h in usable)
        return sum(h['hit']+h['bb'] for h in usable)/den if den else None
    usable=[h for h in v['appearances'] if all(type(h.get(k)) is int and h[k]>=0 for k in ('er','outs'))];outs=sum(h['outs'] for h in usable)
    return 27*sum(h['er'] for h in usable)/outs if outs else None

def rate(row,side,model):
    opp='away' if side=='home' else 'home';teams=model['teams'];features=row['features'];keys=[side+'_team_strength',opp+'_team_strength',side+'_rest_days',opp+'_rest_days','park_factor',side+'_lineup_strength',opp+'_starter_strength',opp+'_bullpen_load'];raw=[features.get(k,{}).get('value') for k in keys];meta=[1.,float(side=='home')]+[float(row[side+'_id']==t) for t in teams]+[float(row[opp+'_id']==t) for t in teams];norm=[(v-model['mean'][i])/model['scale'][i] if v is not None else 0. for i,v in enumerate(raw)];missing=[float(v is None) for v in raw];return math.exp(sum(a*b for a,b in zip(meta+norm+missing,model['beta'])))

def dist(h,a):
    hp=[math.exp(-h)];ap=[math.exp(-a)]
    for i in range(1,100):hp.append(hp[-1]*h/i);ap.append(ap[-1]*a/i)
    p=[0.,0.,0.]
    for i,x in enumerate(hp):
        for j,y in enumerate(ap):p[0 if i>j else 1 if i==j else 2]+=x*y
    return p

def main():
    pred=json.loads((P/'factorial/predictions.json').read_bytes());result=json.loads((P/'factorial/results.json').read_bytes());base=rows(P/'phase2/features.jsonl.gz');old=json.loads((P/'phase2/predictions.json').read_bytes())['models']['player'];over={n:rows(DATA/('official-full-2026-'+n+'.jsonl.gz')) for n in NAMES[1:]};labels=rows(P/'phase2/test-labels.jsonl.gz');max_rate=max_probability=0.
    for x in pred['predictions']:
        for name in NAMES:
            original=base[x['fixture_id']];row={**original,'features':dict(original['features'])}
            if name!='original_projected':
                changed=[k for k in ('home_lineup_strength','away_lineup_strength','home_starter_strength','away_starter_strength') if ('lineup' in k and name in ('official_batting_only','official_batting_and_starter')) or ('starter' in k and name in ('official_starter_only','official_batting_and_starter'))]
                for key in changed:row['features'][key]={'value':scalar(key,over[name][x['fixture_id']]['features'][key]['value'])}
            h=rate(row,'home',old);a=rate(row,'away',old);q=x['settings'][name];max_rate=max(max_rate,abs(h-q['home_rate']),abs(a-q['away_rate']));d=dist(h,a);max_probability=max(max_probability,*[abs(d[i]-q['probabilities_3way'][k]) for i,k in enumerate(('home','draw','away'))])
    if max_rate>1e-10 or max_probability>1e-9:raise ValueError('independent prediction mismatch')
    for group,g in result['results'].items():
        subset=[x for x in pred['predictions'] if group=='full_709' or group=='original_193' and x['cohort']=='original_193' or group=='new_516' and x['cohort']=='new_complement']
        for name in NAMES+('frozen_team',):
            hits=0;loss=brier=0.
            for x in subset:
                label=labels[x['fixture_id']];truth='home' if label['home_score']>label['away_score'] else 'away' if label['home_score']<label['away_score'] else 'draw';p=(x['frozen_team'] if name=='frozen_team' else x['settings'][name])['probabilities_3way'];hits+=max(('home','draw','away'),key=p.get)==truth;loss-=math.log(p[truth]);brier+=sum((p[k]-(k==truth))**2 for k in ('home','draw','away'))
            score=g['threeway']['models'][name];assert hits==score['hits'] and abs(loss/len(subset)-score['logloss_3way'])<1e-12 and abs(brier/len(subset)-score['brier_3way'])<1e-12
        for cohort,entries in result['price_ledgers'][group].items():
            for name in NAMES+('team','market'):
                net=Decimal(0);hits=voids=0;daily={};monthly={}
                for e in entries:
                    pr=e['forecasts'][name]['probabilities'];pick=('home','away')[max(range(2),key=lambda i:pr[i])];winner=e['winner'];hit=winner==pick;void=winner=='draw';z=Decimal(0) if void else Decimal(str(e['price_source']['prices'][pick]))-1 if hit else Decimal(-1);net+=z;hits+=hit;voids+=void;daily[e['date']]=daily.get(e['date'],Decimal(0))+z;month=e['date'][:7];monthly[month]=monthly.get(month,Decimal(0))+z
                balance=peak=dd=Decimal(0)
                for d in sorted(daily):balance+=daily[d];peak=max(peak,balance);dd=max(dd,peak-balance)
                metric=g['prices'][cohort]['models'][name];assert net==Decimal(metric['net_units']) and hits==metric['hits'] and voids==metric['voids'] and Decimal(metric['max_daily_close_drawdown_units'])==dd and {k:Decimal(v) for k,v in metric['monthly_units'].items()}==monthly and abs(float(net)/len(entries)-metric['roi_including_refunds'])<1e-12
    print(json.dumps({'independent_predictions':len(pred['predictions'])*4,'max_rate_error':max_rate,'max_threeway_error':max_probability,'verified_groups':3,'verified_price_ledgers':6,'decimal_monthly_drawdown_refunds':'passed'}))
if __name__=='__main__':main()
