"""Plan and append exact historical results; never fetch results or select a cohort.

Rows use live-market field names plus explicit year and round.  The caller owns
source verification and cohort selection; source metadata is preserved verbatim.
"""
from __future__ import annotations

import hashlib
import json

from src.live_market_refresh import (KST, LINE, SEL_NAMES, UNPLAYED, _aware_timestamp,
                                     _game_kickoff, event_id, offer_id, selection_id,
                                     winner_index)
from src.prediction_runtime import _latest_predictions


def plan_backfill(records, rows, *, snapshot_ids, observed_at, source):
    """Return reviewable entries and holds. No I/O or ledger mutation occurs."""
    observed = _aware_timestamp(observed_at)
    if observed is None or not isinstance(source, dict) or not source.get('name'):
        raise ValueError('timezone-aware observed_at and named source required')
    selected = set(snapshot_ids)
    latest = _latest_predictions(records)
    settled = {r['snapshot_id'] for r in records if r.get('record_type') == 'settlement'}
    entries, holds = [], []
    for event, pred in latest.items():
        sid = pred['snapshot_id']
        if sid not in selected:
            continue
        if sid in settled:
            holds.append({'snapshot_id': sid, 'reason': 'existing_settlement'})
            continue
        p = pred.get('predictions') or {}
        if p.get('action') != 'market_reference':
            holds.append({'snapshot_id': sid, 'reason': 'latest_not_selected'})
            continue
        fixture = (pred.get('features') or {}).get('fixture') or {}
        options = (pred.get('features') or {}).get('options') or []
        matches = []
        for row in rows:
            try:
                year, rnd, nway = int(row['year']), int(row['round']), int(row['n_way'])
                if isinstance(row['n_way'], bool) or nway != row['n_way']:
                    continue
                if any(row.get(k) != fixture.get(k) for k in ('sport', 'league', 'home', 'away', 'date')):
                    continue
                if year != int(fixture['year']) or rnd != int(fixture['round']):
                    continue
                if not row.get('game_no') or row.get('result') in UNPLAYED:
                    continue
                game = {**row, 'year': year, 'round': rnd}
                if event_id(game) != event:
                    continue
                kickoff = _game_kickoff(game, observed)
                if kickoff is None or kickoff.replace(tzinfo=KST) > observed:
                    continue
                names = SEL_NAMES.get((row.get('market'), nway))
                if not names:
                    continue
                line = None
                if row.get('market') in {'핸디캡', '언더오버', '전반핸디캡', '전반언더오버'}:
                    m = LINE.search(str(row.get('label') or ''))
                    if not m:
                        continue
                    line = float(m.group())
                generated = []
                for name in names:
                    option = {'market': row.get('market'), 'label': row.get('label') or '',
                              'line': line, '선택': name, '게임번호': str(row['game_no'])}
                    generated.append((selection_id(game, option), offer_id(game, option)))
                # The frozen option vector must prove the complete n-way offer.
                frozen = set()
                for o in options:
                    option = {'market': o.get('market'), 'label': o.get('label') or '',
                              'line': o.get('line'), '선택': o.get('selection'),
                              '게임번호': str(row['game_no'])}
                    identity = (selection_id(game, option), offer_id(game, option))
                    if (o.get('market') == row.get('market') and
                            (o.get('label') or '') == (row.get('label') or '') and
                            o.get('line') == line and
                            identity == (o.get('selection_id'), o.get('offer_id'))):
                        frozen.add(identity)
                if set(generated) != frozen or (p.get('selection_id'), p.get('offer_id')) not in generated:
                    continue
                void = row.get('result') in {'취소', '연기', '중단', '무효'} or (row.get('market') == '승패' and nway == 2 and row.get('result') == '무승부')
                winner = winner_index(nway, row.get('result'))
                if not void and (winner is None or winner >= nway):
                    continue
                index = generated.index((p['selection_id'], p['offer_id']))
                result = 'void' if void else 'hit' if index == winner else 'miss'
                matches.append((row, result))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        if len(matches) != 1:
            holds.append({'snapshot_id': sid, 'reason': 'ambiguous_or_unmatched', 'matches': len(matches)})
            continue
        row, result = matches[0]
        outcome = {'result': result, 'selection_id': p['selection_id'], 'official_result': row['result']}
        version = 'official-' + hashlib.sha256(json.dumps(outcome, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:16]
        entries.append({'event_id': event, 'snapshot_id': sid, 'outcome': outcome,
                        'settled_at': observed.isoformat(), 'settlement_version': version,
                        'source': {**source, 'year': int(row['year']), 'round': int(row['round']),
                                   'game_no': str(row['game_no']), 'game_date': row['date']}})
    found = {p['snapshot_id'] for p in latest.values()}
    holds.extend({'snapshot_id': sid, 'reason': 'not_latest_or_missing'} for sid in sorted(selected - found))
    return {'entries': entries, 'holds': holds}


def apply_backfill(ledger, plan, rows, *, observed_at, source):
    """Revalidate the entire approved plan under the ledger lock, then append.

    All ledger writers must use the same ledger lock/path. Reject any changed
    entry or newly settled snapshot; callers must create and review a new plan.
    """
    entries = plan['entries']
    with ledger.verified_session():
        records = ledger.records()
        checked = plan_backfill(records, rows, snapshot_ids=[e['snapshot_id'] for e in entries],
                                observed_at=observed_at, source=source)
        if checked['holds'] or checked['entries'] != entries:
            raise ValueError('backfill plan stale or changed; nothing appended')
        return ledger.append_settlements([{k: v for k, v in entry.items() if k != 'event_id'}
                                          for entry in entries])
