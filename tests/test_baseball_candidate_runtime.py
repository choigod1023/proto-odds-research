from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import sys
import json

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ai_decision import build_decision_snapshot
from prediction_runtime import ledger_features,prediction_payload
from baseball_candidate_runtime import capture
from operating_model_comparison import compare
from operating_ledger_inputs import normalize_operating_prediction
from mlb_candidate_feed import extract,BAT_FIELDS,PIT_FIELDS
from test_mlb_candidate_feed import feed
from test_operating_ledger_inputs import operating_record
from test_kbo_candidate_source import collect,Session,clock


def mlb_game():
    raw=feed()
    for side in ('home','away'):
        for row in raw['liveData']['boxscore']['teams'][side]['players'].values():
            row['seasonStats']['batting']={k:1 for k in BAT_FIELDS}
            row['seasonStats']['pitching']={k:1 for k in PIT_FIELDS}
    source=extract(raw,game_pk=1,scheduled_at='2026-10-29T15:00:00Z',received_at='2026-10-29T13:00:00Z',
                   source_url='https://statsapi.mlb.com/api/v1.1/game/1/feed/live',response_sha256='a'*64)
    game=dict(league='MLB',sport='bs',home='Home',away='Away',year=2026,round=128,date='10.30(금) 00:00',
              live_market_observed_at='2026-10-29T13:00:00Z',
              options=[dict(market='승패',label='',선택=s,배당=o,시장확률=p,게임번호='1')
                       for s,o,p in [('홈',1.8,.52),('원정',1.9,.48)]])
    snapshot=build_decision_snapshot(game,as_of='2026-10-29T13:01:00Z',tipster_votes=[])
    game['decision_snapshot']=snapshot
    game['research_player_inputs']=dict(status='captured',observed_at='2026-10-29T13:00:00Z',candidate_input=source)
    return game


def test_same_snapshot_mlb_candidate_preserves_operating_decision_and_price():
    game=mlb_game();original=deepcopy(game)
    features=ledger_features(game)
    arm=features['model_comparison']['learned_candidates']['mlb_frozen_linear_l2_10']
    assert arm['status']=='available',arm
    assert arm['selection'] in {'홈','원정'}
    assert arm['selection_id'] in {o['selection_id'] for o in game['options']}
    assert arm['probability'] is not None and not arm['promoted']
    assert arm['starter_final_confirmed'] is False
    assert game==original
    assert capture(game['decision_snapshot'],features)==features['model_comparison']['learned_candidates']


def test_real_ledger_projection_can_score_saved_candidate_even_if_operating_abstained():
    game=mlb_game();s=game['decision_snapshot']
    record=dict(record_type='prediction',snapshot_id=s['decision_id'],event_id=s['event_id'],
                decision_id=s['decision_id'],as_of=s['as_of'],captured_at='2026-10-29T13:02:00Z',
                kickoff='2026-10-29T15:00:00Z',market_observed_at='2026-10-29T13:00:00Z',
                model=s['model'],features=ledger_features(game),predictions=prediction_payload(game),
                input_revision_hash=s['input_revision_hash'],ledger_sequence=1)
    result=compare([record],[dict(source='proto',season=2026,round=128,game_no='1',kickoff='2026-10-30T00:00:00',
        league='MLB',sport='bs',home_team='Home',away_team='Away',home_score=3,away_score=1,
        observed_at='2026-10-29T18:00:00Z',result_state='confirmed')],evaluated_at='2026-10-29T19:00:00Z')
    assert len(result['events'])==1,result['excluded']
    candidate=result['events'][0]['arms']['mlb_frozen_linear_l2_10']
    assert candidate['status']=='selected'
    group=result['policies']['tipster-vote-v1']['all_opportunities']
    assert group['mlb_frozen_linear_l2_10']['settled']==1
    assert group['operating']['selections']==0


def test_missing_or_late_source_stays_unavailable_without_market_fallback():
    game=mlb_game()
    game['research_player_inputs']['candidate_input']['observed_at']='2026-10-29T13:05:00Z'
    arm=ledger_features(game)['model_comparison']['learned_candidates']['mlb_frozen_linear_l2_10']
    assert arm['status']=='unavailable'
    assert 'collected_at' in arm['reason']


def test_kbo_canonical_state_keeps_precision_and_infers_from_same_vote_offers():
    states=collect(session=Session(),clock=clock())['daily_states']
    row,game=operating_record()
    # This synthetic fixture uses the real frozen artifact/state contract, not
    # an assertion of historical live collection or performance.
    game.update(league='KBO',home='KIA',away='삼성',year=2026,round=128,date='10.30(금) 18:30',
                research_kbo_daily_state=states['2026-10-30'],live_market_observed_at='2026-10-29T14:00:00Z')
    game['tipster_hybrid']=dict(version='tipster-vote-v1',status='applied',authors=1,
        vote_counts={'홈':1,'원정':0},vote_shares={'홈':1.,'원정':0.},latest_observed_at='2026-10-29T14:00:00Z')
    snap=game['decision_snapshot']
    snap['as_of']='2026-10-29T14:02:00Z'
    features=ledger_features(game)
    assert features['research_kbo_daily_state']==states['2026-10-30']
    arm=features['model_comparison']['learned_candidates']['kbo_frozen_e0_residual']
    assert arm['status']=='available',arm
    assert not arm['promoted'] and arm['seed_historical_availability']=='unproven'


def test_nonbaseball_path_does_not_load_artifacts_or_read_files(monkeypatch):
    import mlb_frozen_candidate
    monkeypatch.setattr(mlb_frozen_candidate,'load_mlb_artifacts',lambda *a:(_ for _ in ()).throw(AssertionError()))
    p=capture({},dict(fixture={'league':'K리그1'}))
    assert all(a['status']=='unavailable' for a in p.values())


def test_real_runtime_automatically_settles_candidate_when_operating_abstains(tmp_path):
    from prediction_runtime import PredictionRuntime
    from live_market_refresh import settle_live_market_results
    game=mlb_game()
    runtime=PredictionRuntime(tmp_path/'ledger.jsonl',clock=lambda:datetime(2026,10,29,13,2,tzinfo=timezone.utc))
    saved=runtime.record_pregame(game,kickoff=datetime(2026,10,29,15,tzinfo=timezone.utc),
                                 market_observed_at='2026-10-29T13:00:00Z')
    prediction=deepcopy(saved.record)
    assert prediction['predictions']['action']=='withhold'
    runtime.ledger._clock=lambda:datetime(2026,10,29,18,1,tzinfo=timezone.utc)
    feed=dict(generated_at='2026-10-29T18:00:00Z',markets={'128':{'1':dict(game_no='1',date=game['date'],
        sport='bs',league='MLB',home='Home',away='Away',market='승패',label='',n_way=2,
        odds=[1.8,1.9],result='홈승')}})
    assert settle_live_market_results(feed,runtime)==1
    rows=runtime.records()
    assert rows[0]==prediction
    outcome=rows[-1]['outcome']
    assert outcome['result']=='not_selected'
    result=outcome['research_candidate_results']['mlb_frozen_linear_l2_10']
    assert result['paper_wallet_affected'] is False
    assert result['hypothetical_net_won'] in {8000,-10000}
    assert settle_live_market_results(feed,runtime)==0
    feed['markets']['128']['1']['result']='홈패'
    assert settle_live_market_results(feed,runtime)==1
    assert len(runtime.records())==3
    assert runtime.records()[1]['outcome']==outcome
    feed['markets']['128']['1']['result']='무승부'
    assert settle_live_market_results(feed,runtime)==1
    result=runtime.records()[-1]['outcome']['research_candidate_results']['mlb_frozen_linear_l2_10']
    assert result['result']=='void' and result['hypothetical_net_won']==0
