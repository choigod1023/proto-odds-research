from copy import deepcopy
from pathlib import Path
import importlib.util,json,sys
import numpy as np
import pytest

pytest.importorskip('sklearn',reason='optional research package; dedicated workflow installs it')

HERE=Path(__file__).resolve().parents[1]/'experiments/nested-search-20261102'
sys.path.insert(0,str(HERE))
spec=importlib.util.spec_from_file_location('nested_search_pipeline',HERE/'search.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
f=s._features


def test_complete_trial_registry_and_accuracy_first_selection_ignore_ROI():
    assert len(s.configurations())==4334
    trials=[{'id':'market','status':'ok','config':{'family':'market'},'metrics':{'hits':2,'logloss':1.,'roi':1.}},
            {'id':'nn','status':'ok','config':{'family':'residual'},'metrics':{'hits':3,'logloss':1.1,'roi':-1.}}]
    assert s.choose(trials)['id']=='nn'
    trials[0]['metrics']['hits']=3
    assert s.choose(trials)['id']=='market'


def test_search_loader_does_not_import_unrelated_cached_features(monkeypatch):
    import types
    fake=types.ModuleType('features');fake.poison=True
    monkeypatch.setitem(sys.modules,'features',fake)
    other=importlib.util.spec_from_file_location('nested_namespaced_loader_test',HERE/'search.py')
    module=importlib.util.module_from_spec(other);other.loader.exec_module(module)
    assert module.read(HERE/'prepared/development.jsonl')[0]['day'].startswith('2024')
    assert sys.modules['features'] is fake


def test_fold_training_does_not_include_embargoed_or_same_event_labels():
    rows=f.read(HERE/'prepared/development.jsonl');counts=[]
    for start,end in s.FOLDS:
        cutoff=(s.date.fromisoformat(start)-s.timedelta(days=7)).isoformat()
        train=[r for r in rows if r['day']<=cutoff];target=[r for r in rows if start<=r['day']<=end]
        assert not {r['id'] for r in train}&{r['id'] for r in target}
        counts.append((len(train),len(target)))
    assert counts==[(228,123),(347,44),(389,60)]


def test_targets_with_outcomes_and_future_training_are_rejected():
    rows=f.read(HERE/'prepared/development.jsonl');train=[rows[0]]
    target=deepcopy(rows[-1]);target['y']=0
    with pytest.raises(ValueError,match='labels forbidden'):
        s.predict_grid(train,[target],[{'id':'market','family':'market'}])
    target.pop('y')
    with pytest.raises(ValueError,match='future/embargoed'):
        s.predict_grid([rows[-1]],[{k:v for k,v in rows[0].items() if k!='y'}],[{'id':'market','family':'market'}])


def test_team_form_never_reads_current_or_future_results():
    rows=f.read(HERE/'prepared/development.jsonl');r=rows[240];x={k:r[k] for k in f.ALLOWED}
    history=f.history_rows();changed=deepcopy(history)
    cutoff=(f.date.fromisoformat(x['day'])-f.timedelta(days=7)).isoformat()
    for h in changed:
        if h['day']>cutoff:h['hg'],h['ag']=99,98
    assert f.make_features(x,history)==f.make_features(x,changed)
    with pytest.raises(ValueError,match='forbid score'):
        f.make_features({**x,'home_score':99},history)


def test_selected_configuration_is_best_development_trial_and_not_outer_winner():
    trials=f.read(HERE/'development/trials.jsonl')
    artifact=json.loads((HERE/'development/selected-model.json').read_text())
    assert len(trials)==4334 and all(r['status']=='ok' for r in trials)
    winner=s.choose(trials)
    assert winner['config']==artifact['config'] and winner['metrics']==artifact['development_metrics']


def test_label_free_prediction_succeeds_even_if_outer_labels_are_unreadable(tmp_path,monkeypatch):
    (tmp_path/'development').mkdir();(tmp_path/'prepared').mkdir()
    for rel in ('development/selected-model.json','prepared/development.jsonl','prepared/outer-features.jsonl'):
        (tmp_path/rel).write_bytes((HERE/rel).read_bytes())
    (tmp_path/'prepared/outer-labels.jsonl').write_text('invalid poison labels')
    monkeypatch.setattr(s,'HERE',tmp_path);monkeypatch.setattr(s,'freeze_check',lambda _:None)
    s.future_predict('synthetic-test-registration')
    manifest=json.loads((tmp_path/'outer-predictions/manifest.json').read_text())
    saved=f.read(tmp_path/'outer-predictions/predictions.jsonl')
    assert manifest['target_labels_read'] is False and len(saved)==77
    actual=f.read(HERE/'outer-predictions/predictions.jsonl')
    assert [r['id'] for r in saved]==[r['id'] for r in actual]
    for a,b in zip(saved,actual):
        assert a['day']==b['day'] and a['prices']==b['prices']
        assert set(a['predictions'])==set(b['predictions'])
        for name in a['predictions']:
            np.testing.assert_allclose(a['predictions'][name],b['predictions'][name],atol=1e-12,rtol=0)
