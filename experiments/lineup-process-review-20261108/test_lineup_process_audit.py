import importlib.util
from pathlib import Path

import pytest

spec=importlib.util.spec_from_file_location('lineup_process_audit',Path(__file__).with_name('audit_kbo.py'))
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)
ref_spec=importlib.util.spec_from_file_location('lineup_reference_audit',Path(__file__).with_name('audit_references.py'))
reference=importlib.util.module_from_spec(ref_spec);ref_spec.loader.exec_module(reference)


def record(home=2,away=1,winner='home'):
    return {'home_runs':home,'away_runs':away,'pair_p':.6,
            'price':{'id':'proto:2026:1:1:full_game_2way','home':1.72,'away':2.0,'winner':winner}}


def test_draw_refund_remains_in_roi_denominator():
    x=audit.calculate([record(),record(1,1,'draw')],'pair')
    assert x['n']==2 and x['nonvoid']==1 and x['accuracy']==1
    assert x['roi']==.36


def test_rejects_result_direction_conflict():
    with pytest.raises(AssertionError):audit.calculate([record(winner='away')],'pair')


def test_soccer_three_way_draw_is_paid_instead_of_refunded():
    rows=[{'id':1,'p':[.1,.8,.1],'y':1,'odds':[2.5,3.3,3.0]}]
    result=reference.evaluate(rows,lambda r:r['p'],lambda r:r['y'],lambda r:r['odds'])
    assert result['hits']==1 and result['roi']==2.3


def test_reference_audit_rejects_duplicate_fixture():
    r={'id':1,'p':[.5,.5],'y':0,'odds':[1.8,2.0]}
    with pytest.raises(AssertionError):reference.evaluate([r,r],lambda x:x['p'],lambda x:x['y'],lambda x:x['odds'])
