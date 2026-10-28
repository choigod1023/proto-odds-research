import pytest

from scripts.evaluate_tipster_year import consensus, describe, evaluate, interval
from tests.test_tipster_expansion_evaluation import pick


def row(**changes):
    return pick(**dict(dict(match_date='2026-07-01', proto_odds=[1.91,1.91],
                proto_offer_evidence='archived_exact_fixture'), **changes))


def test_multi_author_votes_count_one_fixture_and_ties_abstain():
    a = row(author='a')
    b = row(author='b', side='away')
    rows, excluded = consensus([a,b])
    assert not rows and excluded['tied_plurality']==1
    rows, excluded = consensus([a,b,row(author='c', side='away')])
    assert len(rows)==1 and rows[0]['voters']==3
    assert rows[0]['picks']['tipster']=='away'


def test_same_unknown_author_cannot_vote_once_per_article_and_conflicts_drop():
    a = row(author=None)
    rows, _ = consensus([a,dict(a,source_url='https://another/article')])
    assert rows[0]['voters']==1
    rows, excluded = consensus([a,dict(a,side='away')])
    assert not rows and excluded['all_authors_conflict']==1


def test_full_same_price_vectors_required_for_all_arms():
    a = row()
    rows, excluded = consensus([a,dict(a,author='second',proto_odds=[1.5,2.5])])
    assert not rows and excluded['missing_or_conflicting_full_proto_prices']==1


def test_bootstrap_uses_observed_weeks_not_number_of_posts():
    one = interval(['2026-01-01']*1000, [1]*1000, [1]*1000)
    assert one['low'] is None and one['observed_weeks']==1
    many = interval(['2026-01-01','2026-02-01'], [1,-1], [1,1])
    assert (many['low'],many['high'])==(-1,1)


def test_fixed_window_and_official_result_gate():
    valid = row()
    r = evaluate([valid,dict(valid,fixture_id='old',match_date='2025-07-01'),
                  dict(valid,fixture_id='unverified',result_evidence='publisher')])
    assert r['coverage']['unique_fixtures']==1
    assert r['excluded']['outside_fixed_window']==1
    assert r['excluded']['unverified result']==1
    assert r['coverage']['monthly_fixtures']['2026-02']==0
    assert r['frozen_operating_model_comparison'] is None


def test_monthly_risk_limit_joint_and_top_fixture_sensitivity():
    rows = [dict(row(fixture_id=str(i),side='away'),settlement='loss') for i in range(10)]
    s = describe(rows)
    assert s['monthly_capped']['bets']==5
    assert s['monthly_capped']['profit_krw']==-50000
    assert s['without_top_one_percent_fixtures']['remaining_roi']==-1
    assert s['roi_interval']['low'] is None


def test_votes_mix_shares_not_one_hot_plurality():
    rows = [row(author=str(i), side='away' if i<2 else 'home', proto_odds=[1.85,2.05]) for i in range(3)]
    # Market slightly favors home. A 2/3 vote share need not flip it, unlike one-hot.
    for r in rows:
        r['settlement']='win' if r['side']=='home' else 'loss'
    pooled, _ = consensus(rows)
    assert pooled[0]['picks']['tipster']=='away'
    assert pooled[0]['picks']['market90_new_tipster10']=='home'
