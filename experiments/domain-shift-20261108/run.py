"""Frozen late-2025 input-density reweighting; retrospective, not live ROI."""
from __future__ import annotations

import os
for _key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[_key] = "1"

import gzip
import hashlib
import json
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import softmax
from sklearn.linear_model import LogisticRegression

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
ALLOWED = {
    "bs": {"KBO", "NPB", "MLB"},
    "bk": {"NBA", "KBL", "WKBL"},
    "vl": {"KOVO남", "KOVO여"},
    "sc": {"K리그1", "K리그2", "J1리그", "J2리그", "EPL", "라리가", "세리에A",
           "프리그1", "분데스리", "MLS", "에레디비", "EFL챔", "A리그"},
}


def features(rows):
    market = np.array([r["market_p"] for r in rows], dtype=float)
    form = np.array([r["predictions"]["outcome_form"]["p"] for r in rows], dtype=float)
    market = market / market.sum(axis=1, keepdims=True)
    form = form / form.sum(axis=1, keepdims=True)
    market = np.clip(market, 1e-8, 1)
    form = np.clip(form, 1e-8, 1)
    logits = np.log(market[:, :-1] / market[:, -1:])
    form_logits = np.log(form[:, :-1] / form[:, -1:])
    entropy = -(market * np.log(market)).sum(axis=1)
    odds = np.array([r["odds"] for r in rows], dtype=float)
    overround = (1 / odds).sum(axis=1) - 1
    return np.column_stack([logits, form_logits, entropy, overround]), market


def normalize_weights(weights):
    w = np.clip(np.asarray(weights, dtype=float), .25, 4)
    return w / w.mean()


def margin_weights(market, late):
    """Jeffreys-smoothed late-quarter/source bin ratio; no evaluation data."""
    bins = np.minimum((market.max(axis=1) * 10).astype(int), 9)
    all_counts = np.bincount(bins, minlength=10) + .5
    late_counts = np.bincount(bins[late], minlength=10) + .5
    ratio = (late_counts / late_counts.sum()) / (all_counts / all_counts.sum())
    return normalize_weights(ratio[bins])


def fit_offset(x, market, y, weights):
    n, d = x.shape
    k = market.shape[1]
    target = np.eye(k)[y]
    offset = np.log(market)
    weights = normalize_weights(weights)

    def loss_grad(flat):
        b = flat.reshape(d, k - 1)
        logits = offset.copy()
        logits[:, :-1] += x @ b
        probs = softmax(logits, axis=1)
        loss = -np.mean(weights * np.log(np.maximum(probs[np.arange(n), y], 1e-12)))
        loss += .01 * np.sum(b * b) / 2
        grad = x.T @ ((probs - target)[:, :-1] * weights[:, None]) / n + .01 * b
        return loss, grad.ravel()

    fitted = minimize(loss_grad, np.zeros(d * (k - 1)), jac=True,
                      method="L-BFGS-B", options={"maxiter": 300, "ftol": 1e-11})
    if not fitted.success:
        raise RuntimeError(fitted.message)
    return fitted.x.reshape(d, k - 1), float(fitted.fun)


def predict(x, market, beta):
    logits = np.log(market).copy()
    logits[:, :-1] += x @ beta
    return softmax(logits, axis=1)


def settle(p, odds, y):
    pick = int(np.argmax(p))
    net = Decimal(str(odds[pick])) - 1 if pick == y else Decimal(-1)
    return pick, pick == y, str(net)


def metrics(rows, p):
    y = np.array([r["y"] for r in rows])
    pick = p.argmax(axis=1)
    hits = pick == y
    nets = np.array([float(settle(q, r["odds"], r["y"])[2]) for r, q in zip(rows, p)])
    ll = -np.log(np.maximum(p[np.arange(len(y)), y], 1e-12))
    brier = ((p - np.eye(p.shape[1])[y]) ** 2).sum(axis=1)
    cumulative = np.r_[0, nets.cumsum()]
    return {"n": len(y), "hits": int(hits.sum()), "accuracy": float(hits.mean()),
            "roi": float(nets.mean()), "logloss": float(ll.mean()),
            "brier": float(brier.mean()),
            "mdd_units": float((np.maximum.accumulate(cumulative) - cumulative).max())}


def paired(rows, p, baseline, replicates=1000):
    y = np.array([r["y"] for r in rows])
    delta = []
    for i, r in enumerate(rows):
        a, b = settle(p[i], r["odds"], r["y"]), settle(baseline[i], r["odds"], r["y"])
        delta.append([int(a[1]) - int(b[1]), float(a[2]) - float(b[2]),
                      -np.log(max(p[i, y[i]], 1e-12)) + np.log(max(baseline[i, y[i]], 1e-12))])
    week_keys = [date.fromisoformat(r["day"]).isocalendar()[:2] for r in rows]
    weeks = sorted(set(week_keys))
    delta = np.asarray(delta)
    sums = np.array([delta[np.array([w == key for w in week_keys])].sum(axis=0) for key in weeks])
    counts = np.array([week_keys.count(key) for key in weeks])
    rng = np.random.default_rng(20261108)
    draws = rng.integers(0, len(weeks), size=(replicates, len(weeks)))
    samples = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)[:, None]
    signs = rng.choice([-1, 1], size=(replicates, len(weeks)))
    null = signs @ sums / len(rows)
    observed = delta.mean(axis=0)
    p_values = ((np.abs(null) >= np.abs(observed)).sum(axis=0) + 1) / (replicates + 1)
    return {"n_weeks": len(weeks), "delta_accuracy_roi_ll": observed.tolist(),
            "nominal_95_ci": np.quantile(samples, [.025, .975], axis=0).T.tolist(),
            "paired_week_signflip_two_sided_p": p_values.tolist(),
            "changed_picks": int((p.argmax(axis=1) != baseline.argmax(axis=1)).sum())}


def main():
    summary = {"scope": "retrospective archived full-game winner only; no tipster/player input",
               "train": ["2025-01-01", "2025-12-27"],
               "late_input_target": ["2025-10-01", "2025-12-27"],
               "evaluation": ["2026-01-01", "2026-08-26"], "sports": {},
               "limitations": ["Prices lack proven HTTP receipt/executability and earliest outcome availability.",
                               "Four-day label-availability assumption; archived upstream form has its own historic protocol.",
                               "Covariate-shift conditional-invariance assumption is NOT established.",
                               "Two-way archives exclude original draws; no refund-inclusive operational replay.",
                               "Repeatedly inspected retrospective period; no prospective superiority claim.",
                               "Quarter-end target uses only 2025 inputs; no 2026 fitting or selection.",
                               "MDD is day/id-ordered virtual unit P&L, not settlement-time wallet exposure."]}
    with gzip.open(OUT / "predictions.jsonl.gz", "wt", encoding="utf8") as output:
        for sport, leagues in ALLOWED.items():
            path = ROOT / f"experiments/all-sports-underdog-20261031/results/{sport}/domestic_archive/ledger.jsonl.gz"
            data = [json.loads(line) for line in gzip.open(path, "rt", encoding="utf8")]
            exclusions = Counter()
            unique = {}
            for r in data:
                if r["league"] not in leagues:
                    exclusions["not_prespecified_league"] += 1
                    continue
                if r["fallback"]:
                    exclusions["upstream_form_fallback"] += 1
                    continue
                if r["id"] in unique:
                    raise ValueError(f"Duplicate event {r['id']}")
                unique[r["id"]] = r
            rows = sorted(unique.values(), key=lambda r: (r["day"], r["id"]))
            train = [r for r in rows if "2025-01-01" <= r["day"] <= "2025-12-27"]
            test = [r for r in rows if "2026-01-01" <= r["day"] <= "2026-08-26"]
            if len(train) < 100 or not test:
                summary["sports"][sport] = {"status": "insufficient", "n_train": len(train), "n_test": len(test)}
                continue
            x, market = features(train)
            xt, mt = features(test)
            mu, scale = x.mean(axis=0), np.maximum(x.std(axis=0), .01)
            x, xt = (x - mu) / scale, (xt - mu) / scale
            x, xt = np.c_[np.ones(len(x)), x], np.c_[np.ones(len(xt)), xt]
            late = np.array([r["day"] >= "2025-10-01" for r in train])
            density = LogisticRegression(C=.1, max_iter=500, random_state=20261108).fit(x[:, 1:], late)
            q = np.clip(density.predict_proba(x[:, 1:])[:, 1], 1e-6, 1 - 1e-6)
            # p(late|x) / p(late) estimates late-subset density / full-2025 density.
            w_density = normalize_weights(q / late.mean())
            weights = {"offset_control": np.ones(len(train)), "density_ratio_offset": w_density,
                       "margin_histogram_offset": margin_weights(market, late)}
            probs = {"market": mt}
            fitted = {}
            for name, w in weights.items():
                beta, objective = fit_offset(x, market, np.array([r["y"] for r in train]), w)
                probs[name] = predict(xt, mt, beta)
                fitted[name] = {"beta": beta.tolist(), "objective": objective,
                                "effective_sample_size": float(w.sum() ** 2 / (w @ w)),
                                "weight_min_max": [float(w.min()), float(w.max())]}
            sport_result = {"n_train": len(train), "n_late": int(late.sum()), "n_test": len(test),
                            "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "exclusions": dict(exclusions), "parameters": fitted, "models": {}}
            for name, p in probs.items():
                assert np.isfinite(p).all() and np.allclose(p.sum(axis=1), 1)
                model = {"all": metrics(test, p), "by_league": {}, "by_quarter": {}, "paired": {}}
                for field, key_fn in [("by_league", lambda r: r["league"]),
                                      ("by_quarter", lambda r: r["day"][:4] + "Q" + str((int(r["day"][5:7])-1)//3+1))]:
                    keys = [key_fn(r) for r in test]
                    for key in sorted(set(keys)):
                        mask = np.array([k == key for k in keys])
                        model[field][key] = metrics([r for r, yes in zip(test, mask) if yes], p[mask])
                if name != "market":
                    model["paired"]["market"] = paired(test, p, mt)
                if name not in {"market", "offset_control"}:
                    model["paired"]["offset_control"] = paired(test, p, probs["offset_control"])
                sport_result["models"][name] = model
                for r, q in zip(test, p):
                    pick, hit, net = settle(q, r["odds"], r["y"])
                    output.write(json.dumps({"event_id": r["id"], "day": r["day"], "sport": sport,
                        "league": r["league"], "y": r["y"], "odds": r["odds"],
                        "market_p": r["market_p"], "model": name, "p": q.tolist(),
                        "pick": pick, "hit": hit, "net": net}, ensure_ascii=False) + "\n")
            summary["sports"][sport] = sport_result
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
    print(json.dumps({s: {m: v["all"] for m, v in x.get("models", {}).items()}
                      for s, x in summary["sports"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
