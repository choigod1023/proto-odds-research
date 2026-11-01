from copy import deepcopy
from datetime import date
import importlib.util
from pathlib import Path
import pytest

PATH=Path(__file__).resolve().parents[1]/'experiments/kleague-roi-diagnostic-20261101/run.py'
spec=importlib.util.spec_from_file_location('kleague_diagnostic',PATH)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_cancelled_nonfinal_invalid_score_and_wrong_year_not_training_inputs():
    row=dict(fixture_id='kleague:2023:1:1',league='KLEAGUE1',day='2023-03-01',
             home_team_id='A',away_team_id='B',source_status='FE',source_end_yn='Y',scores={'home':1,'away':0})
    assert m.official(row)['hg']==1
    for update in [dict(source_status='CANCELLED'),dict(source_end_yn='N'),dict(scores={'home':True,'away':0}),
                   dict(scores={'home':-1,'away':0}),dict(away_team_id='A'),dict(day='2024-03-01')]:
        with pytest.raises(ValueError):m.official({**row,**update})


def test_227_links_must_have_228_unique_official_sources_and_one_to_one_prices():
    nd={str(i):{} for i in range(227)};links={'o'+str(i):str(i) for i in range(227)}
    prior=[{}]*456;fixtures=[{'fixture_id':'o'+str(i)} for i in range(228)]
    m.require_coverage(nd,links,prior,fixtures)
    with pytest.raises(ValueError,match='coverage'):m.require_coverage(nd,links,prior,fixtures[:1])
    with pytest.raises(ValueError,match='coverage'):m.require_coverage(nd,links,prior,fixtures[:-1]+[fixtures[0]])
    bad={**links,'o1':'0'}
    with pytest.raises(ValueError,match='coverage'):m.require_coverage(nd,bad,prior,fixtures)


def test_future_evaluation_labels_cannot_change_earlier_month_states_or_picks(monkeypatch):
    source=[dict(id='past',day='2024-01-01',home_id='A',away_id='B',hg=1,ag=0),
            dict(id='future',day='2025-03-20',home_id='A',away_id='B',hg=2,ag=0)]
    evaluation=[dict(id='test',day='2025-02-12',home_id='A',away_id='B',home='A',away='B',prices=['2','3.5','4'],y=0)]
    calls=[]
    def fake_fit(rows,reference):
        calls.append(deepcopy(rows));return {'teams':['A','B'],'attack':[0.,0.],'defence':[0.,0.],
             'intercept':0.,'home':0.,'rho':0.,'reference_day':reference}
    monkeypatch.setattr(m,'fit',fake_fit)
    a,sa=m.forecasts(source,evaluation)
    changed=deepcopy(source);changed[-1]['hg']=99
    ev=deepcopy(evaluation);ev[0]['y']=2
    b,sb=m.forecasts(changed,ev)
    assert calls[0]==calls[1]==[source[0]] and sa==sb
    assert a[0]['forecasts']['time_weighted_dc']['p']==b[0]['forecasts']['time_weighted_dc']['p']


def test_saved_diagnostic_independent_probability_and_settlement_check(tmp_path):
    path=PATH.with_name('verify.py')
    s=importlib.util.spec_from_file_location('kleague_diagnostic_verify',path)
    v=importlib.util.module_from_spec(s);s.loader.exec_module(v)
    assert v.verify()['verified_forecasts']==454
    # A market probability mutation preserving its argmax and unit settlement
    # must still be rejected, not counted as an independently replayed forecast.
    import shutil,json
    for name in ('ledger.jsonl','states.json','results.json'):
        shutil.copyfile(PATH.parent/name,tmp_path/name)
    rows=[json.loads(s) for s in (tmp_path/'ledger.jsonl').read_text(encoding='utf8').splitlines()]
    p=rows[0]['forecasts']['market']['p'];p[0]+=.001;p[1]-=.001
    (tmp_path/'ledger.jsonl').write_text('\n'.join(json.dumps(r) for r in rows)+'\n',encoding='utf8')
    v.HERE=tmp_path
    with pytest.raises(AssertionError):v.verify()
