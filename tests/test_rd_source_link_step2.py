from copy import deepcopy
from pathlib import Path
from datetime import datetime,timedelta,timezone
import json
import sys

sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
import rd_source_link_step2 as m


def case():
    return dict(cohort='retrospective_x__claimed_time_only',event_id='x',league='KBO',home='A',away='B',
                kickoff='2026-10-30T10:00:00+09:00',decision_at='2026-10-30T09:30:00+09:00',
                available_at='2026-10-29T00:00:00+09:00',day='2026-10-30',issue=[2026,100],
                game_no='1',sides=['home','away'],market_p=[.6,.4],external_p=[1.,0.],
                odds=[1.5,2.5],outcome=0,void=False,auxiliary=False,product_id='p')


def vote(author='a',selection='home',sha='a'*64,site='sample.test'):
    return dict(event_id='x',league='KBO',kickoff='2026-10-30T10:00:00+09:00',selection=selection,
                market='full_game_moneyline',author=author,source_site=site,source_url='https://'+site+'/analysis/'+sha[:5],
                published_at_claim='2026-10-29T00:00:00+09:00',observed_at='2026-10-30T20:00:00+09:00',
                document_sha256=sha,join_evidence={'method':'explicit_fixture'})


def test_tokyo_calendar_adapter_preserves_original_fact_and_bound_assumption():
    raw={'feature_evidence':{'home_x':dict(max_fact_date='2026-10-27',fact_date_timezone='Asia/Tokyo',max_fact_time=None)}}
    adapted=m.calendar_adapter(raw)
    assert raw['feature_evidence']['home_x']['fact_date_timezone']=='Asia/Tokyo'
    meta=adapted['feature_evidence']['home_x']
    assert meta['fact_date_timezone']=='Asia/Seoul' and meta['source_calendar_timezone']=='Asia/Tokyo'
    assert meta['max_fact_time'] is None


def test_tokyo_actual_completion_clock_does_not_require_or_invent_calendar_date():
    raw={'feature_evidence':{'home_x':dict(max_fact_time='2026-03-29T15:41:00+09:00',fact_date_timezone='Asia/Tokyo')}}
    assert m.calendar_adapter(raw)==raw


def test_publication_claim_is_not_historical_receipt():
    assert m.admissible_vote(vote(),case())[0]
    assert not m.admissible_vote(dict(vote(),published_at_claim='2026-10-30T10:30:00+09:00'),case())[0]
    assert not m.admissible_vote(dict(vote(),observed_at='2026-10-28T00:00:00+09:00'),case())[0]
    assert not m.admissible_vote(dict(vote(),published_at_claim='2026-10-29T00:00:00'),case())[0]
    assert not m.admissible_vote(dict(vote(),market='핸디캡'),case())[0]
    assert not m.admissible_vote(dict(vote(),market=None),case())[0]


def test_copied_documents_are_not_independent_authors():
    a,b=vote(),vote(author='other',site='different.test')
    rows,audit=m.independent_votes([a,b])
    assert len(rows)==1 and audit['copied_document_rows_omitted']==1
    assert m.independent_votes([a,dict(b,selection='away')])[0]==[]


def test_source_display_aliases_do_not_duplicate_author_identity():
    a,b=vote(),vote(sha='b'*64)
    b.update(source_site='another display label',source_url='https://www.sample.test/other',observed_at='2026-10-31T00:00:00Z')
    rows,audit=m.independent_votes([a,b])
    assert len(rows)==1 and audit['later_or_duplicate_author_rows_omitted']==1


def test_same_article_query_alias_is_not_a_new_independent_opinion():
    a,b=vote(),vote('different-author','home','b'*64)
    a['source_url']='https://www.goli.tv/board/analysis/123?page=1'
    b['source_url']='https://goli.tv/board/analysis/123?page=2&utm_source=x'
    assert len(m.independent_votes([a,b])[0])==1
    b['selection']='away'
    assert m.independent_votes([a,b])[0]==[]


def test_article_identity_keeps_real_query_ids_and_hash_links_are_transitive():
    a,b,c=vote(),vote('b','home','b'*64),vote('c','home','b'*64,site='other.test')
    a['source_url']='https://sample.test/board.php?wr_id=1&page=1'
    b['source_url']='https://sample.test/board.php?wr_id=1&page=2'
    assert len(m.independent_votes([a,b,c])[0])==1
    b['source_url']='https://sample.test/board.php?wr_id=2'
    assert len(m.independent_votes([a,b])[0])==2


def test_first_author_conflict_and_later_revision_are_not_cherry_picked():
    a,b=vote(),vote(selection='away',sha='b'*64)
    assert m.independent_votes([a,b])[0]==[]
    b['observed_at']='2026-10-31T00:00:00Z'
    assert m.independent_votes([a,b])[0][0]['selection']=='home'


def test_extended_tie_abstains_instead_of_filling_a_pick():
    result=m.compare_votes([case()],[vote()],[vote('b','away','b'*64)])
    assert result['matched_cases']==0 and result['audit']['extended_vote_tie_no_selection']==1
    policies={r['model']:r for r in result['policy_results']}
    assert policies['old_vote_direction_only']['placed']==1
    assert policies['new_vote_direction_only']['placed']==0
    assert policies['new_vote_direction_only']['opportunity_budget_roi_pct']==0
    assert policies['old_vote_direction_only']['opportunity_budget_roi_pct']==50


def test_unknown_old_electorate_cannot_be_replaced_with_a_pseudo_vote():
    r=case();r['external_p']=[.7,.3]
    result=m.compare_votes([r],[vote()],[vote('b','home','b'*64)])
    assert result['matched_cases']==0 and result['audit']['old_electorate_disagrees_with_frozen_baseline']==1


def test_real_new_independent_votes_change_direction_and_use_same_price():
    r=case();r['external_p']=[2/3,1/3]
    old=[vote(),vote('b','home','b'*64),vote('c','away','c'*64)]
    new=[vote('d','away','d'*64),vote('e','away','e'*64)]
    result=m.compare_votes([r],old,new)
    assert result['matched_cases']==1 and result['cases'][0]['added_documents']==2
    arms={x['model']:x for x in result['results']}
    assert arms['old_vote_direction_only']['metrics']['roi_pct']==50
    assert arms['new_vote_direction_only']['metrics']['roi_pct']==-100
    assert arms['new_vote_direction_only']['metrics']['brier'] is None
    assert arms['expanded_fixed91']['changed_vote_directions']==1


def test_no_new_documents_is_explicit_and_no_fake_improvement():
    result=m.compare_votes([case()],[vote()],[deepcopy(vote())])
    assert result['matched_cases']==1 and result['cases'][0]['added_documents']==0
    arms={x['model']:x['metrics']for x in result['results']}
    assert arms['old_fixed91']==arms['expanded_fixed91']


def test_ineligible_identity_bridge_still_blocks_false_independence():
    a=vote();bridge=vote('bridge','home','a'*64,site='second.test')
    bridge.update(published_at_claim=None,source_url='https://second.test/one')
    new=vote('new','home','b'*64,site='second.test');new['source_url']=bridge['source_url']
    result=m.compare_votes([case()],[a,bridge],[new])
    assert result['cases'][0]['added_documents']==0


def test_conflicting_copied_new_direction_abstains_without_losing_old_opportunity():
    result=m.compare_votes([case()],[vote()],[vote('b','away','a'*64,site='other.test')])
    assert result['matched_cases']==0 and result['audit']['extended_no_valid_documents']==1
    arms={r['model']:r for r in result['policy_results']}
    assert arms['old_vote_direction_only']['placed']==1 and arms['new_vote_direction_only']['placed']==0


def test_copies_of_later_author_documents_cannot_create_new_voters():
    old=[vote('A','home','a'*64),vote('A','away','b'*64),vote('A','away','c'*64)]
    for i,v in enumerate(old):v['observed_at']=f'2026-10-29T0{i+1}:00:00+09:00'
    copied=[vote('B','away','b'*64,site='copy.test'),vote('C','away','c'*64,site='third.test')]
    copied[0]['observed_at']='2026-10-29T00:30:00+09:00';copied[1]['observed_at']='2026-10-29T00:40:00+09:00'
    assert m.independent_votes(old)[0][0]['selection']=='home'
    votes,audit=m.independent_votes(old+copied)
    assert votes==[] and audit['dependent_author_cluster_conflict']==3
    result=m.compare_votes([case()],old,copied)
    assert result['matched_cases']==0 and result['audit']['extended_no_valid_documents']==1


def test_consistent_price_offset_does_not_relabel_saved_market_or_use_test_labels():
    rows=[];index={}
    for i in range(90):
        start=datetime(2026,1,1,10,tzinfo=timezone(timedelta(hours=9)))+timedelta(days=i)
        r=case();r.update(event_id=f'e{i}',product_id=f'p{i}',home=f'h{i}',away=f'a{i}',
                         kickoff=start.isoformat(),decision_at=(start-timedelta(minutes=30)).isoformat(),
                         available_at=(start-timedelta(days=1)).isoformat(),day=start.date().isoformat(),
                         outcome=i%2,market_p=[.9,.1],odds=[1.8,2.1])
        rows.append(r)
        feature={k:r[k]for k in ('cohort','event_id','league','kickoff','decision_at')}
        feature.update(features={'home_x':float(i%2)},feature_evidence={'home_x':{
            'max_fact_time':r['available_at'],'observed_at':r['available_at'],
            'source_sha256':['a'*64],'availability_tier':'retrospective_lagged'}})
        index[('x',r['cohort'],r['event_id'],r['kickoff'])]=[feature]
    before=deepcopy(rows)
    result=m.consistent_price_offset(rows,index,'x','retrospective_lagged')
    assert result['results'] and rows==before
    assert result['fits'][0]['fit']['offset'] is True
    assert result['results'][0]['baselines']['original_market']['brier']!=result['results'][0]['baselines']['consistent_inverse_price_market']['brier']
    for r in rows[-18:]:r['outcome']=1-r['outcome']
    perturbed=m.consistent_price_offset(rows,index,'x','retrospective_lagged')
    assert [f['fit']for f in result['fits']]==[f['fit']for f in perturbed['fits']]
    assert [p['probabilities']for p in result['predictions']]==[p['probabilities']for p in perturbed['predictions']]


def test_later_period_loads_frozen_coefficients_without_fitting(tmp_path,monkeypatch):
    c=case();f={k:c[k]for k in ('cohort','event_id','league','kickoff','decision_at')}
    f.update(features={'home_x':1.},feature_evidence={'home_x':dict(max_fact_time=c['available_at'],
             observed_at=c['available_at'],source_sha256=['a'*64],availability_tier='retrospective_lagged')})
    index={('later',c['cohort'],c['event_id'],c['kickoff']):[f]}
    model=dict(status='fitted',k=2,offset=True,intercept_only=False,beta=[0.,0.,0.],
               preprocessing=dict(names=['home_x'],medians=[0.],scales=[1.]))
    path=tmp_path/'frozen.json';path.write_text(json.dumps(dict(model=model,fit_context=dict(
        cohort=c['cohort'],league='KBO',train_ids=['prior'],test_ids=['older'],test_end='2026-05-24'))))
    def forbidden(*args,**kwargs):raise AssertionError('later labels must not be fitted')
    monkeypatch.setattr(m.m,'fit_model',forbidden)
    result,p=m.fixed_later_period([c],index,'later',path)
    assert result['model_refit']is False and result['events']==1
    c['outcome']=1-c['outcome']
    changed,q=m.fixed_later_period([c],index,'later',path)
    assert p[0]['probabilities']==q[0]['probabilities'] and result['metrics']!=changed['metrics']
    c['day']='2026-05-20'
    assert m.fixed_later_period([c],index,'later',path)[0]['events']==0
