"""Synthetic prospective lifecycle checks; never live ROI measurements."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

from ai_decision import annotate_options, build_decision_snapshot
from multi_market_recommendations import build, performance, result_for_offer
from prediction_runtime import PredictionRuntime
from public_tipster_votes import fixture_key, SOURCE
from live_market_refresh import settle_live_market_results

NOW = datetime(2026,11,6,7,tzinfo=timezone.utc)


def game():
    g = dict(year=2026, round=131, date='11.06(금) 19:00', sport='bs', league='KBO',
             home='KIA', away='SSG', status='경기전', live_market_observed_at=NOW.isoformat(), options=[])
    for number,market,label,line,sides in [('31','언더오버','U 8.5',8.5,('언더','오버')),
                                          ('32','핸디캡','H -1.5',-1.5,('핸디홈','핸디원정'))]:
        for n, side in enumerate(sides):
            g['options'].append(dict(게임번호=number,market=market,label=label,line=line,n_way=2,
                                    선택=side,배당=1.8+n*.2,시장확률=.55 if n==0 else .45))
    annotate_options(g)
    return g


def vote(g,side='over',voter='a'):
    return dict(source=SOURCE,event_type='first_observed',eligible_pre_event=True,
                provenance='live_http_observation',market_type='full_game_total',fixture=fixture_key(g),
                family='total',side=side,line=8.5,period='full_game',settlement='2way',candidate_only=True,
                voter=voter,pick_id=voter+'-total',content_hash='a'*64,
                source_url='https://pick79.com/posts/detail/market-test/',observed_at=NOW.isoformat())


def unavailable():return {'status':'unavailable','reason':'no_announced_lineup'}


def score_distribution(m1=2.):
    return dict(status='available', home_mean=2., away_mean=2., capture_hash='b'*64,
                companions={'m0_projected': {'home_mean': 2.2, 'away_mean': 2.2},
                            'm1_projected': {'home_mean': m1, 'away_mean': m1}})


@pytest.mark.parametrize('league,sport,home,away,line,sides,expected,model', [
    ('K리그1', 'sc', '울산HD', 'FC서울', -1., ('핸디홈', '핸디무', '핸디원정'),
     '핸디원정', 'kleague1-team-poisson-handicap-v1'),
    ('KBL', 'bk', '울산모비', '서울삼성', -10.5, ('핸디홈', '핸디원정'),
     '핸디원정', 'kbl-team-margin-handicap-v1'),
])
def test_league_handicap_replaces_only_current_exact_handicap(league, sport, home, away, line, sides, expected, model):
    g = game()
    g.update(league=league, sport=sport, home=home, away=away, options=g['options'][:2])
    g['options'].extend(dict(게임번호='32', market='핸디캡', label=f'H {line:g}', line=line,
                             n_way=len(sides), 선택=side, 배당=price)
                        for side, price in zip(sides, (1.55, 2.25, 2.8)))
    annotate_options(g)
    r = build(g, [], NOW.isoformat(), NOW.isoformat(), distribution=unavailable())
    total, handicap = r['items']
    assert total['model'] == 'market-baseline-v1'
    assert handicap['model'] == model and handicap['selection'] == expected
    assert handicap['comparisons']['market']['selection'] == '핸디홈'
    assert handicap['comparisons'][model]['offer_id'] == handicap['offer_id']
    assert handicap['estimated_roi'] == pytest.approx(handicap['probability'] * handicap['odds'] - 1)
    assert len(handicap['model_artifact_sha256']) == 64

    # An older record without a price clock retains the pre-existing policy.
    g.pop('live_market_observed_at')
    assert build(g, [], NOW.isoformat(), NOW.isoformat(), distribution=unavailable())['items'][1]['model'] == 'market-baseline-v1'
    freeze = NOW + timedelta(hours=2, minutes=30)
    assert build(g, [], NOW.isoformat(), freeze.isoformat(), distribution=unavailable())['status'] == 'after_t30'


def test_frontier_agrees_on_one_exact_pick_without_replacing_operating_vote():
    g = game()
    r = build(g, [vote(g)], NOW.isoformat(), NOW.isoformat(), distribution=score_distribution())
    total = next(i for i in r['items'] if i['market']=='언더오버')
    final = total['comparisons']['frontier_consensus']
    assert total['selection'] == '오버' and final['selection'] == '언더'
    arms = [total['comparisons']['frontier_'+m] for m in ('m0_actual', 'm0_projected', 'm1_projected')]
    assert final['probability'] == min(a['probability'] for a in arms)
    assert final['estimated_roi'] == min(a['estimated_roi'] for a in arms)
    assert final['probability'] >= .55 and final['estimated_roi'] > 0
    assert len([i for i in r['items'] if 'frontier_consensus' in i['comparisons']]) == 1
    assert total['comparisons']['frontier_tipster']['probability'] == pytest.approx(1.8 / 3.8)
    assert not total['validated_edge']


@pytest.mark.parametrize('case', ['disagreement', 'missing_model', 'duplicate_offer'])
def test_frontier_holds_for_unverified_or_conflicting_inputs(case):
    g = game()
    g['options'] = g['options'][:2]
    distribution = score_distribution()
    if case == 'disagreement':
        distribution = score_distribution(m1=10.)
    elif case == 'missing_model':
        distribution['companions'].pop('m1_projected')
    else:
        g['options'] += [dict(o, 게임번호='33') for o in g['options']]
    r = build(g, [], NOW.isoformat(), NOW.isoformat(), distribution=distribution)
    assert r['items']
    assert not any('frontier_consensus' in i['comparisons'] for i in r['items'])


def test_frozen_frontier_settles_its_own_direction_and_price(tmp_path, monkeypatch):
    import m0_forward_provider
    monkeypatch.setattr(m0_forward_provider, 'get_distribution', lambda *args: score_distribution())
    g = game()
    g['decision_snapshot'] = build_decision_snapshot(g, as_of=NOW.isoformat(),
        tipster_votes=[vote(g)], built_at=NOW.isoformat())
    path = tmp_path/'frontier.jsonl'
    r = PredictionRuntime(path, clock=lambda: NOW)
    r.record_pregame(g, kickoff='2026-11-06T10:00:00Z', market_observed_at=NOW.isoformat())
    r = PredictionRuntime(path, clock=lambda: NOW+timedelta(hours=10))
    feed = _result_feed(g, 14, [('31','언더오버','U 8.5','언더')])
    feed['markets']['131']['31']['odds'] = [1.55, 2.4]
    assert settle_live_market_results(feed, r) == 1
    frozen = next(iter(r.market_records().values()))
    grade = next(iter(frozen['market_recommendation_results'].values()))
    assert grade['result'] == 'miss'
    assert grade['comparisons']['frontier_consensus']['result'] == 'hit'
    assert grade['comparisons']['frontier_consensus']['net_units'] == '0.8'
    context = performance(r.market_records())['comparison_by_context']
    assert context['bs|KBO|언더오버|total_half|8.5|언더|frontier_consensus']['roi'] == pytest.approx(.8)
    assert context['bs|KBO|언더오버|total_half|8.5|언더|frontier_m1_projected']['hit_rate'] == 1
    assert settle_live_market_results(feed, r) == 0


def test_actual_market_selections_and_rules_with_no_vote():
    g=game();r=build(g,[],NOW.isoformat(),NOW.isoformat(),distribution=unavailable())
    assert r['status']=='recommended' and len(r['items'])==2
    assert {i['selection'] for i in r['items']}=={'언더','핸디홈'}
    assert all(i['model']=='market-baseline-v1' and not i['candidate_only'] for i in r['items'])
    assert all(o['period']=='full_game' and o['settlement_scope'] for o in g['options'])
    assert all(i['estimated_roi'] is None and not i['validated_edge'] for i in r['items'])


def test_exact_line_vote_can_choose_other_side_without_treating_share_as_probability():
    g=game();r=build(g,[vote(g)],NOW.isoformat(),NOW.isoformat(),distribution=unavailable())
    chosen=next(i for i in r['items'] if i['market']=='언더오버')
    assert chosen['selection']=='오버' and chosen['model']=='tipster-market-vote-v1'
    assert chosen['probability']==.45 and chosen['probability']!=1
    assert chosen['source_evidence'][0]['content_hash']=='a'*64
    tied=build(game(),[vote(g),vote(g,'under','b')],NOW.isoformat(),NOW.isoformat(),distribution=unavailable())
    assert not any(i['market']=='언더오버' for i in tied['items'])
    mismatched=vote(g);mismatched['line']=9.5
    r=build(game(),[mismatched],NOW.isoformat(),NOW.isoformat(),distribution=unavailable())
    assert all(i['model']=='market-baseline-v1' for i in r['items'])


def test_m0_comparison_does_not_self_promote_after_adverse_replay():
    g=game();g['options'][0]['배당']=2.1;g['options'][1]['배당']=1.6
    m0={'status':'available','home_mean':1.,'away_mean':1.,'capture_hash':'b'*64}
    r=build(g,[],NOW.isoformat(),NOW.isoformat(),distribution=m0)
    total=next(i for i in r['items'] if i['market']=='언더오버')
    assert total['selection']=='오버' and total['comparisons']['m0_actual']['selection']=='언더'
    assert not total['comparisons']['m0_actual']['adopted']


@pytest.mark.parametrize('mutation',['half','quarter','label','integer','duplicate'])
def test_unsupported_or_inconsistent_product_never_becomes_recommendation(mutation):
    g=game();g['options']=g['options'][:2]
    if mutation=='half':g['options'][0]['period']='first_half'
    elif mutation=='quarter':
        for o in g['options']:o.update(line=8.25,label='U 8.25')
    elif mutation=='label':g['options'][0]['label']='U 9.5'
    elif mutation=='integer':
        for o in g['options']:o.update(line=8,label='U 8')
    else:g['options'].append(dict(g['options'][0]))
    assert not build(g,[],NOW.isoformat(),NOW.isoformat(),distribution=unavailable())['items']


def test_failed_vote_source_and_deadline_never_fall_back_to_success():
    g=game()
    assert not build(g,[{'_tipster_load_error':True}],NOW.isoformat(),NOW.isoformat(),distribution=unavailable())['items']
    late=NOW+timedelta(hours=2,minutes=30)
    assert build(g,[],late.isoformat(),late.isoformat(),distribution=unavailable())['status']=='after_t30'


def test_same_inputs_preserve_recommendation_clock():
    g=game();g['market_recommendations']=build(g,[],NOW.isoformat(),NOW.isoformat(),distribution=unavailable())
    later=NOW+timedelta(minutes=1)
    assert build(g,[],later.isoformat(),later.isoformat(),distribution=unavailable())==g['market_recommendations']


def test_independent_market_ledger_official_grading_and_restart(tmp_path):
    g=game();g['decision_snapshot']=build_decision_snapshot(g,as_of=NOW.isoformat(),built_at=NOW.isoformat(),tipster_votes=[])
    assert g['decision_snapshot']['action']=='withhold'  # No winner product, markets still get recorded.
    path=tmp_path/'pregame.jsonl';r=PredictionRuntime(path,clock=lambda:NOW)
    r.record_pregame(g,kickoff='2026-11-06T10:00:00+00:00',market_observed_at=NOW.isoformat())
    before=path.read_bytes();records=r.market_records();assert len(records)==1 and not r.ui_records()
    assert r.record_pregame(g,kickoff='2026-11-06T10:00:00+00:00',market_observed_at=NOW.isoformat()) is None
    assert path.read_bytes()==before
    feed={'generated_at':'2026-11-06T14:00:00+00:00','markets':{'131':{}}}
    for number,market,label,result in [('31','언더오버','U 8.5','언더'),('32','핸디캡','H -1.5','핸디패')]:
        feed['markets']['131'][number]=dict(game_no=number,date=g['date'],sport=g['sport'],league=g['league'],
            home=g['home'],away=g['away'],market=market,label=label,n_way=2,result=result,odds=[1.8,2.0])
    r=PredictionRuntime(path,clock=lambda:NOW+timedelta(hours=9))
    assert settle_live_market_results(feed,r)==1
    final=performance(r.market_records());assert final['bets']==2 and final['hits']==1 and final['roi']==pytest.approx(-.1)
    assert settle_live_market_results(feed,r)==0
    assert performance(PredictionRuntime(path).market_records())==final
    wrong=deepcopy(feed);wrong['markets']['131']['31']['label']='U 9.5'
    wrong['generated_at']='2026-11-06T15:00:00+00:00'
    assert settle_live_market_results(wrong,r)==0


def test_refunds_do_not_add_hits_or_change_wallet():
    item={'odds':1.9,'offer_id':'o','selection_id':'s','market':'언더오버','line':8.5,
          'model':'market-baseline-v1','settlement_rule':'total_half'}
    void=result_for_offer(item,'void');assert void['net_units']=='0' and not void['paper_wallet_affected']


def _result_feed(g,hour,rows):
    return {'generated_at':f'2026-11-06T{hour:02d}:00:00+00:00','markets':{'131':{
        number:dict(game_no=number,date=g['date'],sport=g['sport'],league=g['league'],home=g['home'],away=g['away'],
                    market=market,label=label,n_way=2,result=result,odds=[1.8,2.0])
        for number,market,label,result in rows}}}


def _recorded(tmp_path,with_winner=False):
    g=game()
    if with_winner:
        g['options'] += [dict(게임번호='30',market='승패',label='',line=None,n_way=2,
            선택=s,배당=p,시장확률=q) for s,p,q in [('홈',1.9,.55),('원정',2.1,.45)]]
    g['decision_snapshot']=build_decision_snapshot(g,as_of=NOW.isoformat(),built_at=NOW.isoformat(),tipster_votes=[])
    runtime=PredictionRuntime(tmp_path/'cycle.jsonl',clock=lambda:NOW)
    runtime.record_pregame(g,kickoff='2026-11-06T10:00:00+00:00',market_observed_at=NOW.isoformat())
    return g,PredictionRuntime(tmp_path/'cycle.jsonl',clock=lambda:NOW+timedelta(hours=10))


def test_official_correction_can_return_to_an_earlier_grade(tmp_path):
    g,r=_recorded(tmp_path)
    for hour,result in [(14,'언더'),(15,'오버'),(16,'언더')]:
        feed=_result_feed(g,hour,[('31','언더오버','U 8.5',result)])
        assert settle_live_market_results(feed,r)==1
        assert settle_live_market_results(feed,r)==0
    stats=performance(r.market_records())
    assert stats['bets']==1 and stats['hits']==1 and stats['net_units']=='0.8'
    assert len([x for x in r.records() if x['record_type']=='settlement'])==3


def test_partial_market_results_preserve_winner_and_generator_preserves_markets(tmp_path):
    g,r=_recorded(tmp_path,with_winner=True)
    assert settle_live_market_results(_result_feed(g,14,[('30','승패','','홈승')]),r)==1
    event=g['event_id'];assert r.ui_records()[event]['result']=='hit'
    assert settle_live_market_results(_result_feed(g,15,[('31','언더오버','U 8.5','오버')]),r)==1
    assert r.ui_records()[event]['result']=='hit'
    before=deepcopy(r.market_records()[event]['market_recommendation_results'])
    assert r.settle_latest(event,outcome={'result':'hit','score':[5,3]},settled_at='2026-11-06T16:00:00Z',source='official-generator')
    assert r.market_records()[event]['market_recommendation_results']==before
    assert r.ui_records()[event]['result']=='hit'


def test_two_stale_runtimes_merge_under_one_ledger_lock(tmp_path):
    g,first=_recorded(tmp_path,with_winner=True)
    second=PredictionRuntime(tmp_path/'cycle.jsonl',clock=lambda:NOW+timedelta(hours=10))
    # Both have loaded the old graph before either adds a settlement.
    first.market_records();second.market_records();second._cached_settlements()
    assert settle_live_market_results(_result_feed(g,14,[('31','언더오버','U 8.5','언더')]),first)==1
    assert second.settle_latest(g['event_id'],outcome={'result':'hit','score':[5,3]},
                              settled_at='2026-11-06T15:00:00Z',source='official-generator')
    fresh=PredictionRuntime(tmp_path/'cycle.jsonl')
    assert fresh.ui_records()[g['event_id']]['result']=='hit'
    assert performance(fresh.market_records())['hits']==1
    assert settle_live_market_results(_result_feed(g,16,[('32','핸디캡','H -1.5','핸디패')]),second)==1
    assert second.ui_records()[g['event_id']]['result']=='hit'
    assert performance(second.market_records())['bets']==2
    assert performance(second.market_records())['hits']==1
