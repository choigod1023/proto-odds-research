# Adapted from prior local tipster research; reused without changing selection rules.
"""Assumption-labelled retrospective replay; not a production-model backtest.

Normalize stake suffix only. Never fill missing pregame odds from results.
All eligible candidates, including unresolved ones, participate in selection.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
KST = timezone(timedelta(hours=9))
STAKE, LIMIT = 10000, 50000
MLB = set('ARI ATL BAL BOS CHC CWS CIN CLE COL DET HOU KC LAA LAD MIA MIL MIN NYM NYY OAK ATH PHI PIT SD SEA SF STL TB TEX TOR WSH'.split())


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('timezone required')
    return result


def canonical(row):
    selection = re.sub(r'\s+\d+(?:\.\d+)?u$', '', row.get('selection', ''), flags=re.I)
    return (row.get('slate_date'), row.get('team_a'), row.get('team_b'),
            row.get('slug'), row.get('market_type'), selection.casefold())


def prepare(events):
    first, finals = {}, defaultdict(list)
    for r in sorted(events, key=lambda r: stamp(r['observed_at'])):
        first.setdefault(r['pick_id'], r)
        if r.get('result') in ('W', 'L', 'P', 'V') and not r.get('is_parlay_leg'):
            finals[canonical(r)].append(r)
    excluded, pool, seen = Counter(), [], set()
    for r in first.values():
        if not (r.get('event_type') == 'first_observed' and r.get('identity_version') == 2
                and r.get('eligible_pre_event') is True and r.get('game_started') is False
                and r.get('result') is None and not r.get('is_parlay_leg')
                and not r.get('is_baseline')):
            excluded['not_pregame_single'] += 1
            continue
        selection = canonical(r)[-1].upper()
        match = re.fullmatch(r'([A-Z]+) ML ([+-]\d{3,4})', selection)
        if (not match or r.get('market_type') != 'moneyline'
                or r.get('team_a') not in MLB or r.get('team_b') not in MLB
                or match[1] not in (r['team_a'], r['team_b'])):
            excluded['not_supported_full_game_MLB_moneyline'] += 1
            continue
        odds = int(match[2])
        if abs(odds) < 100 or r.get('american_odds') != odds:
            excluded['invalid_first_odds'] += 1
            continue
        time_match = re.search(r'\b(\d{1,2}:\d{2})\s+(AM|PM)\s+ET\b', r['game_header'])
        try:
            kickoff = datetime.strptime(r['slate_date']+' '+time_match[1]+' '+time_match[2],
                                       '%Y-%m-%d %I:%M %p').replace(tzinfo=ET)
            observed = stamp(r['observed_at'])
            if observed > kickoff-timedelta(minutes=30):
                raise ValueError('late')
        except (TypeError, ValueError):
            excluded['missing_schedule_or_after_t30'] += 1
            continue
        key = canonical(r)
        if key in seen:
            excluded['duplicate_first_identity'] += 1
            continue
        seen.add(key)
        records = finals[key]
        # Old results on the same displayed date and conflicting grades are ambiguous.
        status, outcome, settled = 'unmatched', None, None
        if records:
            if any(stamp(x['observed_at']) <= max(observed, kickoff) for x in records):
                status = 'ambiguous_earlier_result'
            elif len({x['result'] for x in records}) != 1:
                status = 'conflicting_results'
            else:
                candidate = min(stamp(x['observed_at']) for x in records)
                if candidate <= kickoff+timedelta(hours=36):
                    status, outcome, settled = 'relinked_publisher_result', records[0]['result'], candidate
                else:
                    status = 'result_too_late'
        pool.append(dict(id=r['pick_id'], author=r['slug'], date=r['slate_date'],
            event='|'.join((r['slate_date'], *sorted((r['team_a'], r['team_b'])))),
            side=match[1], observed=observed, kickoff=kickoff,
            odds=1+odds/100 if odds>0 else 1+100/-odds,
            outcome=outcome, settled=settled, linkage=status))
    return pool, dict(excluded)
