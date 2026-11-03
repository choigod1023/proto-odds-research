"""Replay frozen selection and predictions; never load 2026 labels or prices."""
import copy
import gzip
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'common'))
import candidate
import core


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def main():
    dev_path=HERE/'artifacts'/'development.json.gz'
    final_path=HERE/'artifacts'/'final-predictions.json.gz'
    dev=read(dev_path);result=read(final_path);data=core.load()
    assert result['development_sha256']==hashlib.sha256(dev_path.read_bytes()).hexdigest()
    assert result['candidate_code_sha256']==hashlib.sha256((HERE/'candidate.py').read_bytes()).hexdigest()
    assert dev['test_labels_or_prices_accessed'] is False and result['test_labels_or_prices_accessed'] is False
    train,val=core.development(data)
    assert len(train)==1451 and len(val)==731 and len(core.final_training(data))==2182
    assert core.choose(dev['predictions'],data['labels'])==dev['selection']
    assert result['selected']==dev['selection']['selected']
    assert result['selected']=='negative_binomial_alpha03'
    final_train=core.final_training(data)
    refit=candidate.fit(final_train,data['labels'],result['selected_model']['config_id'])
    np.testing.assert_allclose(refit['beta'],result['selected_model']['beta'],atol=1e-12,rtol=0)
    hurdle=result['draw_hurdle_diagnostic_model']
    assert abs(hurdle['smoothed_draw_target']-hurdle['training_draw_mean_after_calibration'])<1e-12
    max_tails={}
    for scenario in ('test_actual','test_projected'):
        rows=data[scenario];selected=result['selected_predictions'][scenario]
        diag=result['draw_hurdle_diagnostic_predictions'][scenario]
        assert len(rows)==len(selected)==len(diag)==709
        assert [r['fixture_id'] for r in rows]==[r['fixture_id'] for r in selected]==[r['fixture_id'] for r in diag]
        assert candidate.predict(result['selected_model'],rows)==selected
        assert candidate.predict(hurdle,rows)==diag
        baseline=core.predict_baseline(hurdle['baseline'],rows)
        assert all(a['probabilities_2way']==b['probabilities_2way'] for a,b in zip(diag,baseline))
        for item in selected+diag:
            p=item['probabilities_3way'];q=item['probabilities_2way']
            assert all(0<=x<=1 for x in list(p.values())+list(q.values()))
            assert abs(sum(p.values())-1)<1e-12 and abs(sum(q.values())-1)<1e-12
        max_tails[scenario]=max(x['tail_bound'] for x in selected)
        plain=copy.deepcopy(rows[:3]);poisoned=copy.deepcopy(plain)
        for r in poisoned:r['raw_features']['poison_bf']={'value':{'source_bf':999999}}
        assert candidate.predict(result['selected_model'],poisoned)==candidate.predict(result['selected_model'],plain)
        if scenario=='test_actual':
            reference=core.baseline_reference()['final_predictions']
            error=max(abs(x['probabilities_3way'][k]-y['probabilities_3way'][k]) for x,y in zip(reference,baseline) for k in ('home','draw','away'))
            assert error<1e-9
    print(json.dumps({'selected':result['selected'],'train':len(final_train),'predictions_per_scenario':709,
                      'max_tail_bound':max_tails,'baseline_final_replay_max_error':error,
                      'hurdle_preserves_all_conditional_probabilities':True,'source_bf_poison_no_effect':True,
                      'final_sha256':hashlib.sha256(final_path.read_bytes()).hexdigest()},ensure_ascii=False))


if __name__=='__main__':main()
