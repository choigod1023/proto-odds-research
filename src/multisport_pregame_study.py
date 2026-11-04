"""Outcome-blind, same-product research comparison; no serving or wallet writes.

This gate checks preserved records and clocks, not external notarization. A
published-at claim alone must never become a pregame receipt.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import re
from urllib.parse import urlsplit

VERSION = "multisport-pregame-study-v1"
CONTROLS = ("vote", "market", "market90_vote10")
MODEL_SCOPES = {
    "kbo_m0": ("bs", "KBO"), "kbo_m1": ("bs", "KBO"), "kbo_m2": ("bs", "KBO"),
    "mlb_player_lineup": ("bs", "MLB"),
}
OFFICIAL_HOSTS = {
    "KBO": {"www.betman.co.kr", "www.koreabaseball.com", "koreabaseball.com"},
    "MLB": {"www.betman.co.kr", "statsapi.mlb.com"},
    "NPB": {"www.betman.co.kr", "npb.jp"},
    "K리그1": {"www.betman.co.kr", "www.kleague.com"},
    "K리그2": {"www.betman.co.kr", "www.kleague.com"},
    "KBL": {"www.betman.co.kr", "www.kbl.or.kr", "api.kbl.or.kr"},
    "NBA": {"www.betman.co.kr", "cdn.nba.com", "stats.nba.com", "www.nba.com"},
    "K1": {"www.betman.co.kr", "www.kleague.com"},
    "K2": {"www.betman.co.kr", "www.kleague.com"},
}
SHA = re.compile(r"^[a-f0-9]{64}$")
KST = timezone(timedelta(hours=9))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def moment(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("aware_clock_required")
    return parsed.astimezone(timezone.utc)


def physical(row):
    if row["sport"] not in {"sc", "bs", "bk"}:
        raise ValueError("unsupported_sport")
    if any(not isinstance(row.get(k), str) or not row[k] for k in ("league", "home", "away")):
        raise ValueError("exact_fixture_identity_required")
    if row["home"] == row["away"]:
        raise ValueError("opposing_team_identity_collision")
    return (row["sport"], row["league"], moment(row["kickoff"]).isoformat(), row["home"], row["away"])


def numeric(value):
    if isinstance(value, bool):
        raise ValueError("boolean_is_not_numeric")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("nonfinite_number")
    return result


def pregame(row, at, *, max_age=None):
    """Check a *validated cache manifest* clock, never a source's publication claim."""
    if row.get("timing_basis") != "actual_receipt":
        return "no_actual_pregame_receipt"
    if row.get("receipt_verified") is not True:
        return "receipt_body_or_manifest_unverified"
    if not SHA.fullmatch(str(row.get("body_sha256", ""))):
        return "missing_source_body_hash"
    try:
        received = moment(row["received_at"])
    except (ValueError, KeyError, TypeError):
        return "invalid_receipt_clock"
    if received > at or received >= moment(row["kickoff"]) - timedelta(minutes=30):
        return "input_after_decision_or_T30"
    if max_age is not None and at - received > max_age:
        return "stale_price"
    # No API caller flag can upgrade a batch-start clock to HTTP completion.
    if row.get("timestamp_basis") in {"legacy_batch_start", "legacy_batch_start_not_HTTP_receipt"}:
        return "quote_completion_clock_unproven"
    return None


def unique_top(values):
    best = max(values.values())
    leaders = [side for side, value in values.items() if value == best]
    return leaders[0] if len(leaders) == 1 else None


def confirmed_baseball_lineup(row):
    p = row["payload"]
    if p.get("confirmed") is not True:
        return False
    all_ids = []
    for side in ("home", "away"):
        roster = p.get(side) or {}
        hitters, starter = roster.get("batting_order"), roster.get("starter")
        if (not isinstance(hitters, list) or len(hitters) != 9 or len(set(hitters)) != 9
                or any(not isinstance(i, str) or not i for i in hitters)
                or not isinstance(starter, str) or not starter):
            return False
        all_ids.append(set(hitters + [starter]))
    return not (all_ids[0] & all_ids[1])


def price_vector(row):
    p = row["payload"]
    if p.get("period") != "full" or p.get("market") not in {"winner_2way", "winner_3way"} or p.get("line") is not None:
        raise ValueError("non_full_game_winner_product")
    sides = {"home", "away"} if p["market"] == "winner_2way" else {"home", "draw", "away"}
    if not p.get("product_id") or set(p["odds"]) != sides:
        raise ValueError("incomplete_price_roster")
    odds = {s: numeric(v) for s, v in p["odds"].items()}
    if any(v <= 1 for v in odds.values()):
        raise ValueError("invalid_decimal_odds")
    from .devig import shin
    ordered = [s for s in ("home", "draw", "away") if s in sides]
    values = shin([float(odds[s]) for s in ordered])
    probabilities = {s: numeric(round(v, 4)) for s, v in zip(ordered, values)}
    total = sum(probabilities.values())
    return p, odds, {s: v/total for s, v in probabilities.items()}


def choose(rows):
    """Freeze all choices before any score enters grading."""
    prices = []
    rejected = Counter()
    kickoff = moment(rows[0]["kickoff"])
    for row in rows:
        if row["kind"] != "price":
            continue
        try:
            at = moment(row["received_at"])
            reason = pregame(row, at, max_age=timedelta(hours=24))
            if at < kickoff - timedelta(hours=24):
                reason = "quote_older_than_24h"
            if reason:
                rejected[reason] += 1
                continue
            product, odds, probabilities = price_vector(row)
            prices.append((at, row, product, odds, probabilities))
        except (ValueError, KeyError, TypeError):
            rejected["invalid_price"] += 1
    if not prices:
        return None, {"price": dict(rejected) or {"missing_price": 1}}
    latest = max(p[0] for p in prices)
    candidates = [p for p in prices if p[0] == latest]
    if len({digest(p[2]) for p in candidates}) != 1:
        return None, {"price": {"conflicting_latest_prices": 1}}
    at, price, product, odds, market = min(candidates, key=lambda p: p[1]["record_id"])
    voters = defaultdict(list)
    reasons = defaultdict(Counter)
    for row in rows:
        if row["kind"] != "tipster":
            continue
        reason = pregame(row, at)
        p = row["payload"]
        if (p.get("period") != "full" or p.get("market") != product["market"] or p.get("line") is not None
                or p.get("selection") not in odds or not p.get("author") or not p.get("raw_text")):
            reason = "invalid_vote_product_author_or_original_text"
        if reason:
            reasons["vote"][reason] += 1
        else:
            voters[p["author"]].append(row)
    votes = Counter()
    accepted_votes = []
    for author, revisions in sorted(voters.items()):
        first = min(moment(r["received_at"]) for r in revisions)
        earliest = [r for r in revisions if moment(r["received_at"]) == first]
        if len({r["payload"]["selection"] for r in earliest}) != 1:
            reasons["vote"]["conflicting_first_author_votes"] += 1
            continue
        row = min(earliest, key=lambda r: r["record_id"])
        accepted_votes.append(row["record_id"])
        votes[row["payload"]["selection"]] += 1
    picks = {"market": unique_top(market), "vote": None, "market90_vote10": None}
    if votes:
        picks["vote"] = unique_top({s: votes[s] for s in odds})
        picks["market90_vote10"] = unique_top({s: Decimal(".9")*market[s] + Decimal(".1")*votes[s]/sum(votes.values()) for s in odds})
        if picks["vote"] is None:
            reasons["vote"]["tied_votes"] += 1
    else:
        reasons["vote"]["missing_eligible_votes"] += 1
    scoped = {m for m, scope in MODEL_SCOPES.items() if scope == (rows[0]["sport"], rows[0]["league"])}
    by_id = {r["record_id"]: r for r in rows}
    model_predictions = defaultdict(list)
    for row in rows:
        if row["kind"] != "model_prediction":
            continue
        p = row["payload"]
        model = p.get("model_id")
        if model not in scoped:
            reasons[str(model)]["model_not_applicable_to_league"] += 1
            continue
        reason = pregame(row, at)
        try:
            if moment(p["train_end"]) >= moment(p["as_of"]) or moment(p["as_of"]) > at:
                reason = "model_training_or_forecast_clock_leaks"
            if p.get("product_id") != product["product_id"] or not SHA.fullmatch(str(p.get("artifact_sha256", ""))):
                reason = "model_product_or_artifact_mismatch"
            ids = p.get("input_record_ids")
            if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
                reason = "missing_or_duplicate_model_input_lineage"
                inputs = []
            else:
                inputs = [by_id.get(i) for i in ids]
                if any(r is None or r["kind"] not in {"lineup", "player_history"} or pregame(r, moment(p["as_of"])) for r in inputs):
                    reason = "model_input_not_verified_pregame"
            if not any(r and r["kind"] == "lineup" and confirmed_baseball_lineup(r) for r in inputs):
                reason = "missing_confirmed_lineup"
            for r in inputs:
                if r and r["kind"] == "player_history":
                    last = r["payload"].get("latest_source_kickoff")
                    if last is None or moment(last) >= moment(row["kickoff"]).astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0):
                        reason = "same_day_future_or_unbounded_player_history"
            probs = {s: numeric(v) for s, v in p["probabilities"].items()}
            if set(probs) != set(odds) or abs(sum(probs.values())-1) > Decimal(".0002") or any(not 0<=v<=1 for v in probs.values()):
                reason = "model_probability_vector_mismatch"
            if reason:
                reasons[model][reason] += 1
            else:
                model_predictions[model].append((moment(p["as_of"]), row["record_id"], unique_top(probs)))
        except (KeyError, ValueError, TypeError):
            reasons[model]["invalid_saved_model_forecast"] += 1
    for model in scoped:
        eligible = model_predictions[model]
        if not eligible:
            reasons[model]["missing_eligible_model_forecast"] += 1
            picks[model] = None
            continue
        last = max(p[0] for p in eligible)
        final = [p for p in eligible if p[0] == last]
        picks[model] = final[0][2] if len({p[2] for p in final}) == 1 else None
        if picks[model] is None:
            reasons[model]["tied_or_conflicting_model_forecast"] += 1
    return dict(as_of=at.isoformat(), price_record_id=price["record_id"], product=product,
                odds={s: str(v) for s, v in odds.items()}, picks=picks,
                vote_record_ids=accepted_votes, input_record_ids=sorted(by_id)), {m: dict(c) for m, c in reasons.items()}


def result_for(rows, chosen, evaluated_at):
    labels = []
    for row in rows:
        if row["kind"] != "result":
            continue
        p = row["payload"]
        url = urlsplit(row.get("source_url", ""))
        if (url.scheme != "https" or url.hostname not in OFFICIAL_HOSTS.get(row["league"], {"www.betman.co.kr"})
                or row.get("receipt_verified") is not True):
            continue
        try:
            received = moment(row["received_at"])
            if not moment(row["kickoff"]) <= received <= evaluated_at:
                continue
            if any(p.get(k) != chosen["product"].get(k) for k in ("product_id", "period", "market", "line", "settlement_rule")):
                continue
            labels.append((received, p))
        except (ValueError, KeyError, TypeError):
            continue
    if not labels:
        return None
    latest = max(r[0] for r in labels)
    selected = [p for at, p in labels if at == latest]
    if len({digest(p) for p in selected}) != 1:
        return None
    p = selected[0]
    if (p.get("confirmed") is not True or type(p.get("home_score")) is not int
            or type(p.get("away_score")) is not int or min(p["home_score"], p["away_score"]) < 0):
        return None
    return p


def metrics(events, model, stake=10000):
    rows = []
    for event in events:
        side = event["picks"].get(model)
        if side is None or not event.get("result"):
            continue
        result, product = event["result"], event["product"]
        h, a = result["home_score"], result["away_score"]
        winner = "home" if h > a else "away" if h < a else "draw"
        void = winner == "draw" and product["market"] == "winner_2way"
        if void and product.get("settlement_rule") != "draw_void_refund":
            continue
        hit = not void and side == winner
        price = numeric(event["odds"][side])
        pnl = Decimal(0) if void else (price-1)*stake if hit else -Decimal(stake)
        rows.append(dict(fixture_id=event["fixture_id"], selection=side, odds=str(price), hit=hit, void=void, pnl=str(pnl)))
    total = sum((numeric(r["pnl"]) for r in rows), Decimal(0))
    settled = sum(not r["void"] for r in rows)
    return dict(bets=len(rows), decisive=settled, hit=sum(r["hit"] for r in rows), void=sum(r["void"] for r in rows),
                accuracy_percent=100*sum(r["hit"] for r in rows)/settled if settled else None,
                staked=stake*len(rows), net=str(total), roi_percent=float(100*total/(stake*len(rows))) if rows else None, rows=rows)


def compare(records, *, start, end, evaluated_at):
    start, end, evaluated_at = moment(start), moment(end), moment(evaluated_at)
    if not start < end or end > evaluated_at:
        raise ValueError("invalid_evaluation_window")
    groups, malformed = defaultdict(list), Counter()
    ids = {}
    for row in records:
        try:
            key = physical(row)
            if not start <= moment(row["kickoff"]) < end:
                continue
            rid = row["record_id"]
            if not isinstance(rid, str) or not rid:
                raise ValueError("invalid_record_id")
            if rid in ids and ids[rid] != digest(row):
                raise ValueError("conflicting_record_id")
            if rid in ids:
                continue
            ids[rid] = digest(row)
            groups[key].append(row)
        except (KeyError, TypeError, ValueError) as exc:
            if str(exc) == "conflicting_record_id":
                raise
            malformed[str(exc)] += 1
    events, blocked, availability = [], [], defaultdict(Counter)
    # All outcome-free choices are made before result_for is invoked.
    for key, rows in sorted(groups.items()):
        selected, reasons = choose(rows)
        group = "/".join(key[:2])
        for row in rows:
            availability[group][row["kind"]] += 1
        identity = hashlib.sha256(canonical(key)).hexdigest()
        if selected is None:
            blocked.append(dict(fixture_id=identity, sport=key[0], league=key[1], kickoff=key[2], reasons=reasons))
            continue
        events.append(dict(selected, fixture_id=identity, sport=key[0], league=key[1], kickoff=key[2],
                           home=key[3], away=key[4], reasons=reasons))
    indexed = {hashlib.sha256(canonical(k)).hexdigest(): v for k, v in groups.items()}
    for event in events:
        event["result"] = result_for(indexed[event["fixture_id"]], event, evaluated_at)
    summary = {}
    for group in sorted(availability):
        sport, league = group.split("/", 1)
        rows = [e for e in events if (e["sport"], e["league"]) == (sport, league)]
        models = (*CONTROLS, *(m for m, scope in MODEL_SCOPES.items() if scope == (sport, league)))
        all_common = [e for e in rows if e["result"] is not None and all(e["picks"].get(m) is not None for m in models)]
        control_common = [e for e in rows if e["result"] is not None and all(e["picks"].get(m) is not None for m in CONTROLS)]
        # No winner if a player arm is missing or the model is not implemented.
        complete_player_suite = any(m not in CONTROLS for m in models) and bool(all_common)
        yearly = {}
        for year in sorted({moment(e["kickoff"]).astimezone(KST).year for e in rows}):
            pool = [e for e in all_common if moment(e["kickoff"]).astimezone(KST).year == year]
            yearly[str(year)] = {m: metrics(pool, m) for m in models}
        by_year = defaultdict(Counter)
        for key, members in groups.items():
            if key[:2] == (sport, league):
                year = str(moment(key[2]).astimezone(KST).year)
                for r in members:
                    by_year[year][r["kind"] + ":" + r.get("timing_basis", "unknown")] += 1
        scored = {m: metrics(all_common if complete_player_suite else [], m) for m in models}
        def best(metric):
            eligible = {m: v[metric] for m, v in scored.items() if v[metric] is not None}
            return [m for m, v in eligible.items() if v == max(eligible.values())] if eligible else []
        summary[group] = dict(available_records=dict(availability[group]), records_by_year=dict(by_year), models=list(models),
                             unavailable_scope=[m for m in ("kbo_m0", "kbo_m1", "kbo_m2") if MODEL_SCOPES[m] != (sport, league)],
                             all_models_common_n=len(all_common) if complete_player_suite else 0,
                             all_models=scored,
                             controls_common_n=len(control_common), controls={m: metrics(control_common, m) for m in CONTROLS},
                             yearly=yearly if complete_player_suite else {},
                             descriptive_best_accuracy=best("accuracy_percent"), descriptive_best_roi=best("roi_percent"),
                             best_model=None,
                             conclusion="incomplete_player_suite" if not complete_player_suite else "retrospective_comparison_not_promotion")
    return dict(version=VERSION, period=dict(start=start.isoformat(), end=end.isoformat()),
                evaluated_at=evaluated_at.isoformat(), received_record_count=len(ids), physical_fixtures=len(groups),
                invalid_records=dict(malformed), summary=summary, events=events, blocked=blocked,
                limits=["Local body/receipt checks are not independent time certification.",
                        "Same full-game winner product only; O/U and handicap require separate result contracts.",
                        "No live policy, production store, schedule or monthly paper wallet is changed."])
