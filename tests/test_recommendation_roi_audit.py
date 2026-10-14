from datetime import timedelta
import importlib.util
from pathlib import Path
import pytest
from test_recommendation_history import NOW, candidate

spec = importlib.util.spec_from_file_location('roi_audit', Path(__file__).resolve().parents[1]/'scripts/audit_recommendation_roi.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def record(i, p=.65, result='hit', **kwargs):
    return {**candidate(i), 'id':str(i), 'recommended':True, 'probability':p,
            'recorded_at':NOW.isoformat(), 'published_at':NOW.isoformat(),
            'result':result, 'result_source':'official', **kwargs}


def test_valid_requires_pre_cutoff_official_result_and_future_not_settled():
    rows = [record(1), record(2, recorded_at=(NOW+timedelta(hours=3)).isoformat()),
            record(3,result_source='unverified'), record(4,kickoff_at=(NOW+timedelta(days=3)).isoformat())]
    out = module.audit({'recommendation_history':{r['id']:r for r in rows}}, {},
                       NOW+timedelta(hours=5),NOW+timedelta(days=1))
    assert out['cohorts']['all']['settled'] == 1
    assert out['cohorts']['all']['pending'] == 2
    assert out['valid_recommended'] == 3


def test_latest_official_results_settle_original_selection_without_mutation():
    r = record(1,result='pending')
    prices = {'markets':{'1':{'1':{**candidate(1),'label':'','result':'홈패'}}}}
    out = module.audit({'recommendation_history':{'1':r}}, prices, NOW+timedelta(hours=5),NOW+timedelta(days=1))
    assert out['cohorts']['all']['roi'] == -1
    assert r['result'] == 'pending'


def test_shared_deadline_rejects_lookahead():
    a = record(1)
    b = record(2,kickoff_at=(NOW+timedelta(hours=5)).isoformat(),
               recorded_at=(NOW+timedelta(hours=3)).isoformat())
    out = module.audit({'recommendation_history':{'1':a,'2':b}}, {},NOW+timedelta(hours=6),NOW+timedelta(days=1))
    assert out['combo_temporal_feasibility_only']['daily']['not_known_together_by_T30'] == 1
    assert out['holdout_settled_after_policy_cutoff'] == 0


def test_paired_bootstrap_fixed_seed_and_budget_math():
    rows = [record(1),record(2,p=.59,result='miss')]
    result = module.paired_interval(rows,.60,repeats=100)
    # Same one date always sampled; filtered .6 ROI versus all -.2 ROI.
    assert result['roi_difference_95pct'] == pytest.approx([.8,.8])
    assert result == module.paired_interval(rows,.60,repeats=100)
