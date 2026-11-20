import copy
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from tipster_forward_calibration import clusters,group_probability,training_rows,forward,fit,predict,stamp

def row(i=1):
    return dict(id=str(i),date='2026-09-04',at='2026-09-04T12:00:00-04:00',kickoff='2026-09-04T12:30:00-04:00',
                settled_at='2026-09-04T18:00:00-04:00',publisher_y=0,y=0,home='A',away='B',model=[.6,.4],
                tipster_evidence=[dict(author=a,side='A',observed='2026-09-04T11:00:00-04:00') for a in ('a','b')])

def test_no_future_labels_and_no_official_oracle():
    cutoff=stamp('2026-09-05T00:00:00-04:00');r=row()
    assert training_rows([r],cutoff)==[r]
    assert training_rows([dict(r,y=1)],cutoff)==[dict(r,y=1)]
    assert not training_rows([dict(r,settled_at=cutoff.isoformat())],cutoff)
    assert not training_rows([dict(r,settled_at=None)],cutoff)

def test_agreement_threshold_and_no_outcome_dependence():
    assert clusters([row(i) for i in range(4)])[1]==[]
    hist=[row(i) for i in range(5)];mapping,edges=clusters(hist)
    assert mapping['a']==mapping['b'] and len(edges)==1
    assert clusters([dict(r,y=1,publisher_y=1) for r in hist])==(mapping,edges)
    q,s,n=group_probability(row(),mapping);assert n==1 and q==1 and s==pytest.approx(4/6)

def test_future_tip_and_duplicate_author_rejected():
    r=row();r['tipster_evidence'][0]['observed']='2026-09-04T13:00:00-04:00'
    with pytest.raises(ValueError,match='Future'):clusters([r])
    r=row();r['tipster_evidence'][1]['author']='a'
    with pytest.raises(ValueError,match='Duplicate'):clusters([r])

def test_no_training_fallback_and_bounded_fitted_probabilities():
    hist=[row(i) for i in range(29)];mapping,_=clusters(hist)
    assert not fit(hist,mapping,True)['trained']
    hist.append(row(30));f=fit(hist,mapping,True)
    assert f['trained'] and 0<=f['b']<=1 and -1<=f['a']<=1
    result,_=predict(row(),mapping,fit(hist,mapping,False),f)
    assert all(0<=p<=1 for p in result.values())

def source_rows():
    p=Path(__file__).resolve().parents[1]/'experiments/tipster-broader-evidence-20261025/verified-results.json'
    return json.loads(p.read_text(encoding='utf8'))['expanded_ledger']

def test_all_official_results_can_change_without_changing_predictions():
    rows=source_rows();first,audit=forward(rows)
    modified=[dict(r,y=1-r['y']) for r in rows];second,other=forward(modified)
    assert audit==other
    assert {a:[r['model'] for r in rs] for a,rs in first.items()}=={a:[r['model'] for r in rs] for a,rs in second.items()}
    assert all(len(rs)==109 for rs in first.values())

def test_later_votes_and_labels_cannot_change_earlier_predictions():
    rows=source_rows();original,_=forward(rows);changed=copy.deepcopy(rows)
    for r in changed:
        if r['date']>='2026-09-10':
            r['publisher_y']=1-r['publisher_y'] if r['publisher_y'] is not None else None
            for t in r['tipster_evidence']:t['side']=r['away'] if t['side']==r['home'] else r['home']
    after,_=forward(changed)
    for a in original:
        assert [r['model'] for r in original[a] if r['date']<'2026-09-10']==[r['model'] for r in after[a] if r['date']<'2026-09-10']
