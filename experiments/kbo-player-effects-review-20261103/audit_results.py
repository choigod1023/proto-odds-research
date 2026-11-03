"""Independently score frozen 2026 predictions and the specified draw diagnostic."""
import collections,gzip,hashlib,json,math,subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path
import numpy as np
import audit_math as ref
from audit_frozen import WORKER,ROOT,COMMIT,SEL,DATA,MODEL,git,index,dec

RESULT='2156ec6b2c7c54a104c051c5cddc5a9d0b9d46f7';ODDS=Path('C:/Users/user/Documents/Codex/kbo-official-odds-20261103')
def get(path,expected=None):
    b=git(WORKER,RESULT,ROOT/path)
    if expected:assert hashlib.sha256(b).hexdigest()==expected,path
    return b
def close(a,b):assert abs(a-b)<1e-11,(a,b)
def winner(y):return 'home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw'
def choose(p):return max(('home','draw','away'),key=p.get)
def score(p,y):return (int(choose(p)==y),-math.log(max(p[y],1e-15)),sum((p[k]-int(k==y))**2 for k in ('home','draw','away')))
def intervals(bucket,columns):
    arr=np.array([bucket[k] for k in sorted(bucket)]);rng=np.random.default_rng(20261103);out=arr[rng.integers(0,len(arr),size=(5000,len(arr)))].sum(axis=1)
    return [np.quantile(out[:,i]/out[:,den],[.025,.975]) for i,den in columns]
def check_diagnostic(preds,labels,expected):
    rows=[{'probabilities':p['probabilities_3way'],'winner':winner(labels[p['fixture_id']])} for p in preds];calc=ref.draw_diagnostic(rows)
    for source,target in [('n','n'),('observed_draws','actual_draws'),('observed_draw_rate','actual_draw_rate'),('mean_draw_probability','mean_predicted_draw_probability'),('decisive_n','decisive'),('decisive_hits','decisive_hits'),('conditional_accuracy','decisive_accuracy'),('conditional_brier','decisive_brier'),('conditional_logloss','decisive_logloss')]:close(calc[source],expected[target])
    n=len(rows);draw_loss=sum(-math.log(max(r['probabilities']['draw'] if r['winner']=='draw' else 1-r['probabilities']['draw'],1e-15)) for r in rows)/n
    decisive_loss=sum(-math.log(max(r['probabilities'][r['winner']]/(1-r['probabilities']['draw']),1e-15)) for r in rows if r['winner']!='draw')/n
    close(draw_loss,expected['draw_event_logloss_per_game']);close(decisive_loss,expected['decisive_direction_logloss_per_game'])
    return calc

def main():
    assert subprocess.run(['git','merge-base','--is-ancestor',COMMIT,RESULT],cwd=WORKER).returncode==0
    pred=json.loads(git(WORKER,COMMIT,ROOT/'artifacts/predictions.json'));assert hashlib.sha256(git(WORKER,COMMIT,ROOT/'artifacts/predictions.json')).hexdigest()=='ba1ed43123a58535f2ee997de4181931449008b0e9bbbba4ba317245c7525fa9'
    results=json.loads(get('artifacts/results.json','211b89d365136a166fd13026df5f6358e4b7ecda5db50b4459a2f42e75f19cd8'));diag=json.loads(get('artifacts/draw-diagnostic.json','838f89cf8c1e633fb8e67fbcc0b2315a5b0ee10f24a0e91a9d6d7e32b8515877'));manifest=json.loads(get('inputs/evaluation-manifest.json','d6073a4d9c05b2c7ccfdba762bfbdcd706bd748308d945c251b4ab68edcc3ca9'))
    assert results['prediction_freeze']==COMMIT and results['selected_family']==pred['selected_family']=='M2' and diag['selection_unchanged'] is True
    artifacts={k:get('inputs/'+k,v) for k,v in manifest['files'].items()}
    assert artifacts['test-labels.jsonl.gz']==git(MODEL,'b160d3d1',Path('experiments/kbo-player-model-20261103/phase2/test-labels.jsonl.gz'))
    assert artifacts['price-ledgers.json']==git(MODEL,'b160d3d1',Path('experiments/kbo-player-model-20261103/phase3/overlay-results.json'))
    assert artifacts['old-fresh-predictions.json']==git(MODEL,'c91cc13d',Path('experiments/kbo-player-model-20261103/fresh-history/predictions.json'))
    assert artifacts['price-reject-audit.jsonl.gz']==git(ODDS,'9c7986bc',Path('experiments/kbo-official-odds-20261103/audit-original-2026/audit-original-2026-prices.jsonl.gz'))
    assert hashlib.sha256(gzip.decompress(artifacts['source-labels.jsonl.gz'])).hexdigest()=='ef32ffe806b41521f5bd53c12d0eea1362bd44293181fc3ebb7c24c6fcb249ca'
    labels=index(dec(artifacts['test-labels.jsonl.gz']));source=index(dec(artifacts['source-labels.jsonl.gz']));audit=index(dec(artifacts['price-reject-audit.jsonl.gz']));prices=json.loads(artifacts['price-ledgers.json'])['ledgers'];old=index(json.loads(artifacts['old-fresh-predictions.json'])['predictions'])
    assert len(labels)==726 and all((v['home_score'],v['away_score'])==(source[fid]['home_score'],source[fid]['away_score']) for fid,v in labels.items())
    selection=json.loads(git(WORKER,SEL,ROOT/'artifacts/selection.json'));dev=index(dec(git(WORKER,COMMIT,ROOT/'inputs/development-labels.jsonl.gz')))
    diag_summary={'validation_2025':{},'diagnostic_2026':{}}
    for name in ('M0','M1','M2'):
        diag_summary['validation_2025'][name]=check_diagnostic(selection['outer']['models'][name]['predictions'],dev,diag['validation_2025'][name])
    summary={}
    for scenario,models in pred['scenarios'].items():
        by={k:index(v) for k,v in models.items()};ids=set(by['M0']);assert len(ids)==709 and all(set(x)==ids for x in by.values())
        oldname='original_projected' if scenario=='projected' else 'official_batting_and_starter';by['old_fixed']={fid:old[fid]['settings'][oldname] for fid in ids}
        metric=results['scenarios'][scenario]['threeway'];assert metric['n']==709;three={'n':709,'models':{}}
        for name,items in by.items():
            acc=np.zeros(3);buckets=collections.defaultdict(lambda:np.zeros(4))
            for fid in sorted(ids,key=lambda x:(by['M0'][x]['event_date'],x)):
                y=winner(labels[fid]);p=items[fid]['probabilities_3way'];scores=np.asarray(score(p,y));acc+=scores
                if name!='M0':buckets[date.fromisoformat(by['M0'][fid]['event_date']).isocalendar()[:2]]+=np.r_[1.,scores-np.asarray(score(by['M0'][fid]['probabilities_3way'],y))]
            saved=metric['models'][name];assert saved['hits']==int(acc[0]);
            for i,k in enumerate(('accuracy','logloss','brier')):close(saved[k],acc[i]/709)
            if name!='M0':
                saved_ci=metric['versus_M0'][name];assert saved_ci['weeks']==len(buckets)
                for ci,k in zip(intervals(buckets,[(1,0),(2,0),(3,0)]),('accuracy_delta_ci95','logloss_delta_ci95','brier_delta_ci95')):
                    for a,b in zip(ci,saved_ci[k]):close(a,b)
            three['models'][name]={'hits':saved['hits'],'logloss':saved['logloss'],'brier':saved['brier']}
            if name!='old_fixed':diag_summary['diagnostic_2026'].setdefault(scenario,{})[name]=check_diagnostic(list(items.values()),labels,diag['diagnostic_2026'][scenario][name])
        summary[scenario]={'threeway':three,'prices':{}}
        for key,ledger in results['price_ledgers'][scenario].items():
            basekey=key.removesuffix('_current_buy_reject_excluded');restricted=key.endswith('_current_buy_reject_excluded');prior={x['fixture_id']:x for x in prices[basekey] if x['fixture_id'] in ids}
            expected={fid for fid,x in prior.items() if not restricted or x['price_source']['origin']=='official_restored' or audit[fid]['official_result']['buyReject']=='0'}
            assert len(ledger)==len(expected) and {x['fixture_id'] for x in ledger}==expected and len(expected)=={'extended_day_team_source_confirmed':667,'strict_original_time_match':439,'extended_day_team_source_confirmed_current_buy_reject_excluded':660,'strict_original_time_match_current_buy_reject_excluded':432}[key]
            m=results['scenarios'][scenario]['prices'][key];assert m['n']==len(ledger) and m['refunds']==sum(x['winner']=='draw' for x in ledger)
            data={'n':len(ledger),'refunds':m['refunds'],'net':{}}
            for name in ('M0','M1','M2','old_fixed','team','market'):
                net=Decimal(0);daily=collections.defaultdict(Decimal);monthly=collections.defaultdict(Decimal);hits=voids=0;loss=[];brier=[]
                for x in ledger:
                    fid=x['fixture_id'];z=x['forecasts'][name];original=prior[fid];y=winner(labels[fid]);assert x['winner']==y and x['price_source']==original['price_source']
                    rejected=original['price_source']['origin']!='official_restored' and audit[fid]['official_result']['buyReject']!='0';assert x['current_buy_reject']==rejected
                    p=z['probabilities'];pick,amount=ref.settlement(p,[x['price_source']['prices']['home'],x['price_source']['prices']['away']],y)
                    assert z['pick']==pick and z['void']==(y=='draw') and z['hit']==(pick==y) and Decimal(z['net'])==amount
                    if name in by:
                        q=by[name][fid]['probabilities_2way'];close(p[0],q['home']);close(p[1],q['away'])
                    else:assert z==original['forecasts'][name]
                    net+=amount;daily[x['date']]+=amount;monthly[x['date'][:7]]+=amount;hits+=z['hit'];voids+=z['void']
                    if y!='draw':
                        j=0 if y=='home' else 1;loss.append(-math.log(max(p[j],1e-15)));brier.append(sum((v-int(i==j))**2 for i,v in enumerate(p)))
                equity=peak=dd=Decimal(0)
                for day in sorted(daily):equity+=daily[day];peak=max(peak,equity);dd=max(dd,peak-equity)
                saved=m['models'][name];assert (saved['bets'],saved['hits'],saved['voids'],saved['decisive'])==(len(ledger),hits,voids,len(ledger)-voids)
                assert Decimal(saved['net_units'])==net and Decimal(saved['max_daily_close_drawdown_units'])==dd and {k:Decimal(v) for k,v in saved['monthly_units'].items()}==monthly
                close(saved['roi_including_refunds'],float(net/len(ledger)));close(saved['accuracy_decisive'],hits/len(loss));close(saved['logloss_decisive'],sum(loss)/len(loss));close(saved['brier_decisive'],sum(brier)/len(brier));data['net'][name]=str(net)
            for group,baseline,names in [('versus_M0','M0',('M1','M2','old_fixed','team','market')),('versus_market','market',('M0','M1','M2','old_fixed'))]:
                for name in names:
                    buckets=collections.defaultdict(lambda:np.zeros(4));changed=0
                    for x in ledger:
                        a=x['forecasts'][name];b=x['forecasts'][baseline];g=buckets[date.fromisoformat(x['date']).isocalendar()[:2]]
                        if not a['void']:g[0]+=1;g[1]+=int(a['hit'])-int(b['hit'])
                        g[2]+=1;g[3]+=float(Decimal(a['net'])-Decimal(b['net']));changed+=a['pick']!=b['pick']
                    saved=m[group][name];assert (saved['weeks'],saved['changed'])==(len(buckets),changed)
                    for ci,k in zip(intervals(buckets,[(1,0),(3,2)]),('accuracy_delta_ci95','roi_delta_ci95')):
                        for a,b in zip(ci,saved[k]):close(a,b)
            summary[scenario]['prices'][key]=data
    print(json.dumps({'status':'PASS','freeze_commit':COMMIT,'result_commit':RESULT,'summary':summary,'draw_diagnostic':diag_summary},ensure_ascii=False,sort_keys=True))
if __name__=='__main__':main()
