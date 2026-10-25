import json
import hashlib
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from broaden_tipster_fixed91 import fixtures_by_id,recover_ledger,read_verified,paired_day_delta,historical_round,_stable_id
from export_tipster_research import remote_source

def fixture():
    return dict(year=2026,round=105,game_no='8705',sport='bs',league='MLB',home='시카컵스 2',away='1 밀워브루',date_text='09.04(금) 08:15',market_family='승패')

def prediction():
    return dict(event_id='evt_6e14584db3266c79',kickoff='2026-09-03T23:15:00Z',captured_at='2026-09-03T22:00:00Z',as_of='2026-09-03T21:50:00Z',market_observed_at='2026-09-03T21:50:00Z',snapshot_id='x',record_hash='h',ledger_sequence=1,
      options=[dict(market='승패',label='',selection=s,final_probability=p,market_probability=p,odds=o) for s,p,o in [('홈',.6,1.5),('원정',.4,2.2)]])

def test_exact_fixture_id_and_score_independence():
    f=fixture();a=fixtures_by_id([f]);assert list(a)==['evt_6e14584db3266c79']
    assert a==fixtures_by_id([dict(f,home='시카컵스 99',away='0 밀워브루')])

def test_reissue_round_ambiguity_blocks_pairing():
    f=fixture();a=fixtures_by_id([f,dict(f,round=106),f]);assert next(iter(a.values()))['round_key'] is None

def test_archived_offer_round_unchanged_by_future_reissue():
    f=fixture();options=[dict(selection_id='sel_a',offer_id=_stable_id('off','sel_a',105,'8705'))]
    for fixtures in ([f],[f,dict(f,round=106)],[dict(f,round=106),f]):
        row=next(iter(fixtures_by_id(fixtures).values()))
        assert historical_round(row,options)=='105'
        assert historical_round(row,[dict(options[0],offer_id='unknown')]) is None

def test_future_capture_and_market_rejected():
    p=prediction();assert len(recover_ledger([p],[fixture()])[0])==1
    for key in ('captured_at','as_of','market_observed_at'):
        assert not recover_ledger([dict(p,**{key:'2026-09-03T23:00:00Z'})],[fixture()])[0]

def test_wrong_kickoff_and_unknown_id_rejected():
    p=prediction()
    assert not recover_ledger([dict(p,kickoff='2026-09-04T23:15:00Z')],[fixture()])[0]
    assert not recover_ledger([dict(p,event_id='wrong')],[fixture()])[0]

def test_unverified_or_modified_exports_fail(tmp_path):
    path=tmp_path/'records.jsonl';raw=b'{"x":1}\n';path.write_bytes(raw)
    path.with_suffix('.manifest.json').write_text(json.dumps(dict(verified_complete=True,rows=1,sha256=hashlib.sha256(raw).hexdigest())))
    assert read_verified(path)[0]==[{'x':1}]
    path.write_bytes(raw+b'{}\n')
    with pytest.raises(ValueError,match='Unverified'):read_verified(path)

def test_bootstrap_deterministic_and_zero_same_choices():
    rows=[dict(date='2026-09-01',model=[.8,.2],tipster=[.7,.3],odds=[1.5,2.5],y=0)]
    a=paired_day_delta(rows);assert a==paired_day_delta(rows)
    assert a['descriptive_day_bootstrap_95pct']==[0,0]

@pytest.mark.parametrize('kind',['events','predictions','fixtures','odds-fixtures'])
def test_remote_export_syntax_and_readonly(kind):
    source=remote_source(kind);compile(source,'remote','exec')
    assert 'mode=ro' in source and 'query_only=ON' in source

def test_saved_expansion_is_unique_frozen_and_budget_safe():
    root=Path(__file__).resolve().parents[1]
    r=json.loads((root/'experiments/tipster-broader-evidence-20261025/verified-results.json').read_text(encoding='utf8'))
    old=json.loads((root/'experiments/tipster-expand-91-20261025/final-results.json').read_text(encoding='utf8'))['results']['t30']['ledger']
    byid={x['id']:x for x in r['expanded_ledger']}
    assert len(byid)==len(r['expanded_ledger'])==173
    assert all(byid[x['id']]==x for x in old)
    assert r['new_events']==r['later_events']==109
    for name,weight in [('operating',1.),('hybrid91',.9),('tipster',0.)]:
        profit=0;hits=0
        for row in byid.values():
            side=max(range(2),key=lambda i:weight*row['model'][i]+(1-weight)*row['tipster'][i])
            y=0 if row['official']['home_score']>row['official']['away_score'] else 1
            assert y==row['y']
            hits+=side==y;profit+=(row['odds'][side] if side==y else 0)-1
        actual=r['comparison_expanded'][name]
        assert hits==actual['single']['hits']
        assert 100*profit/173==pytest.approx(actual['single']['roi_pct'])
        for replay in actual['conservative_paper'].values():
            months={}
            for t in replay['ticket_log']:
                # stored at is already UTC offset-bearing; replay month uses KST
                from matched_tipster_source import stamp,KST
                month=stamp(t['at']).astimezone(KST).strftime('%Y-%m')
                months[month]=months.get(month,0)+10000
            assert all(v<=50000 for v in months.values())
