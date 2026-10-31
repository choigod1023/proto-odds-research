import copy
from datetime import date,timedelta
from decimal import Decimal
import importlib.util,json
from pathlib import Path
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
STUDY=ROOT/'experiments/all-sports-underdog-20261031'
AUDIT=ROOT/'experiments/validation-pipeline-audit-20261031'
def load(path):
    spec=importlib.util.spec_from_file_location(path.stem,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
c=load(STUDY/'common.py');a=load(STUDY/'replay_audit.py');report=load(STUDY/'report.py')


def fixtures(n=650):
    result=[]
    for i in range(n):
        d=(date(2023,1,1)+timedelta(days=i//5)).isoformat()
        result.append(dict(id=str(i),sport='bs',cohort='domestic_archive',league='test',home='A',away='B',day=d,kickoff=d+'T12:00:00',odds=[1.6,2.1],y=i%2,sources=['synthetic']))
    return result


def make_saved_study(folder,source,ledger,states):
    (folder/'inputs').mkdir();target=folder/'results/bs/domestic_archive';target.mkdir(parents=True)
    c.dump(folder/'inputs/test.jsonl.gz',source);c.dump(target/'ledger.jsonl.gz',ledger);c.save(target/'states.json',states)
    split='2023-03-01'
    data=dict(count=len(ledger),fallback_events=sum(r['fallback'] for r in ledger),cohort='domestic_archive',sport='bs',
              full=c.summarize(ledger),by_year={},by_league={},chronological_split_day=split,
              first_half=c.summarize([r for r in ledger if r['day']<split]),second_half=c.summarize([r for r in ledger if r['day']>=split]))
    c.save(target/'results.json',data)


def test_original_verifier_accepts_wrong_probability_but_new_replay_rejects(tmp_path):
    source=fixtures();ledger,states=c.forecasts(source)
    row=next(r for r in ledger if not r['fallback'] and abs(r['predictions']['outcome_form']['p'][0]-.5)>1e-5)
    p=row['predictions']['outcome_form']['p'];top=int(np.argmax(p));other=1-top
    epsilon=min(.00001,(p[top]-p[other])/4);p[top]-=epsilon;p[other]+=epsilon
    make_saved_study(tmp_path,source,ledger,states)
    old=load(AUDIT/'baseline_verifier.py');old.HERE=tmp_path
    assert old.verify()[2]['mismatches']==0
    with pytest.raises(a.AuditError,match='probability_state_mismatch'):a.check(source,ledger,states)


def test_cutoff_and_fallback_are_checked_against_protocol():
    source=fixtures();rows,states=c.forecasts(source)
    changed=copy.deepcopy(states);changed[0]['training_cutoff_inclusive']='2022-12-31'
    with pytest.raises(a.AuditError,match='cutoff'):a.check(source,rows,changed)
    changed=copy.deepcopy(states);changed[0]['fallback']=False
    with pytest.raises(a.AuditError,match='fallback'):a.check(source,rows,changed)


def test_coefficient_or_scaler_corruption_fails_independent_training_check():
    source=fixtures();rows,states=c.forecasts(source)
    changed=copy.deepcopy(states);s=next(s for s in changed if not s['fallback'])
    s['states']['outcome_form']['scale'][0]*=2
    import hashlib
    s['state_sha256']=hashlib.sha256(json.dumps(s['states'],sort_keys=True).encode()).hexdigest()
    with pytest.raises(a.AuditError,match='scaler'):a.check(source,rows,changed)


def test_market_identity_is_checked_even_without_fallback():
    source=fixtures();rows,states=c.forecasts(source)
    r=next(r for r in rows if not r['fallback']);p=r['predictions']['market']['p'];p[0]-=.001;p[1]+=.001
    with pytest.raises(a.AuditError,match='probability_state_mismatch'):a.check(source,rows,states)


def test_exact_no_underdog_comparison_retains_top_ties():
    rows,_=c.forecasts(fixtures(3))
    for r,group,net in zip(rows,['favorite','top_tie','underdog'],['1','2','-1']):
        r['predictions']['outcome_form'].update(group=group,net=net)
    allp=c.metrics(rows,'outcome_form','all');fav=c.metrics(rows,'outcome_form','favorites');non=c.metrics(rows,'outcome_form','non_underdogs');under=c.metrics(rows,'outcome_form','underdogs')
    assert Decimal(allp['net_units'])-Decimal(fav['net_units'])==1
    assert Decimal(allp['net_units'])-Decimal(non['net_units'])==Decimal(under['net_units'])==-1
    assert non['bets']==2


def test_upstream_conflict_cannot_be_resurrected_from_old_cache():
    import pandas as pd
    prepare=load(STUDY/'prepare.py')
    raw=dict(year=2023,round=2,date_text='01.01(일) 12:00',sport='bs',league='KBO',market_family='승패',n_way=2,
             home='A 4',away='2 B',odds='1.6,2.1',result='홈승',is_void=False)
    conflicts=prepare.raw_primary_conflicts(pd.DataFrame([raw,{**raw,'odds':'1.7,2.0'}]))
    cached=prepare.normalize('bs','KBO','2023-01-01T12:00:00','A','B',[1.6,2.1],0,'legacy_primary_cache')
    assert prepare.merge([cached])[0]  # Old join would silently retain this quote.
    fixed,audit=prepare.merge([cached],rejected=conflicts)
    assert fixed==[] and audit['cached_conflict_resurrections_prevented']==1


def fake_reports(net='1',ci=(.1,.2),period_net=None):
    def cell(value):return {'policies':{'underdogs':{'bets':10,'net_units':value}},'underdog_vs_skip':{'ci95':list(ci)}}
    result={}
    for sport in ('sc','bs','bk','vl'):
        full={m:cell(net) for m in c.MODELS[1:]};period={m:cell(net if period_net is None else period_net) for m in c.MODELS[1:]}
        result['domestic_archive/'+sport]={'full':full,'by_year':{'2023':period},'first_half':period,'second_half':period}
    return result


def test_evidence_gate_can_pass_positive_data_but_not_without_price_proof():
    assert report.assess(fake_reports(),execution_certified=True)['evidence_supports_blanket_inclusion_for_tested_models']
    assert not report.assess(fake_reports(),execution_certified=False)['evidence_supports_blanket_inclusion_for_tested_models']


def test_negative_mixed_and_missing_data_do_not_crash_or_force_a_conclusion():
    assert not report.assess(fake_reports('-1'),True)['evidence_supports_blanket_inclusion_for_tested_models']
    assert not report.assess(fake_reports(period_net='-1'),True)['evidence_supports_blanket_inclusion_for_tested_models']
    assert not report.assess({},True)['criteria']['four_sports_complete']
    assert not report.assess(fake_reports(ci=(-.1,.2)),True)['criteria']['every_conditional_interval_lower_positive']


def test_opportunity_interval_includes_weeks_with_no_underdogs():
    rows,_=c.forecasts(fixtures(2))
    rows[0]['day']='2023-01-01';rows[1]['day']='2023-02-01'
    rows[0]['predictions']['outcome_form'].update(group='underdog',net='-1')
    rows[1]['predictions']['outcome_form']['group']='favorite'
    per_bet=c.interval(rows,'outcome_form');per_op=c.opportunity_interval(rows,'outcome_form')
    assert per_bet['weeks']==1 and per_op['weeks']==2
    assert per_bet['mean']==-1 and per_op['mean']==-.5
    assert per_op['ci95'][0]==-1 and per_op['ci95'][1]==0


@pytest.mark.parametrize('k',[2,3])
@pytest.mark.parametrize('offset',[False,True])
def test_independent_objective_gradient_matches_central_differences(k,offset):
    rng=np.random.default_rng(4242);x=np.column_stack([np.ones(30),rng.normal(size=(30,4))]);y=np.arange(30)%k
    q=rng.dirichlet(np.ones(k),size=30);b=rng.normal(0,.1,size=(5,k));_,gradient=a.loss_gradient(b,x,y,q,offset)
    numeric=np.zeros_like(b);eps=1e-6
    for i in range(b.shape[0]):
        for j in range(k):
            hi=b.copy();lo=b.copy();hi[i,j]+=eps;lo[i,j]-=eps
            numeric[i,j]=(a.loss_gradient(hi,x,y,q,offset)[0]-a.loss_gradient(lo,x,y,q,offset)[0])/(2*eps)
    assert np.max(np.abs(gradient-numeric))<1e-6


@pytest.mark.parametrize('k',[2,3])
def test_training_detects_strong_signal_and_loses_it_when_labels_shuffled(k):
    rng=np.random.default_rng(19048);x=rng.normal(size=(1200,4));b=rng.normal(size=(4,k));y=np.argmax(x@b,axis=1);q=np.full((1200,k),1/k)
    good=c.predict(c.train(x[:800],y[:800],q[:800],False),x[800:],q[800:]).argmax(1)
    bad=c.predict(c.train(x[:800],rng.permutation(y[:800]),q[:800],False),x[800:],q[800:]).argmax(1)
    assert (good==y[800:]).mean()>.8
    assert (good==y[800:]).mean()-(bad==y[800:]).mean()>.2
