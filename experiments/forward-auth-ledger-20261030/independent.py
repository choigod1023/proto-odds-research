"""Second implementation: no imports from ledger.py or replay.py.

Exact integer settlement via Fraction, independent loop aggregation and bootstrap.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import hashlib
import json


def verify_historical(rows):
    out = Counter(revisions=0, internal_t30=0, external_certified=0)
    identities, internal = set(), set()
    for r in rows:
        out["revisions"] += 1
        identities.add(r["event_id"])
        try:
            start = datetime.fromisoformat(r["kickoff"].replace("Z", "+00:00"))
            clocks = [datetime.fromisoformat(r[k].replace("Z", "+00:00"))
                      for k in ("captured_at", "as_of", "market_observed_at")]
            good = all(d.utcoffset() is not None and d <= start-timedelta(minutes=30) for d in clocks)
        except (KeyError, ValueError, TypeError, AttributeError):
            good = False
        if good:
            out["internal_t30"] += 1
            internal.add(r["event_id"])
        # These input files have no raw receipt hash or signed checkpoint field.
        assert not r.get("receipt_checkpoint") and not r.get("source_sha256")
    out["fixtures"] = len(identities)
    out["internal_t30_fixtures"] = len(internal)
    return dict(out)


def settle(events, model):
    results = []
    for e in events:
        selected = e["picks"][model]
        if selected is None:
            continue
        prices = dict(zip(e["order"], e["prices"]))
        win = selected == e["winner"]
        returned = int(Fraction(str(prices[selected]))*10000) if win else 0
        results.append((e["fixture_id"], int(win), returned-10000))
    return results


def weekly_metrics(events, candidate, control, samples):
    a, b = settle(events, candidate), settle(events, control)
    assert len(a) == len(b) == len(events)
    weeks = {}
    for e, first, second in zip(events, a, b):
        date = datetime.fromisoformat(e["evaluation_date"])
        week = (date-timedelta(days=date.weekday())).date().isoformat()
        weeks.setdefault(week, []).append((first[1]-second[1], first[2]-second[2]))
    groups = [weeks[k] for k in sorted(weeks)]
    draws = []
    for chosen in samples:
        n = acc = net = 0
        for index in chosen:
            for hit_delta, profit_delta in groups[index]:
                n += 1
                acc += hit_delta
                net += profit_delta
        draws.append([acc/n, net/(10000*n)])
    return draws


def verify_chain_independent(records, ledger_id):
    """Independent structural verification; semantic validation lives in ledger.py."""
    prev = None
    for index, r in enumerate(records, 1):
        encoded = json.dumps({k:v for k,v in r.items() if k != "record_hash"},
                             ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        assert r["ledger_id"] == ledger_id and r["sequence"] == index
        assert r["previous_hash"] == prev
        assert hashlib.sha256(encoded).hexdigest() == r["record_hash"]
        prev = r["record_hash"]
    return len(records)
