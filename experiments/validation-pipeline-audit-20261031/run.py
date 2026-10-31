"""Independent full-state replay, numerical controls and source alias warnings."""
from collections import defaultdict
import gzip,hashlib,importlib.util,json,unicodedata,subprocess
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve().parent
STUDY=HERE.parent/'all-sports-underdog-20261031'


def module(path):
    spec=importlib.util.spec_from_file_location(path.stem,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def read(path):
    with gzip.open(path,'rt',encoding='utf8') as f:return [json.loads(x) for x in f]


def aliases(rows):
    groups=defaultdict(lambda:defaultdict(list))
    for r in rows:
        for side in ('home','away'):
            name=r[side];normal=unicodedata.normalize('NFKD',name).casefold()
            normal=''.join(v for v in normal if v.isalnum())
            groups[(r['sport'],r['league'],normal)][name].append(r['day'][:4])
    return [{'sport':s,'league':l,'normal':n,'variants':{name:{'appearances':len(years),'years':[min(years),max(years)]} for name,years in sorted(variants.items())},
             'same_real_team_independently_confirmed':False,'action':'warning only; no unverified alias merge or forecast retuning'}
            for (s,l,n),variants in sorted(groups.items()) if len(variants)>1]


def controls(a,c):
    results=[]
    for k in (2,3):
        rng=np.random.default_rng(19048);x=rng.normal(size=(1200,4));beta=rng.normal(size=(4,k));y=np.argmax(x@beta,axis=1);q=np.full((1200,k),1/k)
        good=c.predict(c.train(x[:800],y[:800],q[:800],False),x[800:],q[800:]).argmax(1)
        bad=c.predict(c.train(x[:800],rng.permutation(y[:800]),q[:800],False),x[800:],q[800:]).argmax(1)
        accuracy=float((good==y[800:]).mean());shuffled=float((bad==y[800:]).mean())
        assert accuracy>.8 and accuracy-shuffled>.2
        gradient_errors={}
        for offset in (False,True):
            r=np.random.default_rng(4242);design=np.column_stack([np.ones(30),r.normal(size=(30,4))]);labels=np.arange(30)%k;market=r.dirichlet(np.ones(k),size=30);b=r.normal(0,.1,size=(5,k))
            _,gradient=a.loss_gradient(b,design,labels,market,offset);numeric=np.zeros_like(b);eps=1e-6
            for i in range(5):
                for j in range(k):
                    hi=b.copy();lo=b.copy();hi[i,j]+=eps;lo[i,j]-=eps
                    numeric[i,j]=(a.loss_gradient(hi,design,labels,market,offset)[0]-a.loss_gradient(lo,design,labels,market,offset)[0])/(2*eps)
            error=float(np.max(np.abs(numeric-gradient)));assert error<1e-6;gradient_errors[str(offset)]=error
        results.append(dict(classes=k,synthetic_positive_accuracy=accuracy,shuffled_label_accuracy=shuffled,gradient_finite_difference_errors=gradient_errors,
                            meaning='synthetic algorithm control, not a sport/ROI result'))
    return results


def immutable_artifacts():
    baseline=json.loads(subprocess.run(['git','show','f403ef75da47b02374172581763b44f646094739:experiments/all-sports-underdog-20261031/handoff.json'],cwd=STUDY,capture_output=True,check=True).stdout)
    checked={}
    for name,expected in baseline['artifacts'].items():
        if not (name.endswith('/ledger.jsonl.gz') or name.endswith('/states.json') or name.startswith('inputs/') and name.endswith('.jsonl.gz')):continue
        p=STUDY/name;raw=p.read_bytes();value=hashlib.sha256(raw.replace(b'\r\n',b'\n') if p.suffix=='.json' else raw).hexdigest()
        assert value==expected, 'baseline forecast/state/input changed: '+name
        checked[name]=value
    return checked


def run():
    a=module(STUDY/'replay_audit.py');c=module(STUDY/'common.py')
    immutable=immutable_artifacts()
    corpora={p.stem:read(p) for p in (STUDY/'inputs').glob('*.jsonl.gz')}
    allsource=[r for rows in corpora.values() for r in rows]
    outcomes={}
    for p in sorted((STUDY/'results').glob('*/*/ledger.jsonl.gz')):
        sport=p.parent.parent.name;cohort=p.parent.name
        source=[r for r in allsource if r['sport']==sport and r['cohort']==cohort]
        result=a.check(source,read(p),json.loads((p.parent/'states.json').read_text(encoding='utf8')),refit=True)
        outcomes[cohort+'/'+sport]=result
        print(json.dumps({'completed':cohort+'/'+sport,'max_probability_difference':result['max_probability_difference']},ensure_ascii=False),flush=True)
    findings={'source_commit':'f403ef75da47b02374172581763b44f646094739','probability_state_audit':outcomes,'numerical_controls':controls(a,c),
              'unresolved_team_alias_candidates':aliases(allsource),
              'saved_predictions_and_states_unchanged':True,
              'source_certification':'Replay validates algebra under source-date assumptions; it does not certify pregame receipts/prices or identical real-world team aliases.',
              'original_model_scope':'PR475 outcome-only models, not operating tipster policy or score-static.'}
    findings['unchanged_artifact_hashes']=immutable_artifacts()
    assert findings['unchanged_artifact_hashes']==immutable
    (HERE/'results.json').write_text(json.dumps(findings,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    return findings


if __name__=='__main__':run()
