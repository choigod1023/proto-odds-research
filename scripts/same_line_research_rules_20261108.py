"""Strict selection and physical-event eligibility for the retrospective audit."""
from collections import Counter
from datetime import datetime, timedelta

def canonical_options(market, options, names):
    directions = names.get((market, 2))
    if not directions or len(options) != 2:
        return None
    by_name = {o.get('선택'): o for o in options}
    if len(by_name) != 2 or set(by_name) != set(directions):
        return None
    return [by_name[name] for name in directions]

def parse_time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))

def physical_key(row):
    fixture = row['fixture_key']
    return (fixture[1], fixture[2], fixture[3], fixture[4], parse_time(row['kickoff']).astimezone().timestamp())

def partition(rows):
    # Remove every occurrence of an event duplicated across rounds or product rows.
    counts = Counter(physical_key(row) for row in rows)
    unique = [row for row in rows if counts[physical_key(row)] == 1]
    pre, late = [], []
    for row in unique:
        try:
            captured = parse_time(row['record_captured_at'])
            cutoff = parse_time(row['kickoff']) - timedelta(minutes=30)
            if captured.tzinfo is None or cutoff.tzinfo is None:
                raise ValueError('timezone required')
            (pre if captured <= cutoff else late).append(row)
        except (TypeError, ValueError, KeyError, AttributeError):
            late.append(row)
    return pre, late, len(rows) - len(unique)
