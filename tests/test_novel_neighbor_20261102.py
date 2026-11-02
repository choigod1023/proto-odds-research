import copy
import json
import hashlib
import importlib.util
from pathlib import Path
import pytest

P=Path(__file__).resolve().parents[1]/'experiments/novel-neighbor-20261102/run.py'
spec=importlib.util.spec_from_file_location('novel_neighbor_20261102',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_source_gate_and_selection_count(tmp_path, monkeypatch):
    rows=m.read();assert len(rows)==455 and len({r['id'] for r in rows})==455
    rows[0]['dc_training_cutoff']=rows[0]['day']
    folder=tmp_path/'inputs';folder.mkdir();data=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows).encode()
    (folder/'oof.jsonl').write_bytes(data)
    monkeypatch.setattr(m,'ROOT',tmp_path);monkeypatch.setattr(m,'SHA',hashlib.sha256(data).hexdigest())
    with pytest.raises(ValueError,match='cutoff'):m.read()

def test_score_fields_cannot_enter_forecast():
    rows=m.read();bank=m.prepare(rows[:60]);target={'market_p':rows[-1]['market_p'],'dc_p':rows[-1]['dc_p']}
    p=m.predict(bank,target,30);target['y']=0
    with pytest.raises(ValueError):m.predict(bank,target,30)
    assert sum(p)==pytest.approx(1) and all(v>0 for v in p)

def test_current_future_label_poison_has_no_effect_on_prediction():
    rows=m.read();hist=[r for r in rows if r['split']=='meta_history_2024'];test=[r for r in rows if r['split']=='evaluation_2025']
    bank=m.prepare(hist);x={'market_p':test[0]['market_p'],'dc_p':test[0]['dc_p']};before=m.predict(bank,x,30)
    poison=copy.deepcopy(test)
    for r in poison:r['y']=(r['y']+1)%3;r['hg']=999;r['ag']=999
    assert m.predict(bank,{'market_p':poison[0]['market_p'],'dc_p':poison[0]['dc_p']},30)==before

def test_neighbor_ties_use_id_and_exact_counts():
    x={'market_p':[.5,.3,.2],'dc_p':[.4,.3,.3]};vec=m.features(x)
    rows=[(f'{i:02d}',vec,0 if i<15 else 1) for i in range(30)]
    assert m.predict(rows,x,15)==pytest.approx([16/18,1/18,1/18])
    assert m.pick([.5,.5,0])==0

def test_bank_unmodified_after_prediction():
    rows=m.read();bank=m.prepare(rows[:60]);before=[(i,x.copy(),y) for i,x,y in bank]
    m.predict(bank,{'market_p':rows[-1]['market_p'],'dc_p':rows[-1]['dc_p']},30)
    for old,new in zip(before,bank):assert old[0]==new[0] and old[2]==new[2] and (old[1]==new[1]).all()


def test_full_test_outcome_poison_cannot_change_selection_or_picks():
    rows=m.read();before,ledger=m.run(rows);poison=copy.deepcopy(rows)
    for r in poison:
        if r['split']=='evaluation_2025':r['y']=(r['y']+1)%3;r['hg']=999;r['ag']=999
    after,poisoned=m.run(poison)
    assert before['selected_k']==after['selected_k'] and before['validation_logloss']==after['validation_logloss']
    for a,b in zip(ledger,poisoned):
        assert a['id']==b['id']
        for name in ('market','dc','neighbor'):
            assert a['forecasts'][name]['probability']==b['forecasts'][name]['probability']
            assert a['forecasts'][name]['pick']==b['forecasts'][name]['pick']
