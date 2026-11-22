"""Offline, as-of tournament and tipster features. Never adjusts live probabilities.

Adapters must verify provenance and supply immutable snapshots. A source URL or
timestamp alone is not proof that a historical record existed before kickoff.
"""
from pregame_evidence_gate import check_evidence, _aware


def tournament_features(record, event_id, prediction_at, kickoff):
    """Explicit win targets support asymmetric series advantages; no best-of guess.

    Win targets and scores refer to the current home/away teams, not game-one
    home/away. Draw/aggregate qualification rules are deliberately not inferred.
    """
    reason = check_evidence(record, event_id, prediction_at, kickoff)
    if reason != 'eligible':
        return {'status': reason, 'features': {}}
    required = ('competition_id', 'season', 'rules_version', 'source_url', 'stage')
    if any(not record.get(k) for k in required):
        return {'status': 'missing_competition_provenance', 'features': {}}
    features = {'stage': record['stage'], 'format': record.get('format', 'unknown')}
    if record.get('format') != 'win_target_series':
        return {'status': 'unsupported_format', 'features': features}
    keys = ('home_wins', 'away_wins', 'home_wins_required', 'away_wins_required')
    if any(type(record.get(k)) is not int for k in keys):
        return {'status': 'invalid_series_state', 'features': {}}
    h, a, ht, at = (record[k] for k in keys)
    if min(h, a) < 0 or min(ht, at) < 1 or h >= ht or a >= at:
        return {'status': 'invalid_or_finished_series', 'features': {}}
    features.update(home_wins_needed=ht-h, away_wins_needed=at-a,
                    home_can_clinch=h == ht-1, away_can_clinch=a == at-1,
                    home_elimination_risk=a == at-1, away_elimination_risk=h == ht-1)
    return {'status': 'eligible', 'features': features}


def tipster_features(records, event_id, prediction_at, kickoff, *, market,
                     settlement_scope, line, selections):
    """One latest eligible opinion per canonical author, across sites.

    Equal-time conflicting selections exclude that author. Unknown identities,
    late observations and mismatched markets cannot inflate consensus. Counts
    are opinions, NOT independent evidence or calibrated win probabilities.
    """
    if not market or not settlement_scope or not selections:
        raise ValueError('Explicit market, settlement scope and selections required')
    latest = {}
    rejected = {}
    def reject(reason):
        rejected[reason] = rejected.get(reason, 0) + 1
    for r in records:
        reason = check_evidence(r, event_id, prediction_at, kickoff)
        if reason != 'eligible':
            reject(reason)
            continue
        published = _aware(r.get('published_at'))
        observed = _aware(r['observed_at'])
        if published is None or published > observed:
            reject('invalid_publication_time')
            continue
        if not all(r.get(k) for k in ('canonical_author_id', 'source_url', 'pick_id')):
            reject('missing_identity_or_provenance')
            continue
        if (r.get('market'), r.get('settlement_scope'), r.get('line')) != (market, settlement_scope, line):
            reject('market_mismatch')
            continue
        if 'line' not in r or r.get('selection') not in selections:
            reject('invalid_selection_or_line')
            continue
        author = r['canonical_author_id']
        previous = latest.get(author)
        if previous is None or published > previous[0]:
            latest[author] = (published, {r['selection']})
        elif published == previous[0]:
            previous[1].add(r['selection'])
    counts = {s: 0 for s in selections}
    for _, choices in latest.values():
        if len(choices) != 1:
            reject('conflicting_author_snapshot')
        else:
            counts[next(iter(choices))] += 1
    return {'author_count': sum(counts.values()), 'selection_counts': counts,
            'rejected': rejected, 'status': 'unvalidated_opinion_only'}
