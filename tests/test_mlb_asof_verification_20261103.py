import importlib.util,pathlib
p=pathlib.Path(__file__).resolve().parents[1]/'experiments/mlb-asof-verification-20261103/verify.py'
s=importlib.util.spec_from_file_location('mlb_asof',p);v=importlib.util.module_from_spec(s);s.loader.exec_module(v)
def test_reject_future_provider_timestamp():
 assert not v.eligible('20260401_130000','20260401_133009')
 assert v.eligible('20260401_133009','20260401_133009')
 assert v.eligible('20260401_154000','20260401_133009')
def test_original_source_bytes_and_timecodes():
 v.main()
