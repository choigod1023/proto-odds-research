"""Same-snapshot frozen baseball research inference; never operating picks.

The optional artifacts are loaded only for an eligible league/source. No DB,
HTTP, current clock, training, threshold search or wallet operation occurs here.
"""
from datetime import datetime, timedelta, timezone
import json
import re

NAMES=('mlb_frozen_linear_l2_10','kbo_frozen_e0_residual')
KST=timezone(timedelta(hours=9))


def _time(v):
    d=datetime.fromisoformat(str(v).replace('Z','+00:00'))
    if d.utcoffset() is None:raise ValueError('timezone_required')
    return d.astimezone(timezone.utc)


def _kickoff(fixture):
    m=re.search(r'(\d{1,2})\.(\d{1,2}).*?(\d{1,2}):(\d{2})',str(fixture.get('date','')))
    if not m:raise ValueError('missing_fixture_kickoff')
    month,day,hour,minute=map(int,m.groups())
    year=int(fixture['year'])-int(int(fixture.get('round') or 0)==1 and month==12)
    return datetime(year,month,day,hour,minute,tzinfo=KST).astimezone(timezone.utc)


def capture(snapshot, features):
    base=dict(role='frozen_research_candidate',affects_selection=False,affects_probability=False,
              promoted=False,validated_edge=False)
    arms={n:dict(base,status='unavailable',reason='not_applicable_league') for n in NAMES}
    fixture=features.get('fixture') or {}
    league=fixture.get('league')
    if league not in {'MLB','KBO'}:return arms
    name=NAMES[0] if league=='MLB' else NAMES[1]
    arm=arms[name]
    try:
        at=_time(snapshot['as_of']); start=_kickoff(fixture)
        if at>=start-timedelta(minutes=30):raise ValueError('not_before_t30')
        rows=[o for o in features.get('options',[]) if o.get('market')=='승패'
              and o.get('label') in {'',None} and o.get('line') is None]
        if len(rows)!=2 or {o.get('selection') for o in rows}!={'홈','원정'}:
            raise ValueError('complete_two_way_market_required')
        offers={('home' if o['selection']=='홈' else 'away'):o for o in rows}
        market_at=snapshot.get('market_observed_at') or features.get('candidate_market_observed_at')
        if not market_at or _time(market_at)>at:raise ValueError('missing_or_future_market_observation')
        if league=='MLB':
            source=(features.get('research_player_inputs') or {}).get('candidate_input')
            if not isinstance(source,dict):raise ValueError('missing_announced_lineup_feed')
            if at-_time(source.get('observed_at'))>timedelta(minutes=45):
                raise ValueError('stale_announced_lineup_feed')
            from mlb_frozen_candidate import infer_mlb_candidate_local
            result=infer_mlb_candidate_local(source,decision_at=at.isoformat(),
                matchup=dict(league='MLB',gamePk=source.get('gamePk'),home=fixture['home'],away=fixture['away'],start_at=start.isoformat()),
                odds=dict(home=offers['home']['odds'],away=offers['away']['odds'],observed_at=market_at,
                          source='saved_proto_market',snapshot_id=snapshot['decision_id']))
            if result['status']!='available':
                arm.update(reason=';'.join(result.get('reasons') or ['candidate_unavailable']))
                return arms
            side=result['selection']; picked=offers[side]; ph=result['probability_home']
            arm.update(model_artifact_hash=result['model_artifact_hash'],prior_artifact_hash=result['prior_artifact_hash'],
                       input_hash=result['input_hash'],feature_hash=result['feature_hash'],features=result['features'],
                       training_cutoff_at=result['training_cutoff_at'],model_stage=result['model_stage'],
                       source_observed_at=source.get('observed_at'),source_response_sha256=source.get('response_sha256'),
                       source_game_id=source.get('gamePk'),starter_identity_kind=result['starter_identity_kind'],
                       starter_final_confirmed=False,probability_basis='frozen_lineup_linear_l2_10_unvalidated')
        else:
            state=features.get('research_kbo_daily_state')
            if not isinstance(state,dict):raise ValueError('missing_verified_daily_strength_state')
            if at-_time(state.get('available_at'))>timedelta(hours=6):
                raise ValueError('stale_daily_strength_observation')
            from kbo_frozen_candidate import state_for_fixture,infer_candidate
            f=dict(league='KBO',date=start.astimezone(KST).date().isoformat(),home=fixture['home'],away=fixture['away'],kickoff=start.isoformat())
            bound=state_for_fixture(daily_state=state,fixture=f,decision_at=at.isoformat())
            hybrid=features.get('tipster_hybrid') or {}
            fixed=((features.get('model_comparison') or {}).get('arms') or {}).get('fixed91_same_electorate') or {}
            if fixed.get('status')!='available':raise ValueError('missing_eligible_same_electorate_reference')
            ref=dict(recipe='fixed91_same_electorate',voters=hybrid.get('authors'),
                     home_vote_share=(hybrid.get('vote_shares') or {}).get('홈'),
                     observed_at=hybrid.get('latest_observed_at'),snapshot_id=snapshot['decision_id'])
            rr=[dict(side=s,odds=o['odds'],market_probability=o.get('market_probability'),market='승패',label='',line=None,
                     snapshot_id=snapshot['decision_id'],selection_id=o['selection_id'],offer_id=o['offer_id'],observed_at=market_at)
                for s,o in offers.items()]
            result=infer_candidate(fixture=f,decision_at=at.isoformat(),state=bound,reference=ref,offers=rr)
            if result['status']!='available':
                arm.update(reason=result.get('reason') or 'candidate_unavailable')
                return arms
            side=result['side'];picked=offers[side];ph=result['home_probability']
            arm.update(model_artifact_hash=result['artifact_hash'],input_hash=result['input_hash'],
                       state_hash=result['state_hash'],reference_home_probability=result['reference_home_probability'],
                       source_available_at=state.get('available_at'),
                       seed_historical_availability=result.get('seed_historical_availability'),
                       probability_basis=result['probability_basis'])
        arm.update(status='available',reason=None,selection_id=picked['selection_id'],offer_id=picked['offer_id'],
                   market='승패',selection=picked['selection'],odds=picked['odds'],
                   home_probability=ph,probability=ph if side=='home' else 1-ph)
        if len(json.dumps(arms,ensure_ascii=False,allow_nan=False).encode())>12288:
            raise ValueError('candidate_payload_size_limit')
    except Exception as exc:
        reason=str(exc) if isinstance(exc,ValueError) and str(exc).isidentifier() else type(exc).__name__
        arms[name]=dict(base,status='unavailable',reason=reason)
    return arms
