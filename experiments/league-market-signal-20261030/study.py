"""Frozen study F. Retrospective prices are NOT authenticated pregame quotes."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import NormalDist

import numpy as np

FAMILIES = {"ML": ("승패", "승무패"), "HC": ("핸디캡",), "OU": ("언더오버",)}
BINS = ((1.01, 1.5), (1.5, 2), (2, 3), (3, 5), (5, 10), (10, None))
ALLOWED = {"odds", "side_index_0based"}


def read_jsonl(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def write_gz(path, rows):
    with path.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as handle:
            for row in rows:
                handle.write((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode())


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def date_of(row):
    # Input is explicitly KST; date-only grouping never acts as receipt evidence.
    dt = datetime.fromisoformat(row["kickoff"])
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return dt.date().isoformat()


def phase_map(dates):
    days = sorted(set(dates))
    a, b = len(days) // 3, 2 * len(days) // 3
    return {day: "train" if i < a else "validation" if i < b else "test" for i, day in enumerate(days)}


def quote_only(choices):
    return [{key: c[key] for key in ALLOWED} for c in choices]


def predict(quotes, bin_index):
    """Only quote projection accepted; outcome-like feature keys fail closed."""
    if any(set(q) != ALLOWED for q in quotes):
        raise ValueError("not a quote-only feature vector")
    if not quotes or any(not math.isfinite(float(q["odds"])) or q["odds"] < 1.01 for q in quotes):
        raise ValueError("invalid odds")
    lo, hi = BINS[bin_index]
    eligible = [i for i, q in enumerate(quotes) if lo <= q["odds"] and (hi is None or q["odds"] < hi)]
    order = lambda i: (quotes[i]["odds"], quotes[i]["side_index_0based"])
    return (min(eligible, key=order) if eligible else None), min(range(len(quotes)), key=order)


def receipt_eligible(quote):
    """Cannot rehabilitate historical/final quotes with an assumed available_at."""
    if not quote.get("independent_receipt_verified"):
        return False
    try:
        got = datetime.fromisoformat(quote["received_at"])
        kickoff = datetime.fromisoformat(quote["kickoff"])
        cutoff = datetime.fromisoformat(quote["decision_at"])
        return all(t.tzinfo for t in (got, kickoff, cutoff)) and got <= cutoff < kickoff
    except (KeyError, ValueError, TypeError):
        return False


def product_order(choices):
    c = choices[0]
    try:
        game = int(c["game_no"])
    except (ValueError, TypeError):
        game = 10**9
    return c["issue_year"], c["round"], game, str(c["market_id"])


def canonical(frame, families=None):
    groups = defaultdict(list)
    for c in frame["choices"]:
        groups[c["market_id"]].append(c)
    out = {}
    for family, names in (FAMILIES if families is None else families).items():
        eligible = [cs for cs in groups.values() if cs[0]["market"] in names]
        if not eligible:
            continue
        cs = sorted(min(eligible, key=product_order), key=lambda c: c["side_index_0based"])
        # Fail closed on malformed canonical group, never fall back using labels.
        if len(cs) not in (2, 3) or len(cs) != cs[0]["n_way"] or len({c["side_index_0based"] for c in cs}) != len(cs):
            continue
        out[family] = cs
    return out


def cents(c):
    if c.get("refund") is True:
        return 0
    if type(c.get("hit")) is not bool or type(c.get("refund")) is not bool:
        raise ValueError("unsettled or ambiguous outcome")
    return round(c["odds"] * 100) - 100 if c["hit"] else -100


def build_predictions(frames, families=None, frozen_phases=None):
    """Freeze quote-only predictions BEFORE looking at any settlement labels."""
    phases = frozen_phases if frozen_phases is not None else phase_map(date_of(f) for f in frames)
    predictions = []
    for f in frames:
        date = date_of(f)
        dt = datetime.fromisoformat(f["kickoff"])
        week = f"{dt.isocalendar().year}-W{dt.isocalendar().week:02}"
        for family, cs in canonical(f, families).items():
            phase = phases[date]
            quote = quote_only(cs)
            mass = sum(1 / c["odds"] for c in cs)
            for k in range(len(BINS)):
                pick, base = predict(quote, k)
                if pick is None:
                    continue
                c, m = cs[pick], cs[base]
                predictions.append({"event_id": f["event_id"], "kickoff": f["kickoff"], "date": date,
                    "week": week, "phase": phase, "league": f["league"], "family": family, "bin": k,
                    "market_id": c["market_id"], "pick": pick, "market_pick": base,
                    "odds": c["odds"], "market_odds": m["odds"], "p": 1 / c["odds"] / mass})
    return predictions, phases


def make_records(frames, families=None, frozen_phases=None):
    predictions, phases = build_predictions(frames, families, frozen_phases)
    labels, opportunities, audits = {}, Counter(), Counter()
    for f in frames:
        for family, cs in canonical(f, families).items():
            audits["canonical_products"] += 1
            if any(type(c.get("hit")) is not bool or type(c.get("refund")) is not bool for c in cs):
                audits["incomplete_settlement"] += 1
                continue
            if any(c["refund"] for c in cs) and not all(c["refund"] for c in cs):
                audits["mixed_refund_rejected"] += 1
                continue
            if not all(c["refund"] for c in cs) and sum(c["hit"] for c in cs) != 1:
                audits["nonexclusive_result_rejected"] += 1
                continue
            labels[f["event_id"], cs[0]["market_id"]] = cs
            opportunities[f["league"], family, phases[date_of(f)]] += 1
    records = []
    for p in predictions:
        cs = labels.get((p["event_id"], p["market_id"]))
        if cs is not None:
            c, m = cs[p["pick"]], cs[p["market_pick"]]
            records.append(dict(p, hit=c["hit"], market_hit=m["hit"], void=c["refund"],
                profit_cents=cents(c), market_profit_cents=cents(m)))
    return records, opportunities, dict(audits), phases


def metrics(rows, opportunities=None):
    n = len(rows)
    nonvoid = sum(not r["void"] for r in rows)
    wins = sum(bool(r["hit"]) and not r["void"] for r in rows)
    return {"n": n, "nonvoid": nonvoid, "wins": wins, "void": n - nonvoid,
        "coverage": n / opportunities if opportunities else None,
        "accuracy": wins / nonvoid if nonvoid else None,
        "roi": sum(r["profit_cents"] for r in rows) / (100 * n) if n else None,
        "calibration_gap": sum(float(r["hit"]) - r["p"] for r in rows if not r["void"]) / nonvoid if nonvoid else None}


def independent_verify(rows):
    # Separate cash ledger: payout = stake*price or stake on refund, independently
    # of cents()/metrics(); Decimal prevents shared floating settlement roundoff.
    cash = Decimal(0)
    base_cash = Decimal(0)
    wins = live = base_wins = 0
    for r in rows:
        stake = Decimal("1.00")
        payout = stake if r["void"] else Decimal(str(r["odds"])) if r["hit"] else Decimal(0)
        base_payout = stake if r["void"] else Decimal(str(r["market_odds"])) if r["market_hit"] else Decimal(0)
        net = int((payout - stake) * 100)
        base_net = int((base_payout - stake) * 100)
        if net != r["profit_cents"] or base_net != r["market_profit_cents"]:
            raise AssertionError("per-leg independent payout mismatch")
        cash += payout - stake
        base_cash += base_payout - stake
        if not r["void"]:
            live += 1
            wins += bool(r["hit"])
            base_wins += bool(r["market_hit"])
    primary = metrics(rows)
    if rows and abs(float(cash / len(rows)) - primary["roi"]) > 1e-12:
        raise AssertionError("ROI mismatch")
    if live and wins / live != primary["accuracy"]:
        raise AssertionError("accuracy mismatch")
    return {"rows": len(rows), "matched": True, "profit_cents": int(cash * 100),
        "market_profit_cents": int(base_cash * 100), "wins": wins, "market_wins": base_wins, "nonvoid": live}


def choose(records):
    groups = defaultdict(list)
    for r in records:
        groups[(r["league"], r["family"], r["bin"], r["phase"])].append(r)
    keys = sorted({key[:3] for key in groups})
    selected = []
    for key in keys:
        a, b = metrics(groups.get((*key, "train"), [])), metrics(groups.get((*key, "validation"), []))
        if a["nonvoid"] >= 30 and b["nonvoid"] >= 15 and a["roi"] > 0 and b["roi"] > 0:
            selected.append(key)
    return selected, groups


def bh(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    adjusted = [1.0] * len(values)
    running = 1.0
    for rank in range(len(order), 0, -1):
        idx = order[rank - 1]
        running = min(running, values[idx] * len(order) / rank)
        adjusted[idx] = running
    return adjusted


def inference(rows, repeats=2000, permutations=9999, seed=1030):
    """Week is the resampling unit; all choices of a fixture share that unit."""
    groups = defaultdict(list)
    for r in rows:
        groups[r["week"]].append(r)
    weeks = sorted(groups)
    if not rows:
        return {"weeks": 0, "roi_p": 1.0, "accuracy_delta_p": 1.0, "roi_delta_p": 1.0,
            "accuracy_delta": None, "roi_delta": None, "accuracy_delta_ci": [None, None], "roi_delta_ci": [None, None], "roi_ci": [None, None]}
    arr = np.array([[len(groups[w]), sum(not r["void"] for r in groups[w]),
        sum((int(r["hit"]) - int(r["market_hit"])) if not r["void"] else 0 for r in groups[w]),
        sum(r["profit_cents"] for r in groups[w]) / 100,
        sum(r["profit_cents"] - r["market_profit_cents"] for r in groups[w]) / 100] for w in weeks], dtype=float)
    totals = arr.sum(axis=0)
    delta_a = totals[2] / totals[1] if totals[1] else None
    delta_r = totals[4] / totals[0]
    out = {"weeks": len(weeks), "accuracy_delta": delta_a, "roi_delta": delta_r}
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(weeks), size=(repeats, len(weeks)))
    res = arr[draws].sum(axis=1)
    for name, num, den in (("accuracy_delta_ci", 2, 1), ("roi_ci", 3, 0), ("roi_delta_ci", 4, 0)):
        ratios = res[res[:, den] > 0, num] / res[res[:, den] > 0, den]
        out[name] = np.quantile(ratios, [.025, .975]).tolist() if len(ratios) else [None, None]
    if len(weeks) < 8:
        out.update(roi_p=1.0, accuracy_delta_p=1.0, roi_delta_p=1.0)
        return out
    # A fixed global calendar sign sequence, sliced by week, couples tests across
    # cells while retaining all within-week/fixture dependence.
    epoch_weeks = [f"{year}-W{week:02}" for year in range(2020, 2031) for week in range(1, 54)]
    global_signs = np.random.default_rng(seed + 1).choice([-1, 1], size=(permutations, len(epoch_weeks)))
    columns = [epoch_weeks.index(w) for w in weeks]
    signs = global_signs[:, columns]
    null = signs @ arr[:, [3, 2, 4]]
    for j, (name, column) in enumerate((("roi_p", 3), ("accuracy_delta_p", 2), ("roi_delta_p", 4))):
        out[name] = float((1 + np.sum(null[:, j] >= totals[column] - 1e-12)) / (permutations + 1)) if totals[column] > 0 else 1.0
    return out


def required_pairs(delta, discordance):
    z = NormalDist().inv_cdf(.975)
    zb = NormalDist().inv_cdf(.8)
    return math.ceil((z * math.sqrt(discordance) + zb * math.sqrt(discordance - delta**2)) ** 2 / delta**2)


def independent_arm_size(p, delta):
    p1 = min(p + delta, .999)
    bar = (p + p1) / 2
    z, zb = NormalDist().inv_cdf(.975), NormalDist().inv_cdf(.8)
    return math.ceil((z * math.sqrt(2 * bar * (1 - bar)) + zb * math.sqrt(p * (1 - p) + p1 * (1 - p1))) ** 2 / delta**2)


def evaluate_archive(frames, repeats=2000, permutations=9999):
    records, opportunities, audit, phases = make_records(frames)
    selected, groups = choose(records)
    leagues = sorted({r["league"] for r in frames})
    cells, ps = [], []
    for league in leagues:
        for family in FAMILIES:
            for k in range(len(BINS)):
                key = (league, family, k)
                row = {"league": league, "family": family, "bin": k, "odds_bin": BINS[k], "selected_in_development": key in selected}
                for phase in ("train", "validation", "test"):
                    row[phase] = metrics(groups.get((*key, phase), []), opportunities.get((league, family, phase), 0))
                row["test_inference"] = inference(groups.get((*key, "test"), []), repeats, permutations)
                cells.append(row)
                ps.extend(row["test_inference"][name] for name in ("roi_p", "accuracy_delta_p", "roi_delta_p"))
    qs = bh(ps)
    for i, row in enumerate(cells):
        for j, name in enumerate(("roi_q", "accuracy_delta_q", "roi_delta_q")):
            row["test_inference"][name] = qs[3 * i + j]
    selected_rows = [c for c in cells if c["selected_in_development"]]
    survivors = [c for c in selected_rows if c["test_inference"]["roi_q"] <= .05 and c["test"]["roi"] > 0]
    accuracy_survivors = [c for c in selected_rows if c["test_inference"]["accuracy_delta_q"] <= .05 and c["test_inference"]["accuracy_delta"] > 0]
    power = []
    for league in leagues:
        ml = [r for r in records if r["league"] == league and r["family"] == "ML" and r["pick"] == r["market_pick"] and r["phase"] == "test" and not r["void"]]
        p = sum(r["hit"] for r in ml) / len(ml) if ml else None
        power.append({"league": league, "test_baseline_n": len(ml), "test_baseline_hit_rate": p,
            "paired_q20_plus3pp": required_pairs(.03, .2), "paired_q50_plus3pp": required_pairs(.03, .5),
            "paired_q20_plus5pp": required_pairs(.05, .2), "paired_q50_plus5pp": required_pairs(.05, .5),
            "independent_per_arm_plus3pp": independent_arm_size(p, .03) if p is not None else None,
            "independent_per_arm_plus5pp": independent_arm_size(p, .05) if p is not None else None})
    return {"frames": len(frames), "physical_ids": len({r["event_id"] for r in frames}), "archive_first": min(phases), "archive_last": max(phases),
        "phase_dates": {p: [min(d for d in phases if phases[d] == p), max(d for d in phases if phases[d] == p)] for p in ("train", "validation", "test")},
        "candidate_trials": len(cells), "multiplicity_tests": len(ps), "selected": selected_rows, "survivors_positive_roi": survivors,
        "survivors_accuracy": accuracy_survivors, "audit": audit, "cells": cells, "power": power,
        "independent_settlement": independent_verify(records), "raw_records": records,
        "certified_pregame_prices": 0, "fixed9to1_archive": "UNAVAILABLE: actual votes absent, never equate to market"}


def supplement(ledger, selected):
    """Previously exposed strict492, independently labelled diagnostic NOT new test."""
    records = []
    for f in ledger:
        if not all(f["picks"].get(m) for m in ("market", "market90_vote10", "tipster_vote")):
            continue
        quotes = [{"odds": price, "side_index_0based": i} for i, price in enumerate(f["prices"])]
        for league, family, k in selected:
            if f["league"] != league or family != "ML":
                continue
            pick, _ = predict(quotes, k)
            if pick is None:
                continue
            m = f["order"].index(f["picks"]["market"])
            h = f["order"].index(f["picks"]["market90_vote10"])
            winner = f["order"].index(f["winner"])
            dt = datetime.fromisoformat(f["match_date"])
            r = {"event_id": f["fixture_id"], "week": f"{dt.isocalendar().year}-W{dt.isocalendar().week:02}", "league": league, "bin": k,
                "odds": f["prices"][pick], "market_odds": f["prices"][m], "p": 1 / f["prices"][pick] / sum(1 / o for o in f["prices"]),
                "hit": pick == winner, "market_hit": m == winner, "void": False,
                "profit_cents": round(f["prices"][pick] * 100) - 100 if pick == winner else -100,
                "market_profit_cents": round(f["prices"][m] * 100) - 100 if m == winner else -100}
            r["hybrid_hit"] = h == winner
            r["hybrid_odds"] = f["prices"][h]
            r["hybrid_profit_cents"] = round(f["prices"][h] * 100) - 100 if h == winner else -100
            records.append(r)
    hybrid = [dict(r, market_hit=r["hybrid_hit"], market_odds=r["hybrid_odds"], market_profit_cents=r["hybrid_profit_cents"]) for r in records]
    independent_verify(hybrid)
    return {"scope": "already-exposed strict common492 diagnostic; NOT disjoint test; original Shin market and actual9:1",
        "strict_common_n": sum(all(f["picks"].get(m) for m in ("market", "market90_vote10", "tipster_vote")) for f in ledger),
        "selected_opportunities": metrics(records, len(ledger)), "vs_market": inference(records), "vs_fixed9to1": inference(hybrid),
        "independent_settlement": independent_verify(records), "raw_records": records}


def run(input_dir, output_dir, repeats=2000, permutations=9999):
    output_dir.mkdir(parents=True, exist_ok=True)
    frames = read_jsonl(input_dir / "archive.jsonl.gz")
    result = evaluate_archive(frames, repeats, permutations)
    records = result.pop("raw_records")
    selected = [(r["league"], r["family"], r["bin"]) for r in result["selected"]]
    # Exact exception requested by user, separately frozen in an addendum; never
    # silently conflate three-direction winning-margin market with ML.
    special_frames = [f for f in frames if f["league"] == "KBL"]
    phases = phase_map(date_of(f) for f in frames)
    special, special_opps, _, _ = make_records(special_frames, {"margin5": ("승⑤패",)}, phases)
    special = [r for r in special if r["bin"] == 4]
    special_selected, _ = choose(special)
    exception = {"name": "KBL/승⑤패/[5,10)", "separate_addendum_after_primary_evaluation": True,
        "selected_in_development": bool(special_selected),
        "metrics": {p: metrics([r for r in special if r["phase"] == p], special_opps.get(("KBL", "margin5", p), 0)) for p in ("train", "validation", "test")},
        "inference": inference([r for r in special if r["phase"] == "test"], repeats, permutations),
        "independent_settlement": independent_verify(special)}
    ps = [c["test_inference"][name] for c in result["cells"] for name in ("roi_p", "accuracy_delta_p", "roi_delta_p")]
    ps += [exception["inference"][name] for name in ("roi_p", "accuracy_delta_p", "roi_delta_p")]
    qs = bh(ps)
    exception["inference"].update(dict(zip(("roi_q", "accuracy_delta_q", "roi_delta_q"), qs[-3:])))
    result["named_kbl_exception"] = exception
    result["multiplicity_tests_including_named_exception"] = len(ps)
    for i, c in enumerate(result["cells"]):
        c["test_inference"].update(dict(zip(("roi_q", "accuracy_delta_q", "roi_delta_q"), qs[3*i:3*i+3])))
    strict = json.loads((input_dir / "strict-reference.json").read_text(encoding="utf-8"))["event_ledger"]
    supp = supplement(strict, selected)
    supp_records = supp.pop("raw_records")
    additional = read_jsonl(input_dir / "additional-period-candidates.jsonl.gz")
    later = [c for c in additional if c["kickoff"][:10] > result["archive_last"]]
    result["additional_period"] = {"candidate_rows": len(additional), "strictly_after_archive_rows": len(later),
        "evaluation": "unavailable" if not later else "requires protocol-compliant exact product/physical dedup; not treated as completed"}
    result["supplement_actual_votes"] = supp
    write_gz(output_dir / "predictions.jsonl.gz", records)
    write_gz(output_dir / "quote-only-predictions.jsonl.gz", build_predictions(frames)[0])
    write_gz(output_dir / "strict-diagnostic-predictions.jsonl.gz", supp_records)
    write_gz(output_dir / "named-kbl-exception-predictions.jsonl.gz", special)
    save(output_dir / "results.json", result)
    repo = Path(__file__).resolve().parents[2]
    protocol = repo / "docs/research/2026-10-30-league-market-signal-protocol.json"
    files = [*input_dir.glob("*"), Path(__file__), protocol, Path(__file__).parent / "kbl-exception-addendum.json", *output_dir.glob("*")]
    files = sorted(p for p in files if p.is_file() and p.name != "hashes.json")
    save(output_dir / "hashes.json", {p.resolve().relative_to(repo).as_posix(): {
        "sha256": hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() if p == protocol else digest(p),
        "mode": "LF_normalized_UTF8" if p == protocol else "bytes"} for p in files})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", type=Path, default=Path(__file__).parent / "inputs")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    r = run(args.inputs, args.output)
    print(json.dumps({k: r[k] for k in ("frames", "candidate_trials", "multiplicity_tests", "phase_dates", "additional_period")}, ensure_ascii=False))
    print("selected", len(r["selected"]), "ROI survivors", len(r["survivors_positive_roi"]), "accuracy survivors", len(r["survivors_accuracy"]))
