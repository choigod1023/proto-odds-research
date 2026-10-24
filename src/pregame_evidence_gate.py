"""Offline gate: never substitute game date for information observation time."""
from datetime import datetime


def _aware(value):
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except (ValueError,TypeError):
        return None
    return dt if dt.utcoffset() is not None else None


def check_evidence(record,event_id,prediction_at,kickoff):
    """Return explicit eligibility; payload completeness is adapter-specific.

    `observed_at` must be a source-verified observation, not inferred from a
    historical game's date or from the time a later backfill was generated.
    """
    observed=_aware(record.get('observed_at'));prediction=_aware(prediction_at);start=_aware(kickoff)
    if not event_id or record.get('event_id')!=event_id:return 'event_mismatch'
    if observed is None:return 'missing_or_naive_observation_time'
    if prediction is None or start is None:return 'missing_or_naive_event_time'
    if prediction>=start:return 'prediction_not_pregame'
    if observed>prediction:return 'observed_after_prediction'
    return 'eligible'
