"""Select on earlier validation and freeze label-free later predictions."""
import hashlib,json
import numpy as np
from data import load_inputs,partition,ROOT,CUTOFF,TRAIN_CUTOFF
import model


def main():
    games,prices=load_inputs(scoring=False);train,val,embargo,test=partition(games)
    if not train or not val or not test:raise ValueError('empty split')
    if any('score' in r for r in test):raise ValueError('test labels leaked')
    feats=model.history_features(games);selected={};losses={};states={}
    for name,candidate in [('team',False),('lineup',True)]:
        results=[]
        for alpha in (1,10,100):
            state=model.fit(train,feats,alpha,candidate,TRAIN_CUTOFF)
            rates,_,_=model.predict(state,val,feats);results.append((model.score_loss(val,rates),alpha))
        loss,alpha=min(results,key=lambda x:(x[0],x[1]));losses[name]=[{'alpha':a,'joint_score_nll':v} for v,a in results]
        selected[name]=alpha;states[name]=model.fit(train+val,feats,alpha,candidate,CUTOFF)
    output=[]
    rates1,p1,u1=model.predict(states['team'],test,feats);rates2,p2,u2=model.predict(states['lineup'],test,feats)
    for i,g in enumerate(test):
        q=prices.get(g['id']);market=None
        if q:
            inv=np.reciprocal(q['odds']);market=(inv/inv.sum()).tolist()
        output.append({'fixture_id':g['id'],'day':g['date'].isoformat(),'teams':g['teams'],'team_p':p1[i].tolist(),'lineup_p':p2[i].tolist(),
          'team_rates':rates1[i].tolist(),'lineup_rates':rates2[i].tolist(),'team_unknown':u1[i],'lineup_unknown':u2[i],
          'lineup_features':feats[g['id']],'price':q,'market_p':market})
    dest=ROOT/'frozen';dest.mkdir(exist_ok=True)
    frozen_bytes=''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in output).encode('utf8')
    metadata={'protocol_sha256':hashlib.sha256((ROOT/'protocol.json').read_bytes()).hexdigest(),
      'train':len(train),'validation':len(val),'embargo':len(embargo),'test':len(test),'test_price_candidates':sum(x['price'] is not None for x in output),
      'selected_alpha':selected,'validation_joint_score_losses':losses,'cutoff_inclusive':CUTOFF.isoformat(),
      'states':{k:{'coeff':v['coeff'].tolist(),'teams':v['teams'],'alpha':v['alpha'],'candidate':v['candidate'],'optimizer_message':v['optimizer_message']} for k,v in states.items()},
      'predictions_sha256':hashlib.sha256(frozen_bytes).hexdigest(),'note':'frozen test predictions have no target outcomes; score only after this commit'}
    (dest/'predictions.jsonl').write_bytes(frozen_bytes)
    (dest/'manifest.json').write_bytes((json.dumps(metadata,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode('utf8'))
    print(json.dumps({'train':len(train),'validation':len(val),'embargo':len(embargo),'test':len(test),'price_candidates':metadata['test_price_candidates'],'alpha':selected,'sha':metadata['predictions_sha256']}))
if __name__=='__main__':main()
