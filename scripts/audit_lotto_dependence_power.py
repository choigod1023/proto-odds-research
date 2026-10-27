"""Verify the fixed grid, probability constraints, intervals, and four replays."""
import hashlib
import json
import math
import subprocess
import shutil
import time
import numpy as np
import lotto_dependence_power as study
from lotto_dependence_oracle_diagnostic import target_p

def main():
    study.old.resources.constrain();started=time.monotonic()
    folder=study.OUT
    read=lambda name:json.loads((folder/name).read_text(encoding='utf-8'))
    protocol=read('protocol.json');records=read('records.json');result=read('results.json')
    assert result['status']=='completed' and len(records)==510
    assert read('progress.json')['completed']==510
    for name,sha in protocol['sha256'].items():
        assert hashlib.sha256((study.ROOT/name.replace('\\','/')).read_bytes()).hexdigest()==sha
    prior=read('previous-protocol.json')
    normalized_hashes={k.replace('\\','/'):v for k,v in protocol['sha256'].items()}
    assert prior['source_sha256']==normalized_hashes['scripts/lotto_combination_dependence.py']
    for index,((kind,strength,rep),r) in enumerate(zip(study.jobs(),records)):
        assert r['index']==index and (r['kind'],r['strength'],r['replicate'])==(kind,strength,rep)
        assert r['generation_seed']==study.BASE_SEED+10000*index
        assert r['global_detected']==any(p<=.05 for p in r['global_p'].values())
        assert r['local_gate']==(r['selected_count']>0)
        assert r['local_gate']==(r['global_p']['max_standardized_pair_or_triple']<=.05)
    assert study.summarize(records)==result['cells']
    oracle=read('oracle-records.json');oracle_result=read('oracle-results.json');oracle_protocol=read('oracle-protocol.json')
    assert len(oracle)==60 and oracle_result['status']=='completed'
    assert oracle_protocol['source_sha256']==hashlib.sha256((study.ROOT/'scripts/lotto_dependence_oracle_diagnostic.py').read_bytes()).hexdigest()
    oracle_by_index={r['index']:r for r in oracle}
    assert set(oracle_by_index)==set(range(120,150))|set(range(270,300))
    for c in oracle_result['cells']:
        rows=[r for r in oracle if r['kind']==c['kind']]
        hits=sum(r['known_target_p']<=.05 for r in rows)
        assert hits==c['known_target_count'] and study.exact_interval(hits,30)==c['ci95_pointwise']
        assert sum(records[r['index']]['target_identified'] for r in rows)==c['original_target_count']
    # Check all subset probabilities of size 1 or 2 under the category models,
    # including cross-target/outside and outside/outside pairs.
    for kind in ('pair','pure_triple'):
        k=2 if kind=='pair' else 3
        for strength in study.LEVELS[kind]:
            base,q=study.category_probabilities(kind,strength)
            assert np.isclose(sum(j*q[j]/k for j in range(k+1)),6/45)
            assert np.isclose(sum((6-j)*q[j]/(45-k) for j in range(k+1)),6/45)
            if kind=='pure_triple':
                expected=math.comb(6,2)/math.comb(45,2)
                within=sum(math.comb(j,2)*q[j]/math.comb(k,2) for j in range(k+1))
                outside=sum(math.comb(6-j,2)*q[j]/math.comb(45-k,2) for j in range(k+1))
                cross=sum(j*(6-j)*q[j]/(k*(45-k)) for j in range(k+1))
                assert np.allclose([within,outside,cross],expected)
    native=study.Native();replays=[]
    def pause(tick):time.sleep(max(.001,.5*(time.monotonic()-tick)))
    for index in [0,120,180,270,330,480]:
        r=records[index];rows,target=study.generate(r['kind'],r['strength'],r['generation_seed'])
        matrix=native.sample(rows,r['sampling_seed'],pause)
        assert hashlib.sha256(matrix.tobytes()).hexdigest()==r['counts_sha256']
        analysis,selected=study.old.analyze(matrix)
        assert bool(selected)==r['local_gate']
        assert {k:v['adjusted_p'] for k,v in analysis['global_tests'].items()}==r['global_p']
        if index in oracle_by_index:
            j=study.old.FEATURES.index(target)
            assert target_p(matrix[:,j])==oracle_by_index[index]['known_target_p']
        if index==0:assert np.array_equal(matrix,np.load(folder/'first_null_counts.npz')['counts'])
        replays.append(index);del matrix
    paths=[folder/name for name in ('protocol.json','records.json','results.json','first_null_counts.npz','previous-results.json','previous-protocol.json','oracle-protocol.json','oracle-records.json','oracle-results.json')]
    compiler=shutil.which('g++') or shutil.which('clang++')
    version=subprocess.run([compiler,'--version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]
    evidence={'status':'passed','records':510,'replayed_indices':replays,'unchanged_prior_test_source':True,
              'singles_preserved_all_alternatives':True,'pure_triple_preserves_all_pairs':True,
              'compiler':version,'numpy_version':np.__version__,'native_binary_sha256':hashlib.sha256(native.path.read_bytes()).hexdigest(),
              'audit_wall_seconds':time.monotonic()-started,
              'sha256':{str(p.relative_to(study.ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
    study.save('verification.json',evidence)
    print(json.dumps({k:v for k,v in evidence.items() if k!='sha256'}))

if __name__=='__main__':main()
