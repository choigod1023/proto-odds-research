"""Score frozen forecasts; no training or parameter selection is performed."""
import argparse,gzip,hashlib,json,subprocess,sys
from pathlib import Path
import train
sys.path.insert(0,str(train.OLD))
import metrics,outcome_metrics

def read_gz(path):return json.loads(gzip.decompress(path.read_bytes()))
def lines(path):return [json.loads(x) for x in gzip.decompress(path.read_bytes()).splitlines()]
def artifact(year):
    root=train.P/f'artifacts/{year}';m=json.loads((root/'manifest.json').read_bytes())
    for name,key in (('models.json','models_sha256'),('predictions.json.gz','predictions_sha256')):
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=m[key]:raise ValueError('frozen artifact changed')
    if m['contract_sha256']!=hashlib.sha256((train.P/'contract.json').read_bytes()).hexdigest():raise ValueError('contract changed')
    return read_gz(root/'predictions.json.gz')

def diagnose(predictions,labels):
    out={}
    for r in predictions:
        fid=r['fixture_id'];y=labels[fid];h,a=y['home_score'],y['away_score'];truth=metrics.winner(y);q=r['probabilities_2way'];side=metrics.pick({'q2':q});confidence=max(q.values())
        c='p50_60' if confidence<.6 else 'p60_70' if confidence<.7 else 'p70_100'
        margin='draw' if h==a else 'actual_margin1' if abs(h-a)==1 else 'actual_margin2' if abs(h-a)==2 else 'actual_margin3plus'
        for key in (c,margin):
            z=out.setdefault(key,{'n':0,'decisive':0,'correct':0,'wrong':0,'draws':0});z['n']+=1;z['draws']+=h==a;z['decisive']+=h!=a;z['correct']+=side==truth;z['wrong']+=h!=a and side!=truth
    return out

def score(preds,labels,prices,candidate_key="winner_head"):
    base=train.core.unique(preds['baseline']);candidate=train.core.unique(preds[candidate_key])
    if set(base)!=set(candidate) or set(labels)!=set(base):raise ValueError('paired populations differ')
    out={'n':len(labels),'price_n':len(prices),'models':{}};records={}
    for name,rows in preds.items():
        pm={r['fixture_id']:{'q2':r['probabilities_2way']} for r in rows};settlement,records[name]=metrics.settle(prices,pm,labels)
        out['models'][name]={'outcome':train.core.metrics(rows,labels),'price':settlement,'error_diagnostics':diagnose(rows,labels)}
    out['paired_price']=metrics.paired(records[candidate_key],records['baseline'])
    out['paired_outcome']=outcome_metrics.outcome_pair(preds[candidate_key],preds['baseline'],labels)
    out['changed_conditional_picks']=sum(metrics.pick({'q2':candidate[f]['probabilities_2way']})!=metrics.pick({'q2':base[f]['probabilities_2way']}) for f in base)
    out['draw_probability_max_change']=max(abs(base[f]['probabilities_3way']['draw']-candidate[f]['probabilities_3way']['draw']) for f in base)
    return out

def evaluation_inputs(forecasts):
    labels={y:train.core.unique(lines(train.OLD/f'data/labels-{y}.jsonl.gz')) for y in (2024,2025)}
    prices={y:lines(train.OLD/f'data/prices-{y}.jsonl.gz') for y in (2024,2025)}
    old='d08ccb708033fb6b067e84e80eae81ea3c9b4cfa';prefix='experiments/kbo-player-effects-20261103/inputs/'
    raw_labels=subprocess.check_output(['git','show',old+':'+prefix+'test-labels.jsonl.gz'])
    raw_prices=subprocess.check_output(['git','show',old+':'+prefix+'price-ledgers.json'])
    all_labels=train.core.unique([json.loads(x) for x in gzip.decompress(raw_labels).splitlines()]);ids=set(train.core.unique(forecasts[2026]['baseline']));labels[2026]={f:all_labels[f] for f in ids}
    tiers=json.loads(raw_prices)['ledgers'];prices[2026]=[r for r in tiers['extended_day_team_source_confirmed'] if r['fixture_id'] in ids]
    for rows in prices.values():rows.sort(key=lambda r:(r['date'],r['fixture_id']))
    strict=[r for r in tiers['strict_original_time_match'] if r['fixture_id'] in ids];strict.sort(key=lambda r:(r['date'],r['fixture_id']))
    source={'commit':old,'label_sha256':hashlib.sha256(raw_labels).hexdigest(),'price_sha256':hashlib.sha256(raw_prices).hexdigest()}
    return labels,prices,strict,source

def main(verify_existing=False):
    train.core.verify()
    forecasts={y:artifact(y) for y in (2024,2025,2026)}
    labels,prices,strict,source=evaluation_inputs(forecasts)
    result={'annual':{},'source2026':source,'retrospective_all_periods_previously_seen':True,'new_data_collected':False,'runtime_or_automations_changed':False,'candidate_count':1,'no_reselection_on2025_or2026':True}
    reference=json.loads((train.OLD/'results.json').read_bytes())
    for year in (2024,2025,2026):
        prices[year].sort(key=lambda r:(r['date'],r['fixture_id']));d=score(forecasts[year],labels[year],prices[year]);result['annual'][str(year)]=d
        previous=reference['annual'][str(year)];b=d['models']['baseline']
        if b['outcome']['hits']!=previous['outcome']['baseline']['hits'] or b['price']['net_units']!=previous['price']['baseline']['net_units']:raise ValueError('baseline does not reproduce reference')
    d=result['annual']['2024']['models'];result['development_gate_passed']=d['winner_head']['outcome']['decisive_hits']>d['baseline']['outcome']['decisive_hits'] and d['winner_head']['outcome']['decisive_logloss']<=d['baseline']['outcome']['decisive_logloss']
    result['strict2026']=score(forecasts[2026],labels[2026],strict)
    result['forecast_freeze_commit']=subprocess.check_output(['git','rev-parse','44bc765e'],text=True).strip()
    if verify_existing:
        if result!=json.loads((train.P/'results.json').read_bytes()):raise ValueError('saved result does not reproduce')
        print('saved result reproduced exactly')
    else:train.write(train.P/'results.json',result)
    for y,d in result['annual'].items():
        print(y,'n',d['n'],'prices',d['price_n'],'pick_changes',d['changed_conditional_picks'])
        for name,z in d['models'].items():
            o=z['outcome'];p=z['price'];print(name,'3way',o['hits'],'decisive',o['decisive_hits'],o['decisive'],'acc',round(o['decisive_accuracy']*100,3),'ROI',round(p['roi']*100,3),'DD',p['max_daily_close_drawdown_units'])
        print('ROI_diff_CI',d['paired_price']['roi_delta_ci95'])
    print('development_gate_passed',result['development_gate_passed'])

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--verify-existing',action='store_true');main(ap.parse_args().verify_existing)
