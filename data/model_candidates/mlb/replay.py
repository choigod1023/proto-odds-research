"""Portable offline parity/period report; settle only AFTER blind choices exist."""
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "src"))
from mlb_frozen_candidate import infer_mlb_candidate, load_mlb_artifacts, predict_features


def settle(selection, fixture):
    labels = fixture["labels"]
    hg, ag = labels["hg"], labels["ag"]
    if hg == ag:
        return dict(pick=selection, correct=0, push=1, net_krw=0)
    correct = (hg > ag) == (selection == "home")
    profit = (Decimal(10000) * (Decimal(str(fixture["odds"][selection])) - 1)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP)
    return dict(pick=selection, correct=int(correct), push=0, net_krw=int(profit) if correct else -10000)


def run():
    raw = (HERE / "parity-fixtures.jsonl.gz").read_bytes()
    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf8"))
    assert hashlib.sha256(raw).hexdigest() == manifest["fixture_sha256"]
    fixtures = [json.loads(line) for line in gzip.decompress(raw).decode().splitlines()]
    bundles = {year: load_mlb_artifacts(year) for year in (2025, 2026)}
    choices = []
    for fixture in fixtures:
        model, priors = bundles[int(fixture["snapshot"]["start_at"][:4])]
        result = infer_mlb_candidate(fixture["snapshot"], decision_at=fixture["decision_at"],
                                    matchup=fixture["matchup"], odds=fixture["odds"], model=model, prior_tables=priors)
        assert result["status"] == "available", result["reasons"]
        choices.append(result)
    # All inference has completed. Only now read expected predictions and labels.
    feature_error = probability_error = fixed_feature_error = 0.
    decision_matches = money_matches = 0
    observations = []
    for fixture, result in zip(fixtures, choices):
        expected, labels = fixture["expected"], fixture["labels"]
        feature_error = max(feature_error, max(abs(a-b) for a,b in zip(result["features"], expected["features"])))
        probability_error = max(probability_error, abs(result["probability_home"] - expected["probability_home"]))
        model = bundles[int(fixture["snapshot"]["start_at"][:4])][0]["payload"]["models"][expected["model_stage"]]
        fixed = predict_features(expected["features"], model)
        fixed_feature_error = max(fixed_feature_error, abs(fixed - expected["probability_home"]))
        settled = settle(result["selection"], fixture)
        decision_matches += int(settled["pick"] == labels["ledger"]["primary"]["pick"])
        money_matches += int(settled == labels["ledger"]["primary"])
        observations.append(dict(labels=labels, result=result, settled=settled))
    assert feature_error <= 1e-12 and probability_error <= 1e-12 and fixed_feature_error <= 1e-12
    assert decision_matches == money_matches == len(fixtures)

    def metrics(group, policy):
        ledgers = [r["settled"] if policy == "primary" else r["labels"]["ledger"][policy] for r in group]
        probabilities = [r["result"]["probability_home"] if policy == "primary" else
                         r["labels"]["probabilities"][policy] for r in group]
        bankroll = peak = drawdown = 0
        for ledger in ledgers:
            bankroll += ledger["net_krw"]
            peak = max(peak, bankroll)
            drawdown = max(drawdown, peak - bankroll)
        n = len(group)
        ys = [int(r["labels"]["hg"] > r["labels"]["ag"]) for r in group]
        return dict(n=n, correct=sum(r["correct"] for r in ledgers), pushes=sum(r["push"] for r in ledgers),
                    net_krw=bankroll, roi=bankroll/(10000*n), max_drawdown_krw=drawdown,
                    changed_picks=sum(r["settled"]["pick"] != r["labels"]["ledger"]["baseline"]["pick"] for r in group),
                    logloss=sum(-y*math.log(max(p, 1e-12))-(1-y)*math.log(max(1-p, 1e-12))
                                for y,p in zip(ys,probabilities))/n,
                    brier=sum((p-y)**2 for p,y in zip(probabilities,ys))/n)

    groups = {}
    for cohort, split in (("old116", "2026-07-01"), ("external230", "2026-08-10")):
        rs = [r for r in observations if r["labels"]["cohort"] == cohort]
        periods = dict(all=rs, before_split=[r for r in rs if r["labels"]["date"] < split],
                       split_onward=[r for r in rs if r["labels"]["date"] >= split])
        for month in sorted({r["labels"]["date"][:7] for r in rs}):
            periods["month_"+month] = [r for r in rs if r["labels"]["date"].startswith(month)]
        for period, prs in periods.items():
            if prs:
                groups[cohort+"/"+period] = {policy: metrics(prs, policy) for policy in ("primary", "baseline", "market_only")}
    report = dict(version="mlb-candidate-bridge-parity-v1", accepted=len(fixtures), excluded=len(manifest["excluded"]),
        excluded_reasons=dict(Counter(r["reason"] for r in manifest["excluded"])),
        artifacts=manifest["artifacts"], fixture_sha256=manifest["fixture_sha256"],
        max_abs_feature_error=feature_error, max_abs_probability_error=probability_error,
        max_abs_fixed_feature_probability_error=fixed_feature_error,
        decision_matches=decision_matches, settlement_money_matches=money_matches,
        model_stages=dict(Counter(r["model_stage"] for r in choices)),
        operational_model_reproduced=False, live_capture_validated=False,
        missing_feed_start_evidence=sum(not r["feed_start_verified"] for r in choices),
        missing_verified_quote_time=sum(not r["odds_time_verified"] for r in choices),
        baseline_kinds=dict(Counter(f["labels"]["baseline_kind"] for f in fixtures)), groups=groups)
    (HERE / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf8", newline="\n")
    print(json.dumps({k:v for k,v in report.items() if k != "groups"}))
    for key, value in groups.items():
        if key.endswith(("/all", "/before_split", "/split_onward")):
            print(json.dumps({key: {p: {k: m[k] for k in ("n", "correct", "net_krw", "roi")} for p,m in value.items()}}))
    return report


if __name__ == "__main__":
    run()
