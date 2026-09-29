"""Offline, fixed-split market-offset experiment. Never a production policy."""
from collections import Counter
from datetime import datetime, timedelta
import math

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import expit, logit


def instant(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return dt


def probability(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and 0 < value < 1)


def cohort(records, *, sport, version):
    """Choose revision without seeing outcomes; never fall back to a settled one."""
    latest, settlements, excluded = {}, {}, Counter()
    for row in records:
        if row.get("record_type") == "settlement":
            settlements.setdefault(row["snapshot_id"], []).append(row)
        elif row.get("record_type") == "prediction":
            try:
                observed, captured, kickoff = map(instant, (
                    row["as_of"], row["captured_at"], row["kickoff"]))
                if not observed <= captured < kickoff - timedelta(minutes=30):
                    raise ValueError("late")
            except (ValueError, KeyError, TypeError, AttributeError):
                excluded["invalid_or_late_revision"] += 1
                continue
            order = (observed, row.get("ledger_sequence", 0))
            key = row["event_id"]
            if key not in latest or order > latest[key][0]:
                latest[key] = order, row
    rows = []
    for _, row in latest.values():
        pred = row.get("predictions") or {}
        contract = (pred.get("score_forecast") or {}).get("contract") or {}
        if (contract.get("sport") != sport or pred.get("market") != "승패"
                or (row.get("model") or {}).get("residual_version") != version):
            excluded["outside_fixed_cohort"] += 1
            continue
        detail = pred.get("probability_detail") or {}
        market, model, odds = detail.get("market"), detail.get("ai_candidate"), pred.get("odds")
        if (not all(probability(p) for p in (market, model)) or isinstance(odds, bool)
                or not isinstance(odds, (int, float)) or not math.isfinite(odds) or odds <= 1):
            excluded["missing_probability_or_price"] += 1
            continue
        matches = settlements.get(row["snapshot_id"], [])
        if not matches:
            excluded["pending"] += 1
            continue
        try:
            outcomes = {(s.get("outcome") or {}).get("result") for s in matches}
            if len(outcomes) != 1 or not outcomes <= {"hit", "miss"}:
                raise ValueError("void/conflict")
            for s in matches:
                if (not pred.get("selection_id") or
                        s["outcome"].get("selection_id") != pred["selection_id"] or
                        (s.get("event_id") is not None and s["event_id"] != row["event_id"]) or not s.get("source") or
                        not str(s.get("settlement_version", "")).startswith("official-") or
                        not instant(row["kickoff"]) <= instant(s["settled_at"]) <= instant(s["captured_at"])):
                    raise ValueError("unproven settlement")
            # Corrections must also have been known before a training boundary.
            known = max(instant(s["captured_at"]) for s in matches)
        except (ValueError, KeyError, TypeError, AttributeError):
            excluded["invalid_or_conflicting_settlement"] += 1
            continue
        rows.append(dict(event_id=row["event_id"], kickoff=instant(row["kickoff"]),
                         captured=instant(row["captured_at"]), known=known,
                         market=market, model=model, odds=odds,
                         hit=int(outcomes == {"hit"})))
    return sorted(rows, key=lambda r: (r["captured"], r["event_id"])), dict(excluded)


def predict(rows, beta=0., intercept=0.):
    market = np.array([r["market"] for r in rows])
    model = np.array([r["model"] for r in rows])
    return expit(logit(market) + beta * (logit(model) - logit(market)) + intercept)


def loss(y, p):
    p = np.clip(p, 1e-12, 1-1e-12)
    return float(-np.mean(y*np.log(p) + (1-y)*np.log1p(-p)))


def fit(rows, *, beta=None):
    y = np.array([r["hit"] for r in rows])
    # Fixed regularization; do not tune on the final test period.
    def objective(x):
        p = predict(rows, x) if beta is None else predict(rows, beta, x)
        return loss(y, p) + .01*x*x
    result = minimize_scalar(objective, bounds=(0., 1.) if beta is None else (-1., 1.), method="bounded")
    if not result.success:
        raise ValueError("optimizer failed")
    choices = [0., float(result.x)]
    return min(choices, key=objective)


def metrics(rows, p):
    if not rows:
        return {"n": 0, "hit_rate": None, "realized_roi": None}
    y = np.array([r["hit"] for r in rows])
    return dict(n=len(rows), hit_rate=float(y.mean()),
                brier=float(np.mean((p-y)**2)), log_loss=loss(y, p),
                estimated_roi=float(np.mean(p*np.array([r["odds"] for r in rows])-1)),
                realized_roi=float(np.mean([r["hit"]*r["odds"]-1 for r in rows])))


def experiment(records, *, sport, version, train_end, calibration_end, test_end, minimum=100):
    a, b, c = map(instant, (train_end, calibration_end, test_end))
    if not a < b < c or minimum < 2:
        raise ValueError("ordered boundaries and minimum >= 2 required")
    rows, excluded = cohort(records, sport=sport, version=version)
    train = [r for r in rows if r["captured"] < a and r["known"] < a]
    calibration = [r for r in rows if a <= r["captured"] < b and r["known"] < b]
    test = [r for r in rows if b <= r["captured"] < c and r["known"] < c]
    report = dict(status="insufficient_data", promotion_allowed=False,
                  sport=sport, version=version, boundaries=[train_end, calibration_end, test_end],
                  minimum=minimum, eligible_settled=len(rows), excluded=excluded,
                  split_counts=dict(train=len(train), calibration=len(calibration), test=len(test)),
                  limitations=["Exploratory, not preregistered confirmation.",
                               "Source truth and executable prices require independent verification.",
                               "Selected historical picks only; not all-market selection or combo ROI.",
                               "No uncertainty interval or robust-positive-EV claim."])
    if any(len(part) < minimum or len({r["hit"] for r in part}) < 2 for part in (train, calibration)) or len(test) < minimum:
        return report
    beta = fit(train)
    # Calibrate both models equally, only on the separate middle period.
    market_intercept, intercept = fit(calibration, beta=0.), fit(calibration, beta=beta)
    market_p, candidate_p = predict(test, 0., market_intercept), predict(test, beta, intercept)
    report.update(status="exploratory_holdout", parameters=dict(beta=beta,
                  market_intercept=market_intercept, candidate_intercept=intercept),
                  all_test=dict(raw_market=metrics(test, predict(test)),
                                calibrated_market=metrics(test, market_p),
                                candidate=metrics(test, candidate_p)))
    # Fixed budget (top quarter), selected without outcomes. Both policies bet
    # the same number; this controls coverage, NOT statistical uncertainty.
    n = max(1, len(test)//4)
    report["equal_count_selection"] = {}
    for label, p in (("market", market_p), ("candidate", candidate_p)):
        ids = sorted(range(len(test)), key=lambda i: (-float(p[i]*test[i]["odds"]-1), test[i]["event_id"]))[:n]
        report["equal_count_selection"][label] = metrics([test[i] for i in ids], p[ids])
    return report
