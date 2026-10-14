import importlib.util
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location('simple_calibration',Path(__file__).resolve().parents[1]/'scripts/evaluate_simple_calibration.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def row(i=0):
    return {'id':str(i),'recommended':True,'probability':.6,'odds':1.5,
            'kickoff_at':'2026-09-20T12:00:00Z','recorded_at':'2026-09-20T11:00:00Z',
            'published_at':'2026-09-20T11:00:00Z','result':'hit' if i%2 else 'miss',
            'result_source':'official','settled_at':'2026-09-20T14:00:00Z'}


def test_sigmoid_fit_reduces_in_sample_error_without_reversing_order():
    rows = [row(i) for i in range(100)]
    model = m.fit(rows)
    assert model['a'] >= 0
    p = m.predict(rows,model)
    assert m.metrics(rows,p)['brier'] < m.metrics(rows,[.6]*100)['brier']
    assert m.fit(rows[:4])['status'] == 'identity_insufficient_training'


def test_old_snapshot_and_later_cohort_separation():
    train = {'recommendation_history':{'a':row()}}
    new = row(1)
    for k,v in list(new.items()):
        if k.endswith('_at'):
            new[k]=v.replace('09-20','09-26')
    later = {'recommendation_history':{'old':row(),'new':new}}
    result=m.compare(train,{},later,{},m.stamp('2026-09-25T00:00:00Z'),m.stamp('2026-09-27T00:00:00Z'))
    assert result['training_n']==1 and result['later_n']==1
    assert result['provenance']['history']=={'unknown':2}
    new['result']='miss'
    changed=m.compare(train,{},later,{},m.stamp('2026-09-25T00:00:00Z'),m.stamp('2026-09-27T00:00:00Z'))
    assert changed['model']==result['model']


@pytest.mark.parametrize('field,value',[('odds',float('nan')),('probability',float('inf')),('recorded_at','2026-09-20T11:30:00Z'),('recorded_at','2026-09-20T11:00:00')])
def test_invalid_rows_excluded(field,value):
    r=row();r[field]=value
    assert m.load_rows({'recommendation_history':{'a':r}},{},m.stamp('2026-09-27T00:00:00Z'))==[]


def test_no_selections_is_not_zero_roi():
    assert m.roi([row()],[False])['roi'] is None
    assert m.metrics([],np.array([]))['brier'] is None


def test_future_settlement_does_not_enter_training():
    r=row();r['settled_at']='2026-09-28T00:00:00Z'
    assert not m.load_rows({'recommendation_history':{'a':r}},{},m.stamp('2026-09-27T00:00:00Z'))


def test_future_snapshot_rejected():
    with pytest.raises(ValueError):
        m.load_rows({'recommendation_history':{}},{'generated_at':'2026-09-28T00:00:00Z'},m.stamp('2026-09-27T00:00:00Z'))
