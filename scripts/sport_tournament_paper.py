"""Offline, settled-only archival two-leg research; never places wagers.

``paper(data, p, weekly, rule)`` accepts one row per game. For multiclass
predictions, p is (n, k), odds contains k prices per row, and y is the winning
zero-based class. For binary single-outcome predictions, p and odds are scalar
per row and y is 0/1. Predictions align by row position, not DataFrame index.

Weeks are ISO Monday-Sunday; dates use the supplied calendar (no timezone
conversion). A ticket settles before the following calendar day's decisions,
even at month boundaries. Net losses plus unresolved stakes may not exceed
50,000 in the ticket's purchase month. Earlier profits replenish this budget.
ROI is a fraction (None without tickets), stake is total turnover, and drawdown
is peak-to-trough settled net equity, starting at zero. All observed calendar
months from min(date) to max(date), including empty months, enter the denominator.
Missing/invalid labels fail explicitly, including labels on ineligible rows.
"""

from collections import defaultdict
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, localcontext
import hashlib
import json
from numbers import Integral

import numpy as np
import pandas as pd


TICKET_STAKE = 10_000
MONTHLY_LOSS_LIMIT = 50_000
RULES = ("probability", "positive_edge", "wide_edge", "high_confidence")
WARNINGS = [
    "Round purchasability is unverified: the source lacks round identifiers.",
    "Settled-only archival research; not a live or executable betting strategy.",
    "Next-day settlement is an approximation, not real-time settlement.",
    "Two-leg Decimal product truncated to 2 decimals then ceiling to 1 decimal "
    "is a research assumption, not a verified operator pricing rule.",
]


def _price(first, second):
    """Research price: decimal product -> truncate cents -> ceiling tenths."""
    with localcontext() as ctx:
        ctx.prec = 80
        product = Decimal(str(first)) * Decimal(str(second))
        return product.quantize(Decimal(".01"), rounding=ROUND_DOWN).quantize(
            Decimal(".1"), rounding=ROUND_CEILING
        )


def _id(parts):
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()[:24]


def _inputs(data, p):
    required = {"date", "kickoff", "league", "home_team", "away_team", "y", "odds"}
    if not isinstance(data, pd.DataFrame) or not required.issubset(data.columns):
        raise ValueError("data must be a DataFrame with " + ", ".join(sorted(required)))
    if not data.columns.is_unique:
        raise ValueError("Duplicate column names are not supported")
    try:
        probs = np.asarray(p, dtype=float)
    except (ValueError, TypeError) as exc:
        raise ValueError("p must be numeric probabilities") from exc
    if probs.ndim not in (1, 2) or probs.shape[0] != len(data):
        raise ValueError("p must have shape (n,) or (n, k), aligned by row position")
    binary = probs.ndim == 1
    if binary:
        probs = probs[:, None]
    if probs.shape[1] == 0 or not np.isfinite(probs).all() or ((probs < 0) | (probs > 1)).any():
        raise ValueError("p must contain finite probabilities in [0, 1]")
    if not binary and not np.allclose(probs.sum(axis=1), 1, atol=1e-6, rtol=0):
        raise ValueError("Multiclass probabilities must sum to one per game")
    rows = []
    seen = set()
    for pos, record in enumerate(data.to_dict("records")):
        try:
            day = pd.Timestamp(record["date"])
            if pd.isna(day):
                raise ValueError("missing date")
            day = day.date()
            names = []
            for key in ("league", "home_team", "away_team"):
                value = record[key]
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"missing or invalid {key}")
                names.append(value.strip())
            raw_kickoff = record["kickoff"]
            if pd.isna(raw_kickoff):
                raise ValueError("missing kickoff")
            kickoff = str(raw_kickoff).strip()
            # Support both clock-only and full timestamp source columns.
            stamp = pd.Timestamp(f"{day} {kickoff}" if len(kickoff) <= 8 else kickoff)
            if pd.isna(stamp) or stamp.date() != day:
                raise ValueError("kickoff must belong to date")
            kickoff = stamp.isoformat()
            identity = (day.isoformat(), kickoff, *names)
            if identity in seen:
                raise ValueError("duplicate event; provide one row per game")
            seen.add(identity)
            prices = np.asarray([record["odds"]] if binary else record["odds"], dtype=float)
            if prices.shape != (probs.shape[1],) or not np.isfinite(prices).all() or (prices < 1).any():
                raise ValueError("odds must be finite prices >= 1 matching p's outcomes")
            label = float(record["y"])
            classes = 2 if binary else probs.shape[1]
            if not np.isfinite(label) or not label.is_integer() or not 0 <= label < classes:
                raise ValueError("y must be a settled class label (no missing labels)")
        except (ValueError, TypeError, OverflowError) as exc:
            raise ValueError(f"Invalid row {pos}: {exc}") from exc
        rows.append(dict(day=day, kickoff=kickoff, identity=identity,
                         event_id=_id(identity), prices=prices, y=int(label),
                         p=probs[pos], binary=binary))
    return rows


def _candidate(row, rule):
    candidates = []
    for outcome, (prob, price) in enumerate(zip(row["p"], row["prices"])):
        # Decimal also keeps exact rule boundaries such as edge == .03 stable.
        prob, price = Decimal(str(prob)), Decimal(str(price))
        edge = prob * price - 1
        eligible = {
            "probability": prob >= Decimal(".55") and price >= Decimal("1.5"),
            "positive_edge": prob >= Decimal(".55") and price >= Decimal("1.5") and edge > 0,
            "wide_edge": prob >= Decimal(".5") and price >= Decimal("1.5") and edge >= Decimal(".03"),
            "high_confidence": prob >= Decimal(".6") and price > 1 and edge > 0,
        }[rule]
        if eligible:
            rank = edge if rule in ("positive_edge", "wide_edge") else prob
            # Outcome number resolves within-game ties without reading labels.
            candidates.append(((-rank, outcome), outcome, float(prob), float(price), float(edge)))
    if not candidates:
        return None
    key, outcome, prob, price, edge = min(candidates)
    return dict(row=row, outcome=outcome, p=prob, odds=price, edge=edge,
                rank_key=(key[0], row["identity"], outcome))


def paper(data, p, weekly, rule):
    """Return JSON-safe metrics and ``tickets_detail``; no I/O or model fitting.

    Detail entries contain ticket id, date, settlement_date, month, selected_ids,
    event_ids, legs (id/event_id/outcome/p/odds/edge), price, stake, won and profit.
    Ranking ties use calendar/kickoff/league/teams then outcome index, independent
    of input ordering and outcomes. Duplicate game rows are rejected.
    """
    if isinstance(weekly, bool) or not isinstance(weekly, Integral) or weekly not in (2, 3):
        raise ValueError("weekly must be integer 2 or 3")
    if rule not in RULES:
        raise ValueError(f"rule must be one of {RULES}")
    rows = _inputs(data, p)
    months = ([str(x) for x in pd.period_range(min(r["day"] for r in rows),
                                             max(r["day"] for r in rows), freq="M")]
              if rows else [])
    monthly = dict.fromkeys(months, 0)
    reserved = defaultdict(int)
    weekly_counts = defaultdict(int)
    by_day = defaultdict(list)
    for row in rows:
        candidate = _candidate(row, rule)
        if candidate:
            by_day[row["day"]].append(candidate)
    details, pending = [], []
    equity = peak = drawdown = 0

    def settle(day):
        nonlocal equity, peak, drawdown
        for ticket in pending[:]:
            if day is None or ticket["settlement_date"] <= day.isoformat():
                monthly[ticket["month"]] += ticket["profit"]
                reserved[ticket["month"]] -= TICKET_STAKE
                equity += ticket["profit"]
                peak = max(peak, equity)
                drawdown = max(drawdown, peak - equity)
                pending.remove(ticket)

    for day in sorted(by_day):
        settle(day)
        month = day.strftime("%Y-%m")
        week = tuple(day.isocalendar()[:2])
        candidates = sorted(by_day[day], key=lambda c: c["rank_key"])
        if (len(candidates) < 2 or weekly_counts[week] >= weekly
                or monthly[month] - reserved[month] - TICKET_STAKE < -MONTHLY_LOSS_LIMIT):
            continue
        selected = candidates[:2]
        legs = [dict(id=_id([c["row"]["event_id"], c["outcome"]]),
                     event_id=c["row"]["event_id"], outcome=c["outcome"],
                     event_identity=list(c["row"]["identity"]),
                     date=c["row"]["day"].isoformat(), kickoff=c["row"]["kickoff"],
                     league=c["row"]["identity"][2], home_team=c["row"]["identity"][3],
                     away_team=c["row"]["identity"][4],
                     p=c["p"], odds=c["odds"], edge=c["edge"]) for c in selected]
        ids = [leg["id"] for leg in legs]
        price = _price(legs[0]["odds"], legs[1]["odds"])
        # Labels are accessed only after the ticket selection and budget gate.
        won = all(c["row"]["y"] == (1 if c["row"]["binary"] else c["outcome"])
                  for c in selected)
        profit = int(Decimal(TICKET_STAKE) * (price - 1)) if won else -TICKET_STAKE
        ticket = dict(id=_id([day.isoformat(), ids]), date=day.isoformat(),
                      settlement_date=(pd.Timestamp(day) + pd.Timedelta(days=1)).date().isoformat(),
                      month=month, selected_ids=ids, event_ids=[leg["event_id"] for leg in legs],
                      legs=legs, price=float(price), stake=TICKET_STAKE, won=won, profit=profit)
        details.append(ticket)
        pending.append(ticket)
        reserved[month] += TICKET_STAKE
        weekly_counts[week] += 1
    settle(None)  # Archival end-of-period tickets settle, charged to purchase month.
    stake = len(details) * TICKET_STAKE
    profitable = sum(value > 0 for value in monthly.values())
    return dict(tickets=len(details), wins=sum(t["won"] for t in details), net=equity,
                roi=equity / stake if stake else None, monthly_net=monthly,
                profitable_months=profitable, months=len(months),
                profitable_month_rate=profitable / len(months) if months else None,
                worst_month=min(monthly.values(), default=0), max_drawdown=drawdown,
                stake=stake, ticket_stake=TICKET_STAKE, weekly=int(weekly), rule=rule,
                tickets_detail=details, warnings=list(WARNINGS))
