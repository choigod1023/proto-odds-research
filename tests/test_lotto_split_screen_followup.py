import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import lotto_split_screen as s
import lotto_split_screen_followup as f

def test_failed_gate_does_not_open_historical_source(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'OUT',tmp_path)
    monkeypatch.setattr(s.prior.old.resources,'constrain',lambda:None)
    monkeypatch.setattr(s,'protocol',lambda:{'frozen':True})
    monkeypatch.setattr(s,'gate',lambda records:{'passed':False})
    s.save('protocol.json',{'frozen':True});s.save('records.json',[])
    s.save('results.json',{'status':'completed','gate':{'passed':False}})
    result=f.run(tmp_path/'nonexistent-history')
    assert result['status']=='not_triggered'
    assert not (tmp_path/'actual-draws.json').exists()

def test_completed_followup_is_not_overwritten(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'OUT',tmp_path)
    monkeypatch.setattr(s.prior.old.resources,'constrain',lambda:None)
    monkeypatch.setattr(s,'protocol',lambda:{'frozen':True})
    monkeypatch.setattr(s,'gate',lambda records:{'passed':False})
    s.save('protocol.json',{'frozen':True});s.save('records.json',[])
    s.save('results.json',{'status':'completed','gate':{'passed':False}})
    s.save('actual-results.json',{'status':'not_triggered','original':'keep'})
    before=(tmp_path/'actual-results.json').read_bytes()
    f.run(None)
    assert (tmp_path/'actual-results.json').read_bytes()==before

def test_completed_study_does_not_restart_or_replace_resource_times(tmp_path,monkeypatch):
    monkeypatch.setattr(s,'OUT',tmp_path)
    monkeypatch.setattr(s.prior.old.resources,'constrain',lambda:None)
    monkeypatch.setattr(s,'protocol',lambda:{'frozen':True})
    def forbidden():raise AssertionError('completed study must not create sampler')
    monkeypatch.setattr(s,'Native',forbidden)
    s.save('protocol.json',{'frozen':True})
    s.save('results.json',{'status':'completed','resources_this_invocation':{'wall_seconds':123.45}})
    before=(tmp_path/'results.json').read_bytes()
    s.run()
    assert (tmp_path/'results.json').read_bytes()==before
