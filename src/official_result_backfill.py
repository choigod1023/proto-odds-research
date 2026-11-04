"""Plan and append exact historical results; never fetch results or select a cohort.

Rows use live-market field names plus explicit year and round.  The caller owns
source verification and cohort selection; source metadata is preserved verbatim.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

if __package__:
    from src.live_market_refresh import (KST, LINE, SEL_NAMES, UNPLAYED, _aware_timestamp,
                                         _game_kickoff, event_id, offer_id, selection_id,
                                         winner_index)
    from src.prediction_runtime import _latest_predictions
    from src.betman_result_dom import _row as _normalize_official_row
else:
    from live_market_refresh import (KST, LINE, SEL_NAMES, UNPLAYED, _aware_timestamp,
                                     _game_kickoff, event_id, offer_id, selection_id,
                                     winner_index)
    from prediction_runtime import _latest_predictions
    from betman_result_dom import _row as _normalize_official_row


def _official_source(row, year, rnd, observed, source):
    evidence = row.get('official_source')
    required = ('raw_result_text' in row or 'raw_market_scope' in row or
                source.get('provenance') == 'captured_betman_website_dom')
    if evidence is None and not required:
        return None
    if not isinstance(evidence, dict):
        raise ValueError('missing official source')
    url = urlsplit(evidence.get('url', ''))
    query = parse_qs(url.query, keep_blank_values=True)
    sha = evidence.get('evidence_sha256')
    received = _aware_timestamp(evidence.get('received_at'))
    kickoff = _game_kickoff({**row, 'year': year, 'round': rnd}, observed)
    if (url.scheme != 'https' or url.hostname != 'www.betman.co.kr' or
            url.path != '/main/mainPage/gamebuy/closedGameSlipIFR.do' or
            query.get('gmId') != ['G101'] or
            query.get('gmTs') != [f'{year % 100:02d}{rnd:04d}'] or
            not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha) or
            received is None or received > observed or
            evidence.get('result_text') != row.get('raw_result_text') or
            evidence.get('market_text') != row.get('raw_market_scope') or
            evidence.get('label') != row.get('raw_label') or
            not isinstance(evidence.get('row_index'), int) or isinstance(evidence.get('row_index'), bool) or
            evidence['row_index'] < 0 or evidence['row_index'] != row.get('dom_row_index') or
            not isinstance(evidence.get('kickoff_text'), str) or
            not re.fullmatch(r'\d{14}', evidence['kickoff_text']) or
            kickoff is None or evidence['kickoff_text'] != kickoff.strftime('%Y%m%d%H%M%S') or
            not isinstance(evidence.get('rowname'), str) or
            not evidence['rowname'].endswith('_' + str(int(kickoff.replace(tzinfo=KST).timestamp() * 1000)))):
        raise ValueError('invalid official source')
    try:
        options = [{'key': option['key'], 'text': 'fixture\n' + option['side']}
                   for option in evidence['options']]
        raw = {'game_name': evidence['game_name'], 'game_no': evidence['game_no'],
               'league': evidence['league'], 'kickoff_text': evidence['kickoff_text'],
               'rowname': evidence['rowname'], 'market_text': evidence['market_text'],
               'label': evidence['label'], 'result_text': evidence['result_text'],
               'result_action': evidence['result_action'], 'options': options}
        normalized = _normalize_official_row(raw, year, rnd, received)
        if any(row.get(key) != normalized.get(key) for key in (
                'year', 'round', 'game_no', 'date', 'sport', 'league', 'home', 'away',
                'market', 'label', 'n_way', 'result', 'raw_market_scope', 'raw_label',
                'raw_result_text')):
            raise ValueError('official source does not reproduce normalized result')
    except (KeyError, TypeError, ValueError):
        raise ValueError('invalid official source') from None
    return dict(evidence)


def plan_backfill(records, rows, *, snapshot_ids, observed_at, source):
    """Return reviewable entries and holds. No I/O or ledger mutation occurs."""
    observed = _aware_timestamp(observed_at)
    if (observed is None or not isinstance(source, dict) or
            not source.get('name') or not source.get('provenance')):
        raise ValueError('timezone-aware observed_at and source name and provenance required')
    selected = set(snapshot_ids)
    rows_by_event = {}
    for row in rows:
        try:
            game = {**row, 'year': int(row['year']), 'round': int(row['round'])}
            rows_by_event.setdefault(event_id(game), []).append(row)
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
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
        for row in rows_by_event.get(event, ()):
            try:
                year, rnd, nway = int(row['year']), int(row['round']), int(row['n_way'])
                if isinstance(row['n_way'], bool) or nway != row['n_way']:
                    continue
                if any(fixture.get(k) is not None and row.get(k) != fixture[k]
                       for k in ('sport', 'league', 'home', 'away', 'date',
                                 'source_event_id', 'match_id')):
                    continue
                if ((fixture.get('year') is not None and year != int(fixture['year'])) or
                        (fixture.get('round') is not None and rnd != int(fixture['round']))):
                    continue
                if not row.get('game_no') or row.get('result') in UNPLAYED:
                    continue
                game = {**row, 'year': year, 'round': rnd}
                if event_id(game) != event:
                    continue
                kickoff = _game_kickoff(game, observed)
                frozen_kickoff = _aware_timestamp(pred.get('kickoff'))
                if (kickoff is None or frozen_kickoff is None or
                        kickoff.replace(tzinfo=KST) != frozen_kickoff or
                        frozen_kickoff > observed):
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
                # An available option vector proves n-way; otherwise require
                # the selected side to identify exactly one supported n-way.
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
                selected_identity = (p.get('selection_id'), p.get('offer_id'))
                if selected_identity not in generated:
                    continue
                if options:
                    if set(generated) != frozen:
                        continue
                else:
                    side = names[generated.index(selected_identity)]
                    possible = {count for (market, count), labels in SEL_NAMES.items()
                                if market == row.get('market') and side in labels}
                    if possible != {nway}:
                        continue
                try:
                    evidence = _official_source(row, year, rnd, observed, source)
                except (TypeError, ValueError):
                    matches.append((None, None, None, None, None))
                    continue
                void = row.get('result') in {'취소', '연기', '중단', '무효'} or (row.get('market') == '승패' and nway == 2 and row.get('result') == '무승부')
                winner = winner_index(nway, row.get('result'))
                if not void and (winner is None or winner >= nway):
                    continue
                index = generated.index((p['selection_id'], p['offer_id']))
                result = 'void' if void else 'hit' if index == winner else 'miss'
                matches.append((row, result, nway, line, evidence))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        if len(matches) != 1:
            holds.append({'snapshot_id': sid, 'reason': 'ambiguous_or_unmatched', 'matches': len(matches)})
            continue
        row, result, nway, line, evidence = matches[0]
        if row is None:
            holds.append({'snapshot_id': sid, 'reason': 'invalid_official_source'})
            continue
        outcome = {'result': result, 'selection_id': p['selection_id'], 'official_result': row['result']}
        version = 'official-' + hashlib.sha256(json.dumps(outcome, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:16]
        entries.append({'event_id': event, 'snapshot_id': sid, 'outcome': outcome,
                        'settled_at': observed.isoformat(), 'settlement_version': version,
                        'source': {**source, 'year': int(row['year']), 'round': int(row['round']),
                                   'game_no': str(row['game_no']), 'game_date': row['date'],
                                   'market': row['market'], 'label': row.get('label') or '',
                                   'n_way': nway, 'line': line,
                                   **({'official_source': evidence} if evidence else {})}})
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
