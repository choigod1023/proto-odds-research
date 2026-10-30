"""Actual-input holdout outcome perturbation; no production or network access."""
from pathlib import Path
import copy,hashlib,json,sys

repo=Path.cwd()
sys.path.insert(0,str(repo/'scripts'))
import two_leg_literature_research as m

def check(directory,output):
    frames=m.read(directory/'normalized-frames.jsonl.gz')
    fits=json.loads((directory/'fits.json').read_text(encoding='utf-8'))
    roster=m.read(directory/'forecast-rosters.jsonl.gz')
    index={r['product_id']:r for r in roster}
    common={}
    partitions=[]
    for f in fits:
        common.setdefault(f['cohort'],set(f['test_days']))
        common[f['cohort']] &= set(f['test_days'])
        train={index[k]['event_id'] for k in f['train']['train_ids']}
        val={index[k]['event_id'] for k in f['train']['validation_ids']}
        test={r['event_id'] for r in roster if r['cohort']==f['cohort'] and r['day'] in f['test_days']}
        assert not(train&val or train&test or val&test)
        assert not f['strict_asof_training_certified']
        partitions.append(dict(cohort=f['cohort'],split=f['split'],train_events=len(train),validation_events=len(val),test_events=len(test)))
    mutated=copy.deepcopy(frames);changed=0
    for frame in mutated:
        for r in frame['products']:
            if r['day'] not in common[r['cohort']] or r['target'] is None:continue
            r['target']=(r['target']+1)%len(r['choices'])
            for i,c in enumerate(r['choices']):c['hit']=i==r['target']
            changed+=1
    assert changed
    output.mkdir(parents=True,exist_ok=True)
    m.run(mutated,output)
    assert fits==json.loads((output/'fits.json').read_text(encoding='utf-8'))
    before=m.read(directory/'heldout-predictions.jsonl.gz')
    after=m.read(output/'heldout-predictions.jsonl.gz')
    assert len(before)==len(after)
    assert all({k:v for k,v in a.items() if k!='target'}=={k:v for k,v in b.items() if k!='target'} for a,b in zip(before,after))
    original=m.read(directory/'ticket-sessions.jsonl.gz');modified=m.read(output/'ticket-sessions.jsonl.gz')
    assert len(original)==len(modified)
    changed_nets=0
    for a,b in zip(original,modified):
        assert {k:v for k,v in a.items()if k!='policies'}=={k:v for k,v in b.items()if k!='policies'}
        assert a['policies'].keys()==b['policies'].keys()
        for name,p in a['policies'].items():
            q=b['policies'][name]
            assert all(p[k]==q[k]for k in ('selected','stake','model','rule','quoted_odds'))
            assert [{k:v for k,v in x.items()if k!='hit'}for x in p['legs']]==[{k:v for k,v in x.items()if k!='hit'}for x in q['legs']]
            changed_nets+=p['net']!=q['net']
    assert changed_nets
    return dict(pass_all=True,partitions=partitions,heldout_product_revisions_rotated=changed,
                prediction_rows=len(before),ticket_sessions=len(original),changed_policy_nets=changed_nets,
                fits_identical=True,probabilities_identical=True,selected_legs_identical=True,
                note='Same exposed archives; verifies no direct holdout-label dependency, not authenticated historical label receipt or upstream feature availability')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--experiment-dir',default='experiments/two-leg-literature-20261030')
    args=parser.parse_args()
    experiment=repo/args.experiment_dir
    results={}
    for name,source in [('primary',experiment/'results'),('claimed',experiment/'claimed-time-results')]:
        results[name]=check(source,repo/'.local-test/OUTCOME-PERTURBATION'/name)
    results['script_sha256']=hashlib.sha256((repo/'scripts/two_leg_literature_research.py').read_bytes()).hexdigest()
    path=experiment/'audits/outcome-blindness.json'
    m.write(path,results)
    print(json.dumps(results,ensure_ascii=False))
