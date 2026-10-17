"""Secondary diagnostic: what if the injected relation were known in advance?

Not a deployable lottery rule. Reuses fixed synthetic datasets and compares
the same data/conditional sampler with a single pre-specified target test.
Added as a diagnostic after seeing initial pair power; does not alter the
primary grid or the previous real-data test.
"""
import json
import time
import hashlib
import numpy as np
import lotto_dependence_power as study

def target_p(counts):
    distance=np.abs(counts-counts.mean())
    return float(np.mean(distance>=distance[0]))

def main():
    study.old.resources.constrain();started=time.monotonic();cpu=time.process_time()
    records=json.loads((study.OUT/'records.json').read_text())
    assert len(records)==510,'run only after primary experiment finishes'
    chosen=[r for r in records if (r['kind'],r['strength']) in [('pair',2),('pure_triple',3)]]
    assert len(chosen)==60
    study.save('oracle-protocol.json',{'role':'secondary exploratory diagnostic, not part of primary fixed grid',
        'selected_cases':[['pair',2],['pure_triple',3]],'replicates_each':30,'alpha':.05,
        'null':'same exact-margin conditional sampler; same999spokes,20000steps',
        'statistic':'absolute target count minus symmetric mean; target supplied by generator before observed counts',
        'limitation':'known target requires external prior information; cannot use on a target selected from actual data',
        'source_sha256':hashlib.sha256(__import__('pathlib').Path(__file__).read_bytes()).hexdigest()})
    native=study.Native();out=[]
    def pause(tick):
        assert time.monotonic()-started<300 and study.old.resources.peak_mib()<384
        time.sleep(max(.001,.5*(time.monotonic()-tick)))
    for r in chosen:
        rows,target=study.generate(r['kind'],r['strength'],r['generation_seed'])
        matrix=native.sample(rows,r['sampling_seed'],pause)
        assert hashlib.sha256(matrix.tobytes()).hexdigest()==r['counts_sha256']
        j=study.old.FEATURES.index(target);p=target_p(matrix[:,j])
        out.append({'index':r['index'],'kind':r['kind'],'strength':r['strength'],'known_target_p':p,
                    'known_target_detected':p<=.05,'original_target_identified':r['target_identified']})
        del matrix
        study.save('oracle-records.json',out)
    cells=[]
    for kind in ('pair','pure_triple'):
        selected=[r for r in out if r['kind']==kind];k=sum(r['known_target_detected'] for r in selected)
        cells.append({'kind':kind,'strength':selected[0]['strength'],'n':30,'known_target_count':k,
                      'known_target_rate':k/30,'ci95_pointwise':study.exact_interval(k,30),
                      'original_target_count':sum(r['original_target_identified'] for r in selected)})
    result={'status':'completed','cells':cells,'wall_seconds':time.monotonic()-started,
            'cpu_seconds':time.process_time()-cpu,'peak_mib':study.old.resources.peak_mib()}
    study.save('oracle-results.json',result);print(json.dumps(result))

if __name__=='__main__':main()
