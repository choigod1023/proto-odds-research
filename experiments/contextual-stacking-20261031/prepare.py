"""Extract exact stored, actual-vote forecasts; never use a price proxy cohort."""
from datetime import timedelta
import json
import math
import numpy as np
from common import HERE,ROOT,at,sha,save,read_gz,write_gz


def vector(value,n):
    p=np.asarray(value,dtype=float)
    if p.shape!=(n,) or not np.isfinite(p).all() or (p<0).any() or abs(p.sum()-1)>1e-9:
        raise ValueError('invalid probability/score vector')
    return p.tolist()


def prepare():
    protocol=json.loads((HERE/'protocol.json').read_text(encoding='utf8'))
    directory=ROOT/'experiments/dynamic-score-state-model-20261030'
    pred_path=directory/'results/predictions.jsonl.gz'
    target_path=directory/'inputs/targets.jsonl.gz'
    if sha(pred_path)!=protocol['source_predictions_sha256'] or sha(target_path)!=protocol['source_targets_sha256']:
        raise ValueError('source hash mismatch')
    sources={}
    for row in read_gz(pred_path):
        if row['mode']=='frozen' and row['actual_votes'] and row['model'] in ('score-static','outcome-static'):
            key=(row['league'],row['event_id'],row['model'])
            if key in sources: raise ValueError('duplicate base forecast')
            sources[key]=row
    features=[];labels=[]
    for row in read_gz(target_path):
        if not row['actual_votes'] or at(row['decision_at']).year!=2026: continue
        key=(row['league'],row['event_id']);n=len(row['sides'])
        if at(row['decision_at'])<at('2026-01-05T00:00:00Z'): raise ValueError('forecast predates base freeze')
        if n not in (2,3) or row['sides'] not in (['home','away'],['home','draw','away']):raise ValueError('invalid product')
        market=vector(row['market_p'],n);votes=vector(row['vote_p'],n)
        if len(row['odds'])!=n or any(type(x) not in (int,float) or not math.isfinite(x) or x<=1 for x in row['odds']):raise ValueError('incomplete odds')
        base={}
        for model,name in (('score-static','score_static'),('outcome-static','outcome_static')):
            source=sources[key+(model,)]
            for field in ('league','event_id','decision_at','kickoff','odds','outcome','sides'):
                if source[field]!=row[field]:raise ValueError('base/target mismatch: '+field)
            if not np.allclose(source['market']['p'],market,atol=1e-12,rtol=0):raise ValueError('market mismatch')
            base[name]=vector(source['candidate']['p'],n)
        identifier=row['league']+'|'+row['event_id']
        features.append({'id':identifier,'event_id':row['event_id'],'league':row['league'],
            'decision_at':row['decision_at'],'kickoff_at':row['kickoff'],'day':at(row['decision_at']).date().isoformat(),
            'sides':row['sides'],'odds':row['odds'],'raw_vote_score':votes,
            'components':{'market':market,'vote_score':[(.98*v+.02/n) for v in votes],**base},
            'base_training_cutoff':'2026-01-05T00:00:00Z',
            'external_receipt_verified':False,'price_certified':False})
        labels.append({'id':identifier,'outcome':row['outcome'],
                       'available_at_proxy':(at(row['kickoff'])+timedelta(days=4)).isoformat()})
    features.sort(key=lambda r:(at(r['decision_at']),r['id']))
    if len(features)!=445 or len({r['id'] for r in features})!=445:raise ValueError('unexpected actual-vote cohort')
    labels_by_id={r['id']:r for r in labels}
    write_gz(HERE/'inputs/features.jsonl.gz',features)
    write_gz(HERE/'inputs/labels.jsonl.gz',[labels_by_id[r['id']] for r in features])
    save(HERE/'inputs/provenance.json',{'actual_events':445,'fresh_independent_events':0,
        'retrospective_reused':True,'sources':{str(pred_path.relative_to(ROOT)):sha(pred_path),str(target_path.relative_to(ROOT)):sha(target_path)},
        'no_certified_receipt_or_execution_price':True,'features_sha256':sha(HERE/'inputs/features.jsonl.gz'),
        'labels_sha256':sha(HERE/'inputs/labels.jsonl.gz')})
    print('Prepared 445 reused actual-vote events; labels stored separately')


if __name__=='__main__': prepare()
