"""Local, outcome-blind replay followed by strict official-result linkage.

Controls are saved-input diagnostics, not replay of missing trained artifacts.
This module never changes recommendations, the paper wallet, or storage.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math

from operating_ledger_inputs import latest_pre_cutoff

VERSION = 'operating-comparison-v1'
KST = timezone(timedelta(hours=9))
ABSTENTION_REASONS = {
    'incomplete_same_market_roster', 'missing_or_invalid_selected_odds',
    'missing_selected_market_or_side', 'missing_selected_selection_or_offer',
    'no_operating_selection', 'selection_offer_mismatch', 'vote_policy_not_applied',
}
ARM_NAMES = ('operating', 'market_reference', 'fixed91_same_electorate',
             'saved_score_reference', 'home_always',
             'mlb_frozen_linear_l2_10','kbo_frozen_e0_residual')


def _time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc) if dt.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None


def _number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _full_roster(obs):
    offers = [o for o in obs['all_offers'] if o.get('market') in {'승패', '승무패'}
              and o.get('label') in {None, ''} and o.get('line') is None]
    if not offers or len({o['market'] for o in offers}) != 1:
        return None
    sides = {'홈', '원정'} if offers[0]['market'] == '승패' else {'홈', '무', '원정'}
    if len(offers) != len(sides) or {o.get('selection') for o in offers} != sides:
        return None
    if any(not o.get('selection_id') or not o.get('offer_id') or
           not _number(o.get('odds')) or o['odds'] <= 1 for o in offers):
        return None
    return sorted(offers, key=lambda o: ('홈', '무', '원정').index(o['selection']))


def replay_arms(obs):
    """Use the same frozen control engine as future immutable captures.

    Old rows explicitly replay saved inputs; future rows must match their saved
    prospective arms. A changed recipe cannot silently rewrite a future trial.
    """
    from operating_model_capture import capture
    offers = _full_roster(obs)
    if offers is None:
        return None, 'invalid_or_ambiguous_full_game_roster'
    abstained = obs['operating']['action'] == 'withhold'
    reasons = set(obs['exclusion_reasons'])
    if abstained:
        reasons -= ABSTENTION_REASONS
    if reasons:
        return None, ';'.join(sorted(reasons))
    op = obs['operating']
    snapshot = dict(decision_id=obs['snapshot_id'],event_id=obs['event_id'],
                    as_of=obs['as_of'],input_revision_hash=obs['input_revision_hash'],
                    action=op['action'],selection_id=op['selection_id'],offer_id=op['offer_id'],
                    model=obs['model_metadata'],probability={'final':op['probability']})
    controls = capture(snapshot,obs['source_features'],capture_kind='retrospective_same_snapshot_control')
    if controls['status'] != 'captured':
        return None, controls['reason']
    saved = obs['source_features'].get('model_comparison')
    if saved is not None:
        if (not isinstance(saved, dict) or saved.get('capture_kind')!='prospective_same_snapshot'
                or saved.get('implementation_hash')!=controls['implementation_hash']
                or saved.get('record_version')!=controls['record_version']
                or saved.get('status')!='captured'
                or saved.get('price_roster_hash')!=controls['price_roster_hash']
                or _time(saved.get('prediction_as_of'))!=_time(obs['as_of'])
                or saved.get('arms')!=controls['arms']):
            return None, 'saved_prospective_control_contract_mismatch'
        built = _time(saved.get('source_generated_at')) if saved.get('source_generated_at') else None
        if built is not None and built > _time(obs['captured_at']):
            return None, 'saved_control_built_after_capture'
        controls = saved
    arms = {}
    for name, arm in controls['arms'].items():
        arms[name] = dict(arm)
        arms[name]['status'] = {'available':'selected','abstain':'abstained'}.get(arm['status'],arm['status'])
        if name=='operating':
            arms[name]['probability_kind']=op['probability_kind']
    # Probability is needed for calibration, not for historical ROI of a
    # verifiably saved selection/price. Never substitute a fabricated value.
    if (not abstained and op['probability'] is None and
            arms['operating'].get('reason')=='missing_or_invalid_operating_probability'):
        matches = [o for o in offers if o['selection_id']==op['selection_id']
                   and o['offer_id']==op['offer_id']]
        if len(matches)==1:
            arms['operating'].update(status='selected',probability=None,**{
                k:matches[0].get(k) for k in ('selection_id','offer_id','market','selection','odds')})
    if not abstained and arms['operating']['status']!='selected':
        return None, 'operating_not_same_full_game_market_or_missing_probability'
    # Learned candidates are evaluated from their immutable recorded forecast,
    # never silently recomputed after a coefficient/feature-engine change.
    learned=(saved or {}).get('learned_candidates') or {}
    for name in ARM_NAMES[5:]:
        arm=learned.get(name)
        arms[name]={'status':'unavailable','reason':'no_saved_prospective_candidate'}
        if not isinstance(arm,dict) or arm.get('status')!='available':
            if isinstance(arm,dict):arms[name]['reason']=arm.get('reason')
            continue
        matches=[o for o in offers if o['selection_id']==arm.get('selection_id') and o['offer_id']==arm.get('offer_id')]
        if (len(matches)!=1 or any(arm.get(k)!=matches[0].get(k) for k in ('odds','selection','market'))
                or not _number(arm.get('probability')) or not 0<=arm['probability']<=1
                or not isinstance(arm.get('model_artifact_hash'),str) or len(arm['model_artifact_hash'])!=64
                or arm.get('promoted') is not False or arm.get('affects_selection') is not False):
            arms[name]['reason']='saved_candidate_identity_or_price_mismatch'
            continue
        try:
            if name=='mlb_frozen_linear_l2_10':
                from mlb_frozen_candidate import load_mlb_artifacts
                expected_artifact=load_mlb_artifacts(_time(obs['kickoff']).year)[0]['sha256']
            else:
                from kbo_frozen_candidate import MODEL_HASH
                expected_artifact=MODEL_HASH
            if arm['model_artifact_hash']!=expected_artifact:
                raise ValueError('unknown_frozen_artifact')
        except (ValueError,KeyError,TypeError,OSError):
            arms[name]['reason']='unsupported_saved_candidate_artifact'
            continue
        arms[name]={**arm,'status':'selected','probability_kind':'unvalidated_frozen_model_probability'}
    return arms, None


def _official_index(rows, evaluated_at):
    """Resolve every source-key correction BEFORE filtering confirmed scores.

    match_results kickoff is documented naive KST; observed_at must be aware.
    A later unconfirmed row invalidates the older confirmed score. Conflicting
    equal-time corrections fail closed instead of depending on input order.
    """
    latest, conflicts = {}, set()
    for row in rows:
        if row.get('source') != 'proto':
            continue
        observed = _time(row.get('observed_at'))
        if observed is None or observed > evaluated_at:
            continue
        key = tuple(row.get(k) for k in ('source', 'season', 'round', 'game_no'))
        if key not in latest or observed > latest[key][0]:
            latest[key] = observed, row
            conflicts.discard(key)
        elif observed == latest[key][0] and row != latest[key][1]:
            conflicts.add(key)
    index = defaultdict(list)
    for key, (observed, row) in latest.items():
        if key in conflicts or row.get('result_state') != 'confirmed':
            continue
        try:
            kickoff = datetime.fromisoformat(row['kickoff'])
            if kickoff.tzinfo is not None:
                continue  # unexpected source schema; no guessed conversion
            kickoff = kickoff.replace(tzinfo=KST).astimezone(timezone.utc)
            scores = row['home_score'], row['away_score']
            if observed < kickoff or any(type(s) is not int or s < 0 for s in scores):
                continue
            physical = (kickoff.isoformat(), row['sport'], row['league'],
                        row['home_team'], row['away_team'])
            index[physical].append(row)
        except (ValueError, KeyError, TypeError):
            continue
    return index


def _physical(obs):
    f = obs['fixture']
    return tuple([obs['kickoff']] + [f.get(k) for k in ('sport', 'league', 'home', 'away')])


def summarize(events, name, stake):
    selected = [e for e in events if e['arms'][name]['status'] == 'selected']
    settled = [e for e in selected if e['label_status'] == 'settled']
    daily, batches = defaultdict(int), defaultdict(int)
    hits = misses = voids = net = 0
    probability_errors=[]
    log_losses=[]
    for e in settled:
        arm = e['arms'][name]
        winner = e['official']['winner']
        if winner == '무' and arm['market'] == '승패':
            voids += 1
            profit = 0
        elif arm['selection'] == winner:
            hits += 1
            profit = int((Decimal(str(arm['odds'])) * stake).quantize(Decimal(1), rounding=ROUND_HALF_UP)) - stake
        else:
            misses += 1
            profit = -stake
        net += profit
        probability=arm.get('probability')
        if not (winner=='무' and arm['market']=='승패') and _number(probability) and 0<=probability<=1:
            target=float(arm['selection']==winner)
            probability_errors.append((probability-target)**2)
            clipped=max(1e-15,min(1-1e-15,probability))
            log_losses.append(-target*math.log(clipped)-(1-target)*math.log1p(-clipped))
        day = _time(e['kickoff']).astimezone(KST).date().isoformat()
        daily[day] += profit
        batches[e['official']['observed_at']] += profit
    equity = peak = drawdown = 0
    for at in sorted(batches, key=_time):
        equity += batches[at]
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    turnover = stake * len(settled)  # void stakes included, explicitly disclosed
    return dict(opportunities=len(events), selections=len(selected), settled=len(settled),
                pending=len(selected)-len(settled), abstained=sum(e['arms'][name]['status']=='abstained' for e in events),
                unavailable=sum(e['arms'][name]['status']=='unavailable' for e in events),
                hits=hits, misses=misses, voids=voids,
                hit_rate=hits/(hits+misses) if hits+misses else None,
                net_won=net, settled_turnover_won=turnover,
                roi=net/turnover if turnover else None,
                result_batch_close_drawdown_won=drawdown, kickoff_day_net_won=dict(sorted(daily.items())),
                probability_observations=len(probability_errors),
                selected_event_brier=sum(probability_errors)/len(probability_errors) if probability_errors else None,
                selected_event_logloss=sum(log_losses)/len(log_losses) if log_losses else None,
                loss_days=sum(p<0 for p in daily.values()), days=len(daily),
                selection_rate=len(selected)/len(events) if events else None)


def compare(records, official_rows, *, evaluated_at, stake=10000, cutoff_minutes=30):
    """Return diagnostics only; no data-dependent promotion or fitted tuning."""
    at = _time(evaluated_at)
    if at is None or type(stake) is not int or stake <= 0:
        raise ValueError('aware evaluated_at and positive integer stake required')
    records = list(records)
    frozen = latest_pre_cutoff([r for r in records if _time(r.get('captured_at'))
                               and _time(r['captured_at']) <= at], cutoff_minutes)
    index = _official_index(official_rows, at)
    groups, excluded = defaultdict(list), []
    for obs in frozen['selected']:
        if any(not obs['fixture'].get(k) for k in ('sport','league','home','away')):
            excluded.append(dict(event_id=obs['event_id'],reason='missing_physical_fixture_identity'))
            continue
        groups[_physical(obs)].append(obs)
    events = []
    for physical, observations in sorted(groups.items(), key=lambda kv: str(kv[0])):
        # Bookmaker reissues do not create independent games. Fail closed when
        # multiple ledger event IDs represent the same physical fixture.
        if len(observations) != 1:
            excluded.extend(dict(event_id=o['event_id'], reason='duplicate_physical_fixture') for o in observations)
            continue
        o = observations[0]
        arms, reason = replay_arms(o)
        if reason:
            excluded.append(dict(event_id=o['event_id'], reason=reason,
                                 policy=o['model_identity']['feature_policy_version'] or o['model_identity']['model_version']))
            continue
        # All choices are now frozen. Outcome lookup starts here.
        matches = [r for r in index.get(physical, [])
                   if r['season'] == o['fixture']['year'] and r['round'] == o['fixture']['round']]
        label_status, official = 'missing_official_result', None
        if matches:
            scores = {(r['home_score'], r['away_score']) for r in matches}
            if len(scores) != 1:
                label_status = 'conflicting_official_results'
            else:
                hs, aws = next(iter(scores))
                newest = max(matches, key=lambda r: _time(r['observed_at']))
                official = dict(home_score=hs, away_score=aws,
                                winner='홈' if hs>aws else '원정' if hs<aws else '무',
                                observed_at=_time(newest['observed_at']).isoformat(),
                                source='proto', game_numbers=sorted({r['game_no'] for r in matches}))
                label_status = 'settled'
        policy = o['model_identity']['feature_policy_version'] or o['model_identity']['model_version']
        events.append(dict(event_id=o['event_id'],snapshot_id=o['snapshot_id'],
                           kickoff=o['kickoff'],as_of=o['as_of'],captured_at=o['captured_at'],
                           fixture=o['fixture'],policy=policy,model_identity=o['model_identity'],
                           record_hash=o['record_hash'],input_hash=_digest(o['source_features']),
                           arms=arms, label_status=label_status,official=official,
                           capture_kind=('prospective_same_snapshot' if o['source_features'].get('model_comparison')
                                         else 'retrospective_saved_input_replay')))
    policies = {}
    for policy in sorted({e['policy'] for e in events}):
        group = [e for e in events if e['policy'] == policy]
        paired = [e for e in group if all(e['arms'][n]['status']=='selected'
                  for n in ('operating','market_reference','fixed91_same_electorate'))]
        policies[policy] = dict(
            opportunities=len(group), settled_opportunities=sum(e['label_status']=='settled' for e in group),
            all_opportunities={n:summarize(group,n,stake) for n in ARM_NAMES},
            selected_three_arm_intersection={n:summarize(paired,n,stake) for n in ARM_NAMES[:3]},
            learned_candidate_intersections={name:{n:summarize(
                [e for e in group if e['arms'][name]['status']=='selected'
                 and e['arms']['operating']['status']=='selected'],n,stake)
                for n in ('operating','market_reference','fixed91_same_electorate',name)} for name in ARM_NAMES[5:]},
            league={league:{n:summarize([e for e in group if e['fixture']['league']==league],n,stake)
                            for n in ARM_NAMES} for league in sorted({e['fixture']['league'] for e in group})},
            operating_market_pick_differences=sum(e['arms']['operating']['status']=='selected' and
                e['arms']['operating']['selection']!=e['arms']['market_reference']['selection'] for e in group))
    operating_versions = {}
    for version in sorted({e['model_identity']['model_version'] for e in events}):
        group = [e for e in events if e['model_identity']['model_version']==version
                 and e['arms']['operating']['status']=='selected']
        operating_versions[version] = dict(
            summary=summarize(group,'operating',stake),
            policy_context_counts=dict(Counter(e['policy'] for e in group)),
            note='Actual selected operating_version only; feature-policy fallback cohorts are separate.')
    return dict(version=VERSION,evaluated_at=at.isoformat(),stake_won=stake,monthly_cap_applied=False,
                hash_chain_verified=False, latest_events=len(frozen['selected']),
                limitations=['No automatic promotion; tiny recent policy samples cannot prove stability.',
                             'Historical replay is not prospective candidate capture or actual wallet profit.',
                             'Official source is the stored proto feed, not independent result authentication.',
                             'No guarantee stored quotes were executable at real stake size.',
                             'Drawdown uses result-availability batch closes; intrabatch losses are unobserved.',
                             '9:1 control uses the saved current electorate; not exact old deployed model replay.',
                             'No MLB/KBO research trained model is replayed without its causal feature engine.'],
                policies=policies,operating_versions=operating_versions,events=events,excluded=excluded,
                exclusion_counts=dict(Counter(e['reason'] for e in excluded)),
                input_rejections=len(frozen['rejected']))
