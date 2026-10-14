"""Read-only archive normalization and paired diagnostics for team-style research.

Shared methodology with PR #210; no runtime database or production writes.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re

import numpy as np
import pandas as pd

from bets import winner_index
from devig import market_probabilities
from matches import actual_game_year, clean_team

SUPPORTED = {("승패", 2): {"홈승", "홈패"},
             ("승무패", 3): {"홈승", "무승부", "홈패"},
             ("언더오버", 2): {"언더", "오버"},
             ("핸디캡", 2): {"핸디승", "핸디패"},
             ("핸디캡", 3): {"핸디승", "핸디무", "핸디패"}}
DATE = re.compile(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def normalize(raw, protocol):
    """Reject ambiguous reissued prices/results; retain exact match times."""
    counts, scopes = Counter(), Counter()
    frame = raw.fillna("").copy()
    parsed = frame["date_text"].astype(str).str.extract(DATE).apply(pd.to_numeric, errors="coerce")
    years = actual_game_year(frame["year"], frame["round"], parsed[0])
    times = pd.to_datetime(dict(year=years, month=parsed[0], day=parsed[1],
                               hour=parsed[2], minute=parsed[3]), errors="coerce")
    records, conflicts = {}, set()
    for values, kickoff in zip(frame.to_dict("records"), times):
        try:
            n = int(values["n_way"])
        except (ValueError, TypeError):
            counts["invalid_n_way"] += 1
            continue
        market = str(values["market_family"])
        if (market, n) not in SUPPORTED:
            counts["unsupported_market"] += 1
            continue
        if pd.isna(kickoff):
            counts["invalid_date"] += 1
            continue
        day = kickoff.date().isoformat()
        if not protocol["training_start"] <= day <= protocol["evaluation_end"]:
            counts["outside_dates"] += 1
            continue
        sport, league = str(values["sport"]).strip(), str(values["league"]).strip()
        scopes[sport+"|"+league] += 1
        void_flag = str(values["is_void"]).lower()
        if void_flag not in ("true", "1", "false", "0"):
            counts["invalid_void_flag"] += 1
            continue
        if void_flag in ("true", "1"):
            counts["void"] += 1
            continue
        result = str(values["result"]).strip()
        if result not in SUPPORTED[(market, n)]:
            counts["unknown_or_unsettled:"+market+":"+result] += 1
            continue
        try:
            odds = tuple(float(x) for x in str(values["odds"]).split(","))
            if len(odds) != n or not all(math.isfinite(x) and x > 1 for x in odds):
                raise ValueError("invalid odds")
            if not 1.0 <= sum(1/x for x in odds) <= 1.40:
                raise ValueError("unsupported overround")
        except ValueError:
            counts["invalid_odds"] += 1
            continue
        home, away = clean_team(values["home"]), clean_team(values["away"])
        if not sport or not league or not home or not away or home == away:
            counts["invalid_teams_scope"] += 1
            continue
        event = (kickoff.isoformat(), sport, league, home, away)
        label = str(values["market_label"]).strip()
        booking = str(values["booking_class"]).strip()
        label_ok = (not label if market in ("승패", "승무패") else
                    re.fullmatch(r"U [+-]?\d+(?:\.\d+)?", label) if market == "언더오버" else
                    re.fullmatch(r"H [+-]?\d+(?:\.\d+)?", label))
        expected_booking = "3-way-핸디캡" if market == "핸디캡" and n == 3 else f"{n}-way"
        if not label_ok or booking != expected_booking:
            counts["unsupported_period_or_booking"] += 1
            continue
        offer = (*event, market, str(values["booking_class"]), label, n)
        winner = winner_index(n, result)
        previous = records.get(offer)
        if previous is not None:
            if previous["odds"] != odds or previous["winner"] != winner:
                conflicts.add(offer)
            else:
                counts["duplicate_same_offer"] += 1
            continue
        records[offer] = {"event": digest(event), "offer": digest(offer), "day": day,
                          "kickoff": kickoff.isoformat(), "sport": sport, "league": league,
                          "home_team": home, "away_team": away,
                          "market": market, "n_way": n, "odds": odds, "winner": winner}
    clean = [r for key, r in records.items() if key not in conflicts]
    for row in clean:
        row["q"] = market_probabilities(list(row["odds"]))
    clean.sort(key=lambda r: (r["kickoff"], r["offer"]))
    counts["conflicting_offers_excluded"] = len(conflicts)
    return clean, {"input_rows": len(raw), "usable_markets": len(clean),
                   "unique_events": len({r["event"] for r in clean}),
                   "excluded_or_duplicates": dict(counts), "raw_supported_scopes": dict(scopes),
                   "first_day": min((r["day"] for r in clean), default=None),
                   "last_day": max((r["day"] for r in clean), default=None)}


def scope(row):
    return row["sport"], row["league"], row["market"], row["n_way"]


def metrics_per_row(row, probability):
    p = np.asarray(probability)
    y = np.zeros(len(p)); y[row["winner"]] = 1
    choice = int(np.argmax(p))
    return {"brier": float(np.sum((p-y)**2)),
            "log_loss": float(-math.log(max(float(p[row["winner"]]), 1e-12))),
            "hit": int(choice == row["winner"]), "odds": row["odds"][choice],
            "profit": row["odds"][choice]-1 if choice == row["winner"] else -1,
            "choice": choice}


def average(rows, candidate):
    values = [r["metrics"][candidate] for r in rows]
    return {"n": len(rows), **{k: float(np.mean([v[k] for v in values]))
                               for k in ("brier", "log_loss", "hit", "odds", "profit")}}


def diagnostic(rows, candidate, protocol, seed, reference="baseline"):
    weeks = defaultdict(lambda: np.zeros(3))
    for r in rows:
        day = pd.Timestamp(r["day"])
        monday = day-pd.Timedelta(days=day.weekday())
        gain = [r["metrics"][reference][k]-r["metrics"][candidate][k]
                for k in ("brier", "log_loss")]
        weeks[monday] += np.r_[gain, 1]
    active_weeks = len(weeks)
    timeline = pd.date_range(min(weeks), max(weeks), freq="7D")
    values = np.asarray([weeks.get(d, np.zeros(3)) for d in timeline])
    observed = values[:, :2].sum(axis=0)/values[:, 2].sum()
    if active_weeks < 2:
        return {"active_weeks": active_weeks, "calendar_weeks": len(timeline),
                "gain": observed.tolist(), "ci95": None, "p_two_sided": [None, None]}
    width = min(protocol["block_calendar_weeks"], len(values))
    rng = np.random.default_rng(seed)
    draws = []
    for start in range(0, protocol["bootstrap_repeats"], 500):
        size = min(500, protocol["bootstrap_repeats"]-start)
        starts = rng.integers(0, len(values), size=(size, math.ceil(len(values)/width)))
        indexes = ((starts[:, :, None]+np.arange(width)) % len(values)).reshape(size, -1)[:, :len(values)]
        sums = values[indexes].sum(axis=1)
        valid = sums[:, 2] > 0
        draws.append(sums[valid, :2]/sums[valid, 2, None])
    sampled = np.concatenate(draws)
    intervals = np.quantile(sampled, [.025, .975], axis=0).T
    # Centered paired block-bootstrap diagnostic; not an exact randomization p.
    ps = (1+np.sum(np.abs(sampled-observed) >= np.abs(observed), axis=0))/(len(sampled)+1)
    return {"active_weeks": active_weeks, "calendar_weeks": len(timeline),
            "gain": observed.tolist(), "ci95": intervals.tolist(),
            "p_two_sided": ps.tolist(), "bootstrap_valid_draws": len(sampled)}


def holm(pvalues):
    valid = sorted((v, k) for k, v in pvalues.items() if v is not None)
    adjusted, last = {k: None for k in pvalues}, 0.0
    for i, (p, key) in enumerate(valid):
        last = max(last, min(1., (len(valid)-i)*p))
        adjusted[key] = last
    return adjusted
