import importlib.util
from pathlib import Path


def test_saved_date_sensitivity_matches_events_and_independent_decimal_settlement():
    path = Path(__file__).resolve().parents[1] / 'experiments/chronology-witness-20261031/verify.py'
    spec = importlib.util.spec_from_file_location('chronology_diagnostic_verifier', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.verify()
    assert result['forecasts_checked'] == 3092*4*2
    assert result['fixture_date_changes'] == 2
    assert result['strict_performance_certified'] is False
