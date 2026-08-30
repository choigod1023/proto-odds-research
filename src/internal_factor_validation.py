"""야구 내부요인 계수의 시간순 외부검증과 자동 shadow 판정."""
from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import datetime
from typing import Iterable

LEAGUES = ("MLB", "KBO", "NPB")
MIN_FUTURE_SAMPLE = 300


def _log_loss(probability: float, outcome: int) -> float:
    p = min(.999999, max(.000001, probability))
    return -(outcome * math.log(p) + (1 - outcome) * math.log(1 - p))


def _metrics(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0, "brier": None, "log_loss": None, "roi": None, "coverage": 0.0}
    market_bets = [row for row in rows if float(row.get("odds") or 0) > 1]
    return {
        "n": len(rows),
        "brier": sum((row["probability"] - row["outcome"]) ** 2 for row in rows) / len(rows),
        "log_loss": sum(_log_loss(row["probability"], row["outcome"]) for row in rows) / len(rows),
        "roi": (sum((float(row["odds"]) - 1) if row["outcome"] else -1 for row in market_bets) / len(market_bets)) if market_bets else None,
        "coverage": len(market_bets) / len(rows),
    }


def _ci(values: list[float]) -> list[float | None]:
    if not values:
        return [None, None]
    ordered = sorted(values)
    return [ordered[int(.025 * (len(ordered) - 1))], ordered[int(.975 * (len(ordered) - 1))]]


def validate_internal_factors(rows: Iterable[dict], *, bootstrap: int = 1000, seed: int = 17) -> dict:
    """사전 순서를 보존한 후반 40%만 평가하고 개선 CI가 0 아래일 때만 승격한다."""
    grouped = defaultdict(list)
    for raw in rows:
        league = str(raw.get("league") or "").upper()
        if league not in LEAGUES:
            continue
        try:
            item = {**raw, "market_probability": float(raw["market_probability"]),
                    "internal_probability": float(raw["internal_probability"]),
                    "outcome": int(raw["outcome"]), "observed_at": str(raw["observed_at"])}
            datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            continue
        if item["outcome"] not in (0, 1):
            continue
        grouped[league].append(item)
    report = {"schema": "internal-factor-rolling-validation-v1", "leagues": {}}
    for league in LEAGUES:
        ordered = sorted(grouped[league], key=lambda row: row["observed_at"])
        split = max(1, int(len(ordered) * .6)) if ordered else 0
        future = ordered[split:]
        market_rows = [{**row, "probability": row["market_probability"]} for row in future]
        candidate_rows = [{**row, "probability": row["internal_probability"]} for row in future]
        market, candidate = _metrics(market_rows), _metrics(candidate_rows)
        rng = random.Random(f"{seed}:{league}")
        brier_delta, log_delta = [], []
        if future:
            for _ in range(max(0, bootstrap)):
                sample = [future[rng.randrange(len(future))] for _ in future]
                brier_delta.append(sum((r["internal_probability"] - r["outcome"]) ** 2 - (r["market_probability"] - r["outcome"]) ** 2 for r in sample) / len(sample))
                log_delta.append(sum(_log_loss(r["internal_probability"], r["outcome"]) - _log_loss(r["market_probability"], r["outcome"]) for r in sample) / len(sample))
        segments = defaultdict(int)
        for row in future:
            market_favorite = row["market_probability"] >= .5
            internal_favorite = row["internal_probability"] >= .5
            if market_favorite != internal_favorite:
                segments["underdog_flip"] += 1
            elif abs(row["internal_probability"] - .5) > abs(row["market_probability"] - .5):
                segments["favorite_strengthened"] += 1
            else:
                segments["favorite_weakened"] += 1
        brier_ci, log_ci = _ci(brier_delta), _ci(log_delta)
        promoted = (len(future) >= MIN_FUTURE_SAMPLE and brier_ci[1] is not None
                    and brier_ci[1] < 0 and log_ci[1] < 0
                    and candidate["coverage"] >= market["coverage"])
        report["leagues"][league] = {
            "cutoff": ordered[split - 1]["observed_at"] if split else None,
            "future_sample": len(future), "market": market, "candidate": candidate,
            "brier_delta_ci95": brier_ci, "log_loss_delta_ci95": log_ci,
            "segments": dict(segments), "status": "promoted" if promoted else "shadow_only",
            "reason": None if promoted else "future sample or bootstrap improvement gate not passed",
        }
    return report


__all__ = ["validate_internal_factors", "MIN_FUTURE_SAMPLE"]
