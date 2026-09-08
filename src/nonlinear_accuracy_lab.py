"""Offline per-league CatBoost residuals and unchanged recommendation policy."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import platform
import subprocess
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
GROUP = ["sport", "league", "market", "n_way"]
STRATEGIES = ("market", "catboost_market", "catboost_team", "validated_team")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-8, 1 - 1e-8)
    return np.log(p / (1 - p))


def sigmoid(x):
    return 1 / (1 + np.exp(-np.clip(x, -30, 30)))


def binary_loss(y, p):
    p = np.clip(np.asarray(p, dtype=float), 1e-8, 1 - 1e-8)
    y = np.asarray(y, dtype=float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def validate_protocol(p):
    if (p.get("schema") != "nonlinear-accuracy-v1" or
            p.get("production_allowed") is not False or
            tuple(p.get("strategies", [])) != STRATEGIES):
        raise ValueError("research-only protocol required")
    stamps = [pd.Timestamp(p[k]) for k in ("training_start", "inner_start",
        "selection_cutoff_exclusive", "evaluation_start", "evaluation_end")]
    if (not all(a < b for a, b in zip(stamps, stamps[1:])) or
            any(x.tzinfo is not None for x in stamps)):
        raise ValueError("strictly ordered naive KST dates required")
    for key in ("embargo_days", "training_window_days", "half_life_days",
                "minimum_training_rows", "minimum_training_class",
                "minimum_validation_rows", "minimum_validation_weeks",
                "iterations", "depth", "block_weeks", "bootstrap_repeats",
                "minimum_assessment_picks", "minimum_assessment_weeks"):
        if not isinstance(p[key], int) or isinstance(p[key], bool) or p[key] < 1:
            raise ValueError(f"invalid {key}")
    grid = p["blend_grid"]
    if (not grid or not all(isinstance(x, (int, float)) and math.isfinite(x)
                            and 0 <= x <= 1 for x in grid)
            or grid != sorted(set(grid)) or grid[0] != 0):
        raise ValueError("blend grid must start at zero and lie within [0,1]")
    for key in ("learning_rate", "l2_leaf_reg", "minimum_pick_retention",
                "minimum_day_retention", "minimum_matched_retention", "maximum_odds_drop"):
        if not math.isfinite(p[key]) or p[key] <= 0:
            raise ValueError(f"invalid {key}")
    if p["training_window_days"] <= p["embargo_days"]:
        raise ValueError("invalid training window")


def training_subset(frame, quarter_start, p):
    cutoff = pd.Timestamp(quarter_start) - pd.Timedelta(days=p["embargo_days"])
    lower = max(pd.Timestamp(p["training_start"]),
                cutoff - pd.Timedelta(days=p["training_window_days"]))
    return frame[(frame.kickoff >= lower) & (frame.kickoff < cutoff) &
                 frame.y.notna()].copy()


def fit_predict(train, test, columns, p, cutoff):
    # Optional research dependency; never imported by collector/runtime code.
    from catboost import CatBoostClassifier, Pool
    x_train = train[columns].to_numpy(dtype=np.float32)
    x_test = test[columns].to_numpy(dtype=np.float32)
    if np.isinf(x_train).any() or np.isinf(x_test).any():
        raise ValueError("infinite input feature")
    age = (pd.Timestamp(cutoff) - train.kickoff).dt.total_seconds().to_numpy() / 86400
    pool = Pool(x_train, label=train.y.to_numpy(dtype=int),
                baseline=logit(train.q), weight=np.exp2(-age / p["half_life_days"]),
                feature_names=columns)
    model = CatBoostClassifier(
        iterations=p["iterations"], depth=p["depth"],
        learning_rate=p["learning_rate"], l2_leaf_reg=p["l2_leaf_reg"],
        loss_function="Logloss", random_seed=p["seed"], thread_count=1,
        boosting_type="Ordered", has_time=True, bootstrap_type="No",
        random_strength=0, boost_from_average=False, nan_mode="Min",
        allow_writing_files=False, verbose=False)
    model.fit(pool)
    # Test pool has NO baseline. Add market logit exactly once.
    raw = model.predict(Pool(x_test, feature_names=columns), prediction_type="RawFormulaVal")
    probability = sigmoid(logit(test.q) + raw)
    if not np.isfinite(probability).all():
        raise ArithmeticError("invalid CatBoost probabilities")
    return probability


def calendar_resamples(start, end, p):
    first = pd.Timestamp(start).to_period("W-SUN").start_time
    last = pd.Timestamp(end).to_period("W-SUN").start_time
    n = int((last - first).days // 7) + 1
    width = min(p["block_weeks"], n)
    rng = np.random.default_rng(p["seed"])
    starts = rng.integers(0, n, size=(p["bootstrap_repeats"], math.ceil(n / width)))
    samples = ((starts[:, :, None] + np.arange(width)) % n).reshape(len(starts), -1)[:, :n]
    return first, n, samples


def select_blend(predicted, p):
    inner = predicted[(predicted.kickoff >= pd.Timestamp(p["inner_start"])) &
        (predicted.kickoff < pd.Timestamp(p["selection_cutoff_exclusive"])) &
        predicted.y.notna() & predicted.model_available]
    weeks = inner.kickoff.dt.to_period("W-SUN").nunique()
    info = {"rows": len(inner), "weeks": int(weeks), "alpha": 0.0,
            "reason": "insufficient_inner_validation"}
    if (len(inner) < p["minimum_validation_rows"] or
            weeks < p["minimum_validation_weeks"]):
        return info
    losses = np.column_stack([binary_loss(inner.y,
        (1 - a) * inner.q + a * inner.catboost_team) for a in p["blend_grid"]])
    means = losses.mean(axis=0)
    best = int(np.argmin(means))
    first, n, samples = calendar_resamples(p["inner_start"],
        pd.Timestamp(p["selection_cutoff_exclusive"]) - pd.Timedelta(days=1), p)
    ids = ((inner.kickoff.dt.normalize() - first).dt.days // 7).to_numpy(int)
    sums = np.bincount(ids, weights=losses[:, best], minlength=n)
    counts = np.bincount(ids, minlength=n)
    denominators = counts[samples].sum(axis=1)
    boot = sums[samples].sum(axis=1)[denominators > 0] / denominators[denominators > 0]
    se = float(np.std(boot, ddof=1)) if len(boot) > 1 else 0.0
    chosen = int(np.flatnonzero(means <= means[best] + se + 1e-12)[0])
    return {**info, "alpha": float(p["blend_grid"][chosen]),
        "reason": "one_standard_error", "mean_log_loss": [float(x) for x in means],
        "best_alpha": p["blend_grid"][best], "best_loss_standard_error": se}


def fit_group(job):
    frame, p, market_columns, team_columns = job
    frame = frame.sort_values(["kickoff", "row_id"]).reset_index(drop=True)
    first = pd.Timestamp(p["inner_start"]).to_period("Q")
    last = pd.Timestamp(p["evaluation_end"]).to_period("Q")
    outputs, folds = [], []
    for quarter in pd.period_range(first, last, freq="Q"):
        start, end = quarter.start_time, (quarter + 1).start_time
        test = frame[(frame.kickoff >= start) & (frame.kickoff < end) &
            (frame.kickoff < pd.Timestamp(p["evaluation_end"]) + pd.Timedelta(days=1))].copy()
        if test.empty:
            continue
        train = training_subset(frame, start, p)
        classes = train.y.value_counts()
        available = (len(train) >= p["minimum_training_rows"] and
            all(classes.get(c, 0) >= p["minimum_training_class"] for c in (0, 1)))
        test["catboost_market"] = test.q
        test["catboost_team"] = test.q
        test["model_available"] = available
        if available:
            test["catboost_market"] = fit_predict(train, test, market_columns, p, start)
            test["catboost_team"] = fit_predict(train, test, market_columns + team_columns, p, start)
        folds.append({"quarter": str(quarter), "train_rows": len(train),
            "test_rows": len(test), "available": bool(available),
            "train_max": train.kickoff.max().isoformat() if len(train) else None,
            "train_ids_sha256": digest(train.row_id.tolist())})
        outputs.append(test)
    key = [str(frame.iloc[0][k]) for k in GROUP]
    if not outputs:
        return pd.DataFrame(), {"group": key, "folds": folds}
    predicted = pd.concat(outputs, ignore_index=True)
    blend = select_blend(predicted, p)
    predicted["validated_team"] = ((1 - blend["alpha"]) * predicted.q
                                  + blend["alpha"] * predicted.catboost_team)
    evaluation = predicted[predicted.kickoff >= pd.Timestamp(p["evaluation_start"])].copy()
    return evaluation, {"group": key, "folds": folds, "blend": blend}


def policy_payload(frame, strategy):
    rows = []
    for row in frame.to_dict("records"):
        rows.append({
            "selection_id": row["row_id"], "event_key": row["event_key"],
            "sport": row["sport"], "league": row["league"],
            "kickoff_at": pd.Timestamp(row["kickoff"]).isoformat(timespec="seconds") + "+09:00",
            "market": row["market"], "market_label": row["market_label"],
            "sel": row["sel"], "odds": float(row["odds"]),
            "market_prob": float(row["q"]),
            "predicted_hit_prob": float(row["q"] if strategy == "market" else row[strategy]),
            "is_market_favorite": True, "n_way": int(row["n_way"]),
            "game_no": row["row_id"], "round": "research",
        })
    return rows


def select_policy(frame, strategy, node="node"):
    if frame.empty:
        return frame.copy(), frame.copy()
    run = subprocess.run([node, str(ROOT / "scripts/nonlinear_policy_bridge.mjs")],
        input=json.dumps(policy_payload(frame, strategy), ensure_ascii=False, allow_nan=False),
        capture_output=True, text=True, encoding="utf-8", check=True)
    result = json.loads(run.stdout)
    indexed = frame.set_index("row_id", drop=False)
    choices, highlights = result["event_choices"], result["highlighted"]
    if (len(set(choices)) != len(choices) or len(set(highlights)) != len(highlights)
            or not set(highlights) <= set(choices) or not set(choices) <= set(indexed.index)):
        raise ValueError("invalid policy response")
    return (indexed.loc[choices].reset_index(drop=True),
            indexed.loc[highlights].reset_index(drop=True))


def metrics(frame, strategy):
    settled = frame[frame.y.notna()]
    count, wins = len(settled), int(settled.y.sum())
    p = settled.q if strategy == "market" else settled[strategy]
    profits = frame.y.fillna(0) * frame.odds - frame.y.notna().astype(int)
    return {
        "picks": len(frame), "settled": count, "wins": wins, "losses": count - wins,
        "voids": int(frame.is_void.sum()), "hit_rate": wins / count if count else None,
        "days": int(frame.kickoff.dt.normalize().nunique()),
        "weeks": int(frame.kickoff.dt.to_period("W-SUN").nunique()),
        "settled_weeks": int(settled.kickoff.dt.to_period("W-SUN").nunique()),
        "average_odds": float(frame.odds.mean()) if len(frame) else None,
        "low_odds_share": float((frame.odds < 1.5).mean()) if len(frame) else None,
        "flat_single_roi": float(profits.mean()) if len(frame) else None,
        "brier": float(((p - settled.y) ** 2).mean()) if count else None,
        "log_loss": float(binary_loss(settled.y, p).mean()) if count else None,
        "mean_probability": float(p.mean()) if count else None,
        "selected_ids_sha256": digest(sorted(frame.row_id.tolist())),
    }


def matched_picks(candidate, reference_choices):
    # Neither labels nor model scores enter matching keys/reference ranking.
    keys = ["_day", "sport", "league", "market", "n_way", "_price_bin"]
    def keyed(frame):
        out = frame.copy()
        out["_day"] = out.kickoff.dt.normalize()
        out["_price_bin"] = np.floor(out.odds * 10 + 1e-8).astype(int)
        return out
    a, b = keyed(candidate), keyed(reference_choices)
    reference = dict(tuple(b.groupby(keys, sort=True)))
    left, right = [], []
    for key, rows in a.groupby(keys, sort=True):
        pool = reference.get(key)
        if pool is None:
            continue
        count = min(len(rows), len(pool))
        left.extend(rows.sort_values(["q", "row_id"], ascending=[False, True]).head(count).row_id)
        right.extend(pool.sort_values(["q", "row_id"], ascending=[False, True]).head(count).row_id)
    return (candidate.set_index("row_id", drop=False).loc[left].reset_index(drop=True),
            reference_choices.set_index("row_id", drop=False).loc[right].reset_index(drop=True))


def comparison(candidate, reference, p):
    first, n, samples = calendar_resamples(p["evaluation_start"], p["evaluation_end"], p)
    def totals(frame):
        settled = frame[frame.y.notna()]
        ids = ((settled.kickoff.dt.normalize() - first).dt.days // 7).to_numpy(int)
        return (np.bincount(ids, weights=settled.y, minlength=n),
                np.bincount(ids, minlength=n))
    aw, an = totals(candidate)
    bw, bn = totals(reference)
    if an.sum() == 0 or bn.sum() == 0:
        return {"delta": None, "ci95": None, "p": None}
    delta = float(aw.sum() / an.sum() - bw.sum() / bn.sum())
    ac, bc = an[samples].sum(axis=1), bn[samples].sum(axis=1)
    valid = (ac > 0) & (bc > 0)
    boot = aw[samples].sum(axis=1)[valid] / ac[valid] - bw[samples].sum(axis=1)[valid] / bc[valid]
    if not len(boot):
        return {"delta": delta, "ci95": None, "p": None}
    lo, hi = np.quantile(boot, [0.025, 0.975])
    prob = (1 + np.sum(np.abs(boot - delta) >= abs(delta) - 1e-12)) / (1 + len(boot))
    return {"delta": delta, "ci95": [float(lo), float(hi)], "p": float(prob)}


def holm(comparisons):
    finite = sorted((c for c in comparisons if c.get("p") is not None), key=lambda c: c["p"])
    previous = 0.0
    for i, row in enumerate(finite):
        previous = max(previous, min(1.0, row["p"] * (len(finite) - i)))
        row["holm_p"] = previous


def scope_summaries(frame, choices, selected, p):
    scopes = [("overall", "all", set(frame.row_id))]
    for column in ("sport", "league", "market"):
        columns = ["sport", "league"] if column == "league" else [column]
        for key, rows in frame.groupby(columns, sort=True):
            key = key if isinstance(key, tuple) else (key,)
            scopes.append((column, "|".join(map(str, key)), set(rows.row_id)))
    reports, comparisons = [], []
    for kind, key, ids in scopes:
        pools = {s: f[f.row_id.isin(ids)] for s, f in selected.items()}
        ref_choices = choices["market"][choices["market"].row_id.isin(ids)]
        stats = {s: metrics(f, s) for s, f in pools.items()}
        tests = []
        for strategy in STRATEGIES[1:]:
            candidate, baseline = pools[strategy], pools["market"]
            a, b = matched_picks(candidate, ref_choices)
            direct, matched = comparison(candidate, baseline, p), comparison(a, b, p)
            comparisons.extend([direct, matched])
            tests.append({"strategy": strategy, "policy": direct, "matched": matched,
                "matched_candidate": metrics(a, strategy), "matched_reference": metrics(b, "market"),
                "matched_retention": len(a) / len(candidate) if len(candidate) else 0.0})
        extra = comparison(pools["catboost_team"], pools["catboost_market"], p)
        comparisons.append(extra)
        reports.append({"scope": kind, "key": key, "metrics": stats,
            "by_year": {str(year): {s: metrics(f[f.kickoff.dt.year == year], s)
                for s, f in pools.items()} for year in sorted(frame.kickoff.dt.year.unique())},
            "comparisons": tests, "team_vs_market_features": extra})
    holm(comparisons)
    for report in reports:
        for comp in report["comparisons"]:
            a, b = report["metrics"][comp["strategy"]], report["metrics"]["market"]
            signal = all(c["ci95"] is not None and c["ci95"][0] > 0 and c.get("holm_p", 1) < .05
                         for c in (comp["policy"], comp["matched"]))
            signal &= all(m["settled"] >= p["minimum_assessment_picks"] and
                          m["settled_weeks"] >= p["minimum_assessment_weeks"]
                          for m in (a, b, comp["matched_candidate"], comp["matched_reference"]))
            signal &= (a["picks"] >= b["picks"] * p["minimum_pick_retention"] and
                a["days"] >= b["days"] * p["minimum_day_retention"] and
                comp["matched_retention"] >= p["minimum_matched_retention"] and
                a["average_odds"] is not None and b["average_odds"] is not None and
                a["average_odds"] >= b["average_odds"] - p["maximum_odds_drop"])
            signal &= all(year[comp["strategy"]]["hit_rate"] is not None and
                year["market"]["hit_rate"] is not None and
                year[comp["strategy"]]["hit_rate"] > year["market"]["hit_rate"]
                for year in report["by_year"].values())
            if comp["strategy"] == "catboost_team":
                extra = report["team_vs_market_features"]
                signal &= (extra["ci95"] is not None and extra["ci95"][0] > 0 and
                           extra.get("holm_p", 1) < .05)
            comp["historical_signal"] = bool(signal)
            comp["production_allowed"] = False
    return reports, len(comparisons)


def build_report(games, p, workers=2, node="node"):
    from nonlinear_features import load_research_rows, MARKET_FEATURES, TEAM_FEATURES
    import catboost
    frame, audit = load_research_rows(games)
    jobs = [(rows.copy(), p, list(MARKET_FEATURES), list(TEAM_FEATURES))
            for _, rows in frame.groupby(GROUP, sort=True)
            if ((rows.kickoff >= pd.Timestamp(p["evaluation_start"])) &
                (rows.kickoff < pd.Timestamp(p["evaluation_end"]) + pd.Timedelta(days=1))).any()]
    if workers == 1:
        results = list(map(fit_group, jobs))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = []
            for index, result in enumerate(pool.map(fit_group, jobs), 1):
                results.append(result)
                if index % 20 == 0 or index == len(jobs):
                    print(f"fit groups {index}/{len(jobs)}", file=sys.stderr, flush=True)
    frames = [f for f, _ in results if not f.empty]
    if not frames:
        raise ValueError("no evaluation rows")
    predictions = pd.concat(frames, ignore_index=True).sort_values(
        ["kickoff", "sport", "league", "row_id"]).reset_index(drop=True)
    if predictions.row_id.duplicated().any():
        raise ValueError("duplicate evaluation market")
    choices, selected = {}, {}
    for strategy in STRATEGIES:
        choices[strategy], selected[strategy] = select_policy(predictions, strategy, node)
    scopes, comparisons = scope_summaries(predictions, choices, selected, p)
    return {
        "schema": p["schema"], "production_allowed": False, "protocol": p,
        "environment": {"python": platform.python_version(), "numpy": np.__version__,
                        "pandas": pd.__version__, "catboost": catboost.__version__},
        "input_audit": audit, "evaluation_rows": len(predictions),
        "evaluation_events": int(predictions.event_key.nunique()),
        "available_model_rows": int(predictions.model_available.sum()),
        "all_candidate_metrics": {s: metrics(predictions, s) for s in STRATEGIES},
        "features": {"market": list(MARKET_FEATURES), "team": list(TEAM_FEATURES)},
        "fit_groups": [meta for _, meta in results], "scopes": scopes,
        "planned_comparisons": comparisons,
        "prediction_sha256": digest([[r.row_id, float(r.q), float(r.catboost_market),
            float(r.catboost_team), float(r.validated_team)] for r in predictions.itertuples()]),
    }


def markdown_report(report):
    def pct(v):
        return "표본 없음" if v is None else f"{v * 100:.2f}%"
    overall = next(s for s in report["scopes"] if s["scope"] == "overall")
    lines = ["# 리그별 비선형 모델 개선 실험", "", "## 판정", "",
        "운영 승격은 하지 않는다. 아래 수치는 과거 보관 데이터의 모의 추천이며 "
        "실제 사이트 사전 추천 원장 적중률이나 미래 성능 보장이 아니다.", "",
        f"평가: {report['protocol']['evaluation_start']} ~ {report['protocol']['evaluation_end']}; "
        f"{report['evaluation_events']:,}실경기 / {report['evaluation_rows']:,}최유력 시장 후보.",
        "", "## 기존 추천 조건을 적용한 전체 결과", ""]
    for strategy, m in overall["metrics"].items():
        lines.append(f"- {strategy}: **{m['wins']}/{m['settled']} = {pct(m['hit_rate'])}**, "
            f"추천 {m['picks']}개, 평균 배당 {m['average_odds']}, "
            f"동일 금액 단식 ROI {pct(m['flat_single_roi'])}.")
    lines += ["", "## 적중률 차이와 같은 수·가격대 대조", ""]
    for c in overall["comparisons"]:
        ci, delta = c["policy"]["ci95"], c["policy"]["delta"]
        ci_text = "표본 없음" if ci is None else f"[{ci[0]*100:+.2f}, {ci[1]*100:+.2f}]%p"
        delta_text = "표본 없음" if delta is None else f"{delta*100:+.2f}%p"
        a, b = c["matched_candidate"], c["matched_reference"]
        lines.append(f"- {c['strategy']}: 기준 대비 {delta_text}; 95% 구간 {ci_text}; "
            f"Holm p={c['policy'].get('holm_p')}. 매칭 {a['wins']}/{a['settled']} "
            f"대 {b['wins']}/{b['settled']}; 유지율 {pct(c['matched_retention'])}; "
            f"과거 개선 조건 통과: {c['historical_signal']}.")
    lines += ["", "## 리그별 결과", ""]
    for s in report["scopes"]:
        if s["scope"] == "league":
            m = s["metrics"]
            lines.append(f"- {s['key']}: 기준 {m['market']['wins']}/{m['market']['settled']} "
                f"({pct(m['market']['hit_rate'])}) → 팀 특징 "
                f"{m['catboost_team']['wins']}/{m['catboost_team']['settled']} "
                f"({pct(m['catboost_team']['hit_rate'])}); "
                f"과거 검증 혼합 {pct(m['validated_team']['hit_rate'])}.")
    lines += ["", "## 방법과 해석 제한", ""]
    lines += [f"- {x}" for x in report["protocol"]["limitations"]]
    lines += ["", "## 모델 문서", "",
        "- [CatBoost Pool baseline](https://catboost.ai/docs/en/concepts/python-reference_pool): "
        "배당 logit을 학습 시작값으로 사용하고 예측에는 한 번만 더한다.",
        "- [CatBoost 예측](https://catboost.ai/docs/en/concepts/python-reference_catboostclassifier_predict): "
        "RawFormulaVal 잔차로 후보 확률을 계산한다.",
        "", "## 재현 정보", "",
        f"- 예측 SHA-256: {report['prediction_sha256']}",
        f"- 원자료 SHA-256: {report.get('provenance', {}).get('games')}",
        "- 학습 시점·내부 혼합 선택·연도별 지표·프로토콜은 같은 이름의 JSON에 보존한다.",
        "- 실행 중 입력/코드 변경 시 저장하지 않으며 기존 출력은 덮어쓰지 않는다.",
        "", "서버 재시작, DB 쓰기, 운영 픽 변경, 병합 및 배포는 이 실험에 포함하지 않았다.", ""]
    return "\n".join(lines)


def provenance(games, protocol):
    paths = [Path(__file__), ROOT / "src/nonlinear_features.py", ROOT / "src/matches.py",
        ROOT / "src/devig.py", ROOT / "src/bets.py", ROOT / "scripts/nonlinear_policy_bridge.mjs"]
    paths += sorted((ROOT / "web/src/lib").glob("*.js"))
    return {"games": sha256(games), "protocol": sha256(protocol),
        "code": {p.relative_to(ROOT).as_posix(): sha256(p) for p in paths}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", required=True, type=Path)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=2, choices=range(1, 5))
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    output, md_path = args.output.resolve(), args.output.with_suffix(".md").resolve()
    if output.suffix != ".json":
        raise ValueError("output must end in .json; Markdown companion exported")
    for path in (output, md_path):
        if path.exists():
            raise FileExistsError(f"refuse to overwrite {path}")
    p = json.loads(args.protocol.read_text(encoding="utf-8"))
    validate_protocol(p)
    before = provenance(args.games, args.protocol)
    report = build_report(args.games, p, args.workers, args.node)
    if provenance(args.games, args.protocol) != before:
        raise RuntimeError("source/protocol/code changed during run")
    report["provenance"] = before
    encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    md = markdown_report(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
    with md_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(md)
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
