"""Independently audit frozen corrected M2 results against unchanged v1."""
import collections,hashlib,json,math,subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path
import numpy as np
import audit_math as ref
import audit_results as original
from audit_frozen import WORKER,ROOT,git,index,dec

V1='2156ec6b2c7c54a104c051c5cddc5a9d0b9d46f7';PRED='18c2e84d6962f9016b20996e543e5b4bb6c70975';RESULT='d08ccb708033fb6b067e84e80eae81ea3c9b4cfa'
def read(commit,path,sha=None):
    b=git(WORKER,commit,ROOT/path)
    if sha:assert hashlib.sha256(b).hexdigest()==sha,path
    return json.loads(b)
def close(a,b):assert abs(a-b)<1e-11,(a,b)
def paired_three(ids,a,b,labels):
    buckets=collections.defaultdict(lambda:np.zeros(4));changed3=changed2=0;diff=[]
    for fid in sorted(ids,key=lambda x:(a[x]['event_date'],x)):
        x=a[fid];y=b[fid];truth=original.winner(labels[fid]);p=x['probabilities_3way'];q=y['probabilities_3way'];u=x['probabilities_2way'];v=y['probabilities_2way']
        changed3+=original.choose(p)!=original.choose(q);changed2+=max(('home','away'),key=u.get)!=max(('home','away'),key=v.get);diff.extend(abs(p[k]-q[k]) for k in ('home','draw','away'))
        week=date.fromisoformat(x['event_date']).isocalendar()[:2];buckets[week]+=np.r_[1.,np.asarray(original.score(p,truth))-np.asarray(original.score(q,truth))]
    return buckets,changed3,changed2,sum(diff)/len(diff)
def main():
    assert subprocess.run(['git','merge-base','--is-ancestor',PRED,RESULT],cwd=WORKER).returncode==0
    for path in ('artifacts/selection.json','artifacts/final-models.json','artifacts/predictions.json','artifacts/results.json','artifacts/draw-diagnostic.json'):
        assert git(WORKER,V1,ROOT/path)==git(WORKER,RESULT,ROOT/path)
    old=read(V1,'artifacts/results.json','211b89d365136a166fd13026df5f6358e4b7ecda5db50b4459a2f42e75f19cd8');oldpred=read(V1,'artifacts/predictions.json','ba1ed43123a58535f2ee997de4181931449008b0e9bbbba4ba317245c7525fa9')
    pred=read(PRED,'v2/predictions.json','6d3dd2cc15d4a4681335e1984c76e8526bf94aae9f46fe372d5b4c10b1abced5');out=read(RESULT,'v2/results.json','1b120334063c0ff535d3ff74c3ab0f55dc01a3c4bb878204b745b05f2c0d1b55')
    assert out['v2_prediction_commit']==PRED and out['not_blind_prior_2026_seen'] is True and out['unchanged_M0_M1_verified'] is True and out['selected_family']==pred['selected_family']=='M2'
    labels=index(dec(git(WORKER,RESULT,ROOT/'inputs/test-labels.jsonl.gz')));assert len(labels)==726
    summary={}
    for scenario,rows in pred['scenarios'].items():
        fresh=index(rows);v1=index(oldpred['scenarios'][scenario]['M2']);ids=set(fresh);assert len(ids)==709 and set(v1)==ids
        r=out['scenarios'][scenario];assert r['threeway']['n']==709
        for name in ('M0','M1','old_fixed'):
            assert r['threeway']['models'][name]==old['scenarios'][scenario]['threeway']['models'][name]
        total=np.zeros(3)
        for fid in sorted(ids,key=lambda x:(fresh[x]['event_date'],x)):total+=np.asarray(original.score(fresh[fid]['probabilities_3way'],original.winner(labels[fid])))
        m=r['threeway']['models']['M2'];assert m['hits']==int(total[0]);
        for i,key in enumerate(('accuracy','logloss','brier')):close(m[key],total[i]/709)
        m0=index(oldpred['scenarios'][scenario]['M0']);base_buckets,_,_,_=paired_three(ids,fresh,m0,labels);m0ci=r['threeway']['versus_M0']['M2'];assert m0ci['weeks']==len(base_buckets)
        for ci,key in zip(original.intervals(base_buckets,[(1,0),(2,0),(3,0)]),('accuracy_delta_ci95','logloss_delta_ci95','brier_delta_ci95')):
            for a,b in zip(ci,m0ci[key]):close(a,b)
        buckets,c3,c2,diff=paired_three(ids,fresh,v1,labels);paired=r['M2_v2_vs_v1'];assert (paired['weeks'],paired['changed_3way_picks'],paired['changed_2way_picks'])==(len(buckets),c3,c2);close(paired['mean_absolute_probability_change_3way_per_class'],diff)
        for ci,key in zip(original.intervals(buckets,[(1,0),(2,0),(3,0)]),('accuracy_delta_ci95','logloss_delta_ci95','brier_delta_ci95')):
            for a,b in zip(ci,paired[key]):close(a,b)
        diagnostic={}
        for name,x in [('M2_v1',v1),('M2_v2',fresh)]:diagnostic[name]=original.check_diagnostic(list(x.values()),labels,r['draw_diagnostic'][name])
        summary[scenario]={'3way':{'M2_v1_hits':old['scenarios'][scenario]['threeway']['models']['M2']['hits'],'M2_v2_hits':m['hits'],'M2_v2_logloss':m['logloss'],'changed_3way':c3},'draw':diagnostic,'prices':{}}
        for key,ledger in out['price_ledgers'][scenario].items():
            before=old['price_ledgers'][scenario][key];prior={x['fixture_id']:x for x in before};assert len(ledger)==len(before) and {x['fixture_id'] for x in ledger}==set(prior)
            metrics=r['prices'][key];assert metrics['n']==len(ledger) and metrics['refunds']==sum(x['winner']=='draw' for x in ledger)
            assert metrics['M2_v1']==old['scenarios'][scenario]['prices'][key]['models']['M2']
            for n in ('M0','M1','old_fixed','team','market'):assert metrics['models'][n]==old['scenarios'][scenario]['prices'][key]['models'][n]
            data={'n':len(ledger),'refunds':metrics['refunds'],'net':{}}
            for name in ('M0','M1','M2','M2_v1','old_fixed','team','market'):
                total=Decimal(0);monthly=collections.defaultdict(Decimal);daily=collections.defaultdict(Decimal);hits=voids=0;ll=[];br=[]
                for x in ledger:
                    fid=x['fixture_id'];p=x['forecasts'][name];y=original.winner(labels[fid]);oldrow=prior[fid]
                    assert (x['date'],x['winner'],x['price_source'],x['current_buy_reject'])==(oldrow['date'],oldrow['winner'],oldrow['price_source'],oldrow['current_buy_reject']) and x['winner']==y
                    if name=='M2':
                        q=fresh[fid]['probabilities_2way'];close(p['probabilities'][0],q['home']);close(p['probabilities'][1],q['away'])
                    else:assert p==oldrow['forecasts']['M2' if name=='M2_v1' else name]
                    pick,net=ref.settlement(p['probabilities'],[x['price_source']['prices']['home'],x['price_source']['prices']['away']],y)
                    assert p['pick']==pick and p['hit']==(pick==y) and p['void']==(y=='draw') and Decimal(p['net'])==net
                    total+=net;monthly[x['date'][:7]]+=net;daily[x['date']]+=net;hits+=p['hit'];voids+=p['void']
                    if y!='draw':
                        j=0 if y=='home' else 1;ll.append(-math.log(max(p['probabilities'][j],1e-15)));br.append(sum((v-int(i==j))**2 for i,v in enumerate(p['probabilities'])))
                equity=peak=dd=Decimal(0)
                for day in sorted(daily):equity+=daily[day];peak=max(peak,equity);dd=max(dd,peak-equity)
                z=metrics['M2_v1'] if name=='M2_v1' else metrics['models'][name]
                assert (z['bets'],z['hits'],z['voids'],z['decisive'])==(len(ledger),hits,voids,len(ledger)-voids) and Decimal(z['net_units'])==total and Decimal(z['max_daily_close_drawdown_units'])==dd and {k:Decimal(v) for k,v in z['monthly_units'].items()}==monthly
                close(z['roi_including_refunds'],float(total/len(ledger)));close(z['logloss_decisive'],sum(ll)/len(ll));close(z['brier_decisive'],sum(br)/len(br));data['net'][name]=str(total)
            comparisons=[('M2_v2_vs_v1','M2','M2_v1')]
            for group,baseline,names in [('versus_M0','M0',('M1','M2','old_fixed','team','market')),('versus_market','market',('M0','M1','M2','old_fixed'))]:
                comparisons += [(group+':'+n,n,baseline) for n in names]
            for group,name,base in comparisons:
                buckets=collections.defaultdict(lambda:np.zeros(4));changed=0
                for x in ledger:
                    a=x['forecasts'][name];b=x['forecasts'][base];g=buckets[date.fromisoformat(x['date']).isocalendar()[:2]]
                    if not a['void']:g[0]+=1;g[1]+=int(a['hit'])-int(b['hit'])
                    g[2]+=1;g[3]+=float(Decimal(a['net'])-Decimal(b['net']));changed+=a['pick']!=b['pick']
                if ':' in group:major,minor=group.split(':');z=metrics[major][minor]
                else:z=metrics[group]
                assert (z['weeks'],z['changed'])==(len(buckets),changed)
                for ci,k in zip(original.intervals(buckets,[(1,0),(3,2)]),('accuracy_delta_ci95','roi_delta_ci95')):
                    for a,b in zip(ci,z[k]):close(a,b)
            summary[scenario]['prices'][key]=data
    print(json.dumps({'status':'PASS','v2_freeze':PRED,'v2_result':RESULT,'v1_preserved':True,'summary':summary},ensure_ascii=False,sort_keys=True))
if __name__=='__main__':main()
