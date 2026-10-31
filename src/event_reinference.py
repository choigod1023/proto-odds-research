"""Finite, opt-in research re-inference boundary; no scheduler, I/O or training.

A caller owns durable state, source polling and an independently trusted verifier.
This module never publishes to the operational ledger or mutates the last result.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import math


class InvalidInput(ValueError):
    pass


def _time(value):
    try:
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError
        return value
    except (AttributeError, TypeError, ValueError):
        raise InvalidInput('timezone_aware_time_required') from None


def _identifier(value):
    if not isinstance(value, str) or not value.strip():
        raise InvalidInput('identity_missing')
    return value


def _hash(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise InvalidInput('invalid_content') from None
    return hashlib.sha256(raw.encode('utf8')).hexdigest()


def polling_plan(status, kickoff_at, now):
    """Suggested next source check only; the caller enforces provider quotas.

    Frozen and live games deliberately have no inference polling recommendation.
    """
    now, kickoff = _time(now), _time(kickoff_at)
    if status in ('final', 'cancelled', 'postponed'):
        return {'phase': 'closed', 'after_seconds': None}
    if status == 'live':
        return {'phase': 'live_model_unavailable', 'after_seconds': None}
    if status != 'scheduled':
        return {'phase': 'unknown_status', 'after_seconds': None}
    remaining = kickoff - now
    if remaining <= timedelta(minutes=30):
        return {'phase': 'frozen', 'after_seconds': None}
    if remaining > timedelta(hours=24):
        return {'phase': 'early', 'after_seconds': 21600}
    if remaining > timedelta(hours=6):
        return {'phase': 'day_before', 'after_seconds': 3600}
    return {'phase': 'near_kickoff', 'after_seconds': 900}


def _prepare(job, now, quote_max_age, fact_max_age):
    """Validate scheduling constraints; the supplied verifier authenticates data."""
    identity = job['identity']
    for field in ('league', 'canonical_game_id', 'official_game_id', 'product_id', 'home_team_id', 'away_team_id'):
        _identifier(identity[field])
    if identity['home_team_id'] == identity['away_team_id']:
        raise InvalidInput('team_identity_invalid')
    kickoff = _time(identity['kickoff_at'])
    snapshot_at = _time(job['snapshot_at'])
    if snapshot_at > now:
        raise InvalidInput('snapshot_from_future')
    status = job['status']
    if status == 'live':
        raise InvalidInput('live_model_unavailable')
    if status != 'scheduled':
        raise InvalidInput('game_not_scheduled')
    if now > kickoff - timedelta(minutes=30):
        raise InvalidInput('pregame_frozen')
    quote = job['quote']
    if quote['product_id'] != identity['product_id']:
        raise InvalidInput('quote_product_mismatch')
    if quote['status'] != 'open':
        raise InvalidInput('market_not_open')
    if quote['market'] != 'win' or quote['period'] != 'full' or quote['line'] is not None:
        raise InvalidInput('unsupported_product')
    outcomes = quote['outcomes']
    if outcomes not in (['home', 'away'], ['home', 'draw', 'away']):
        raise InvalidInput('unsupported_outcomes')
    odds = quote['odds']
    if not isinstance(odds, dict) or set(odds) != set(outcomes) or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 1 for v in odds.values()):
        raise InvalidInput('invalid_quote_vector')
    observed, received = _time(quote['observed_at']), _time(quote['received_at'])
    if not observed <= received <= snapshot_at:
        raise InvalidInput('quote_unavailable')
    if now - observed > quote_max_age:
        raise InvalidInput('quote_stale')
    facts = job['facts']
    if not isinstance(facts, list):
        raise InvalidInput('facts_invalid')
    semantic_facts = []
    fact_groups = {}
    for fact in facts:
        if fact['kind'] not in ('lineup', 'injury', 'substitution'):
            raise InvalidInput('fact_kind_unsupported')
        if fact['canonical_game_id'] != identity['canonical_game_id']:
            raise InvalidInput('fact_game_mismatch')
        fact_observed, fact_received = _time(fact['observed_at']), _time(fact['received_at'])
        if not fact_observed <= fact_received <= snapshot_at:
            raise InvalidInput('fact_unavailable')
        if now - fact_observed > fact_max_age:
            raise InvalidInput('fact_stale')
        # Ignore transport timestamps only; the verifier must authenticate facts.
        item = {k: fact[k] for k in ('kind', 'canonical_game_id', 'content')}
        semantic_facts.append(item)
        fact_groups.setdefault(fact['kind'], []).append((fact_observed, item))
    semantic_facts.sort(key=_hash)
    if len({_hash(x) for x in semantic_facts}) != len(semantic_facts):
        raise InvalidInput('duplicate_fact')
    revision = _hash({'identity': identity, 'quote': {k: quote[k] for k in ('product_id', 'market', 'period', 'line', 'outcomes', 'odds')},
                      'facts': semantic_facts, 'model_revision': _identifier(job['model_revision'])})
    watermarks = {kind: {'observed_at': max(t for t, _ in rows).isoformat(),
                         'content': _hash(sorted((_hash(item) for _, item in rows)))}
                  for kind, rows in fact_groups.items()}
    quote_watermark = {'observed_at': observed.isoformat(), 'content': _hash(odds)}
    return identity['canonical_game_id'], revision, outcomes, snapshot_at, quote_watermark, watermarks


def run_batch(jobs, *, now, quote_max_age_seconds, fact_max_age_seconds, max_inferences, max_jobs, last_success, last_seen, verify, infer):
    """Evaluate a finite list; return results plus a new last-success map.

    verify(job, now) must authenticate identity, evidence and exact model state;
    infer(job, now) must enforce the actual model contract. Both are caller-owned.
    Persist outputs, last_success and last_seen atomically outside this call.
    """
    now = _time(now)
    if type(quote_max_age_seconds) is not int or quote_max_age_seconds <= 0:
        raise InvalidInput('quote_age_policy_required')
    if type(fact_max_age_seconds) is not int or fact_max_age_seconds <= 0:
        raise InvalidInput('fact_age_policy_required')
    if type(max_inferences) is not int or max_inferences < 0:
        raise InvalidInput('quota_required')
    if not isinstance(jobs, list) or not isinstance(last_success, dict) or not isinstance(last_seen, dict) or not callable(verify) or not callable(infer):
        raise InvalidInput('invalid_batch')
    if type(max_jobs) is not int or max_jobs <= 0 or len(jobs) > max_jobs:
        raise InvalidInput('batch_limit_exceeded')
    success = deepcopy(last_success)
    seen = deepcopy(last_seen)
    outputs = []
    attempted = 0
    for original in jobs:
        job = deepcopy(original)
        game_id = None
        try:
            if not isinstance(job, dict) or not isinstance(job.get('identity'), dict):
                raise InvalidInput('identity_missing')
            game_id = _identifier(job['identity']['canonical_game_id'])
            product_id = _identifier(job['identity']['product_id'])
            snapshot_at = _time(job['snapshot_at'])
            if snapshot_at > now:
                raise InvalidInput('snapshot_from_future')
            # A trusted snapshot, including a suspension or failed inference,
            # advances a separate watermark so older open prices cannot return.
            if verify(deepcopy(job), now) is not True:
                raise InvalidInput('evidence_unverified')
            key = _hash([game_id, product_id])
            content = _hash(job)
            prior_seen = seen.get(key)
            if prior_seen is not None:
                prior_at = _time(prior_seen['snapshot_at'])
                if snapshot_at < prior_at or (snapshot_at == prior_at and prior_seen['content'] != content):
                    raise InvalidInput('out_of_order_snapshot')
            seen[key] = {'snapshot_at': snapshot_at.isoformat(), 'content': content}
            game_id, revision, outcomes, snapshot_at, quote_mark, fact_marks = _prepare(
                job, now, timedelta(seconds=quote_max_age_seconds), timedelta(seconds=fact_max_age_seconds))
            previous = success.get(key)
            if previous is not None:
                previous_at = _time(previous['snapshot_at'])
                if snapshot_at < previous_at or (snapshot_at == previous_at and previous['revision'] != revision):
                    raise InvalidInput('out_of_order_snapshot')
                old_quote = previous['quote_watermark']
                if _time(quote_mark['observed_at']) < _time(old_quote['observed_at']) or (_time(quote_mark['observed_at']) == _time(old_quote['observed_at']) and quote_mark['content'] != old_quote['content']):
                    raise InvalidInput('out_of_order_quote')
                for kind, old in previous['fact_watermarks'].items():
                    new = fact_marks.get(kind)
                    if new is None or _time(new['observed_at']) < _time(old['observed_at']) or (_time(new['observed_at']) == _time(old['observed_at']) and new['content'] != old['content']):
                        raise InvalidInput('out_of_order_fact')
            if previous is not None and previous['revision'] == revision:
                success[key] = {'revision': revision, 'snapshot_at': snapshot_at.isoformat(), 'quote_watermark': quote_mark, 'fact_watermarks': fact_marks}
                outputs.append({'game_id': game_id, 'status': 'unchanged', 'revision': revision})
                continue
            if attempted >= max_inferences:
                outputs.append({'game_id': game_id, 'status': 'deferred', 'reason': 'inference_quota'})
                continue
            attempted += 1
            result = infer(deepcopy(job), now)
            probs = result['probability_vector']
            if not isinstance(probs, list) or len(probs) != len(outcomes) or any(type(p) not in (int, float) or not math.isfinite(p) or p < 0 or p > 1 for p in probs) or not math.isclose(sum(probs), 1, abs_tol=1e-9, rel_tol=0):
                raise InvalidInput('invalid_model_output')
            success[key] = {'revision': revision, 'snapshot_at': snapshot_at.isoformat(), 'quote_watermark': quote_mark, 'fact_watermarks': fact_marks}
            outputs.append({'game_id': game_id, 'status': 'inferred', 'revision': revision, 'result': deepcopy(result)})
        except (InvalidInput, KeyError, TypeError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, InvalidInput) else 'invalid_input_or_model_failure'
            outputs.append({'game_id': game_id, 'status': 'withheld', 'reason': reason})
        except Exception:
            outputs.append({'game_id': game_id, 'status': 'withheld', 'reason': 'worker_failed'})
    return {'outcomes': outputs, 'last_success': success, 'last_seen': seen,
            'metrics': {'requested': len(jobs), 'attempted': attempted,
                        'inferred': sum(x['status'] == 'inferred' for x in outputs),
                        'unchanged': sum(x['status'] == 'unchanged' for x in outputs),
                        'withheld': sum(x['status'] == 'withheld' for x in outputs),
                        'deferred': sum(x['status'] == 'deferred' for x in outputs)}}
