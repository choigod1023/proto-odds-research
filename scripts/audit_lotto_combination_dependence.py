"""Read-only independent recomputation of stored association statistics."""
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import lotto_combination_dependence as experiment

def main():
    experiment.resources.constrain()
    out=experiment.OUT;root=experiment.ROOT
    read=lambda name:json.loads((out/name).read_text(encoding='utf-8'))
    protocol=read('protocol.json');result=read('results.json')
    assert result['status']=='verified' and read('progress.json')['state']=='verified'
    assert hashlib.sha256((root/'scripts/lotto_combination_dependence.py').read_bytes()).hexdigest()==protocol['source_sha256']
    for name,sha in protocol['data_sha256'].items():
        assert hashlib.sha256((out/name).read_bytes()).hexdigest()==sha
    matrix=np.load(out/'conditional_counts.npz')['counts']
    assert matrix.shape==(1000,math.comb(45,2)+math.comb(45,3))
    assert np.all(matrix[:,:990].sum(1)==15000)
    assert np.all(matrix[:,990:].sum(1)==20000)
    train=[json.loads(s)['numbers_sorted'] for s in (out/'base.jsonl').read_text().splitlines()[:1000]]
    raw=np.array([sum(set(v+1 for v in feature)<=set(row) for row in train) for feature in experiment.FEATURES])
    assert np.array_equal(raw,matrix[0])
    # Independent feature-wise calculation avoids the implementation's chunk path.
    maximum=np.zeros(len(matrix));actual=[]
    for column in matrix.T:
        scale=max(.5,float(np.std(column)));z=(column-float(np.mean(column)))/scale
        maximum=np.maximum(maximum,np.abs(z));actual.append(abs(z[0]))
    pmax=float((maximum>=maximum[0]).mean())
    assert pmax==result['analysis']['global_tests']['max_standardized_pair_or_triple']['raw_p']
    for key,sl in [('pair_dispersion',slice(0,990)),('triple_dispersion',slice(990,None))]:
        stat=(matrix[:,sl].astype(np.int64)**2).sum(1)
        p=float((stat>=stat[0]).mean())
        assert p==result['analysis']['global_tests'][key]['raw_p']
        assert min(1,3*p)==result['analysis']['global_tests'][key]['adjusted_p']
    selected=[i for i,z in enumerate(actual) if min(1,3*float((maximum>=z).mean()))<=.05]
    assert selected==[x['index'] for x in result['analysis']['selected']]
    if not selected:assert result['validation']['status']=='not_triggered'
    control=np.load(out/'positive_control_counts.npz')['counts']
    assert control.shape==(100,15180)
    assert result['positive_control']['global_tests']['pair_dispersion']['adjusted_p']<=.05
    assert result['sampler_diagnostics']['all_margins_preserved']
    paths=[out/name for name in ('protocol.json','results.json','conditional_counts.npz','positive_control_counts.npz','base.jsonl','extra.jsonl')]
    paths += [root/name for name in ('scripts/lotto_combination_dependence.py','scripts/lotto_coverage_resources.py')]
    evidence={'status':'passed','training_draws':1000,'sample_matrices_including_observed':1000,
              'pair_features':990,'triple_features':14190,'selected_count':len(selected),
              'hashes':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    experiment.save('verification.json',evidence)
    print(json.dumps({k:v for k,v in evidence.items() if k!='hashes'}))

if __name__=='__main__':main()
