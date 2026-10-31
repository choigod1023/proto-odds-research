import copy
import importlib.util
from pathlib import Path
from decimal import Decimal
import pytest

P=Path(__file__).resolve().parents[1]/'experiments/expert-information-20261031/run.py'
spec=importlib.util.spec_from_file_location('expert_information_research',P)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def row(e,decision,kickoff,outcome):
    f={'event_id':e,'league':'KBO','decision_at':decision,'kickoff':kickoff,'sides':['home','away'],'market_p':[.55,.45],'odds':[1.8,2.0],'groups':[[('site','author','home')]],'previous_fixed_pick':'home'}
    return {'features':f,'outcome':outcome,'available_at':(m.instant(kickoff)+m.timedelta(hours=96)).isoformat()}


def test_pregame_and_future_outcome_poisoning():
    rs=[row('a','2026-05-01T10:00:00+00:00','2026-05-01T11:00:00+00:00',0),row('b','2026-07-01T10:00:00+00:00','2026-07-01T11:00:00+00:00',1),row('c','2026-07-02T10:00:00+00:00','2026-07-02T11:00:00+00:00',0)]
    rs += [row('v'+str(i),'2026-06-10T10:00:00+00:00','2026-06-10T11:00:00+00:00',i%2) for i in range(30)]
    out,_=m.predictions(rs); poisoned=copy.deepcopy(rs);poisoned[1]['outcome']=0;poisoned[2]['outcome']=1
    out2,_=m.predictions(poisoned)
    assert [x for x in out if x['event_id'] in ('b','c')]==[x for x in out2 if x['event_id'] in ('b','c')]
    assert len(m.prior_rows(rs,m.instant('2026-07-02T10:00:00+00:00')))==31
    f=copy.deepcopy(rs[1]['features']);f['outcome']=1
    with pytest.raises(ValueError):m.predict({'kind':'market'},f)


def test_duplicate_document_and_conflicting_votes():
    v={'source':'site','author':'a','side':'home','document_sha256':'a'*64,'source_reported_published_at':'2026-07-01T08:00:00+00:00'}
    t=m.instant('2026-07-01T10:00:00+00:00');one=m.groups([v],t,['home','away'])
    assert one==m.groups([v,copy.deepcopy(v)],t,['home','away'])
    conflict=dict(v,side='away');assert m.groups([v,conflict],t,['home','away'])==[]
    assert m.groups([dict(v,source_reported_published_at='2026-07-01T11:00:00+00:00')],t,['home','away'])==[]


def test_zero_offset_unseen_author_and_no_mutation():
    rs=[row('a','2026-05-01T10:00:00+00:00','2026-05-01T11:00:00+00:00',0)]
    model=m.fit(rs,'expert_hierarchical'); f=copy.deepcopy(rs[0]['features']); original=copy.deepcopy(f)
    market=m.predict({'kind':'market'},f); assert market==[.55,.45]
    model['coef']=[0]*len(model['coef']); f['groups']=[[('unseen','new','away')]]
    assert m.predict(model,f)==market; assert rs[0]['features']==original
    assert m.design(f,model['index'])[:,model['index'][('author','KBO','site','author')]].tolist()==[0,0]


def test_independent_decimal_settlement():
    r=row('x','2026-07-01T10:00:00+00:00','2026-07-01T11:00:00+00:00',1)
    fs=[{'event_id':'x','league':'KBO','decision_at':r['features']['decision_at'],'mode':'frozen','model':'market','probabilities':[.55,.45],'pick':0,'training_n':0}, {'event_id':'x','league':'KBO','decision_at':r['features']['decision_at'],'mode':'frozen','model':'expert_global','probabilities':[.4,.6],'pick':1,'training_n':1}]
    snapshot=copy.deepcopy(fs);metrics, ledger=m.measure(fs,[r]); assert fs==snapshot
    actual=next(x for x in ledger if x['model']=='expert_global'); assert Decimal(actual['profit'])==Decimal('2.0')-Decimal(1)
    cell=next(x for x in metrics if x['model']=='expert_global' and x['league']=='KBO');assert cell['roi']==1 and cell['pick_changes_vs_market']==1


def test_label_availability_midnight_boundary():
    r=row('x','2026-06-01T20:00:00+00:00','2026-06-02T00:00:00+00:00',1)
    cutoff=m.instant('2026-06-06T00:00:00+00:00')
    assert m.prior_rows([r],cutoff)==[]
    assert m.prior_rows([r],cutoff,inclusive=True)==[r]


def test_validation_minimum_not_relaxed():
    r=row('x','2026-06-01T00:00:00+00:00','2026-06-01T01:00:00+00:00',0)
    with pytest.raises(ValueError,match='minimum 30'):m.predictions([r])


def test_selection_excludes_labels_unavailable_july_first():
    rs=[row('train','2026-05-01T10:00:00+00:00','2026-05-01T11:00:00+00:00',0)]
    rs += [row('v'+str(i),'2026-06-10T10:00:00+00:00','2026-06-10T11:00:00+00:00',i%2) for i in range(30)]
    rs += [row('late','2026-06-30T10:00:00+00:00','2026-06-30T11:00:00+00:00',0),row('test','2026-07-01T10:00:00+00:00','2026-07-01T11:00:00+00:00',1)]
    a,stats=m.predictions(rs); rs[-2]['outcome']=1; b,stats2=m.predictions(rs)
    assert stats['validation_n']==30 and stats['validation_excluded_not_settled_by_july1']==1
    assert stats['validation_logloss']==stats2['validation_logloss']
    assert a==b


def test_original_offer_cohort_and_ambiguous_case_fail_closed():
    inputs=m.ROOT/'inputs'
    targets=m.read_gz(inputs/'targets.jsonl.gz'); frames=m.read_gz(inputs/'votes.jsonl.gz'); cases=m.read_gz(inputs/'cases.jsonl.gz')
    target=next(t for t in targets if t.get('actual_votes') is True)
    frame=next(f for f in frames if f['event_id']==target['event_id'])
    case=next(c for c in cases if c['event_id']==target['event_id'] and c['cohort']==target['cohort'] and c['league']==target['league'])
    good,_,_=m.eligible([target],[frame],[case]); assert len(good)==1
    bad=copy.deepcopy(frame); bad['cohort']='other'
    assert not m.eligible([target],[bad],[case])[0]
    assert not m.eligible([target],[frame],[case,copy.deepcopy(case)])[0]
    bad=copy.deepcopy(case); bad['game_no']='not-the-offer'
    assert not m.eligible([target],[frame],[bad])[0]


def test_source_hash_is_not_a_document_hash():
    at=m.instant('2026-07-01T09:00:00+00:00')
    source={'source':'s','author':'a','side':'home','source_sha256':'b'*64,'source_reported_published_at':'2026-07-01T08:00:00+00:00'}
    second=dict(source,author='b')
    assert len(m.groups([source,second],at,['home','away']))==2
    source['document_sha256']='c'*64;second['document_sha256']='c'*64
    assert len(m.groups([source,second],at,['home','away']))==1
