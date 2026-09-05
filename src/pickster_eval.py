"""공개 픽스터 기록의 성과·성향을 과장 없이 요약한다.

두 결과를 분리한다.

1. ``leaderboard_cross_section``: TailSlips 최근 30일 화면의 단면. 이미 잘한 사람을
   위에서 본 선택 편향이 있으므로 아이디어 탐색용이다.
2. ``prospective_validation``: 이 프로젝트가 결과가 나기 **전에** 처음 관측한 픽만.
   실제 판정과 모델 입력에는 이 표본만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from runtime_db import RuntimeDatabase, database_enabled, load_document, persist_document

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "picksters"
LEADERBOARD_LOG = RAW / "tailslips_leaderboard.jsonl"
PICK_LOG = RAW / "tailslips_pick_events.jsonl"
STATE = RAW / "_state.json"
OUT = ROOT / "data" / "processed" / "pickster_eval.json"
FINDING = ROOT / "findings" / "픽스터_공개픽_전향검증.md"


def _jsonl(path: Path) -> list[dict]:
    if database_enabled():
        stream = {LEADERBOARD_LOG: "pickster_leaderboard",
                  PICK_LOG: "pickster_pick_events"}[path]
        return RuntimeDatabase().events(stream)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def _wilson(wins: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = wins / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(100 * (center - half), 2), round(100 * (center + half), 2)


def _rank(values: list[float]) -> list[float]:
    """동점은 평균 순위로 처리하는 작은 Spearman 보조 함수."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        avg = (i + j - 1) / 2 + 1
        for k in order[i:j]:
            ranks[k] = avg
        i = j
    return ranks


def _corr(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or len(x) != len(y):
        return None
    mx, my = statistics.fmean(x), statistics.fmean(y)
    sx = sum((v - mx) ** 2 for v in x)
    sy = sum((v - my) ** 2 for v in y)
    if sx == 0 or sy == 0:
        return None
    return round(sum((a - mx) * (b - my) for a, b in zip(x, y)) / math.sqrt(sx * sy), 3)


def _leaderboard() -> dict:
    snapshots = _jsonl(LEADERBOARD_LOG)
    if not snapshots:
        return {"available": False, "reason": "leaderboard snapshot 없음"}
    snap = snapshots[-1]
    rows = [r for r in snap.get("rows", []) if r.get("n_picks") and r.get("roi_pct") is not None]
    if not rows:
        return {"available": False, "reason": "유효 행 없음"}
    rois = [float(r["roi_pct"]) for r in rows]
    wins = [float(r["win_rate_pct"]) for r in rows if r.get("win_rate_pct") is not None]
    ns = [int(r["n_picks"]) for r in rows]
    units = [float(r["net_units"]) for r in rows if r.get("net_units") is not None]
    # 각 capper 화면 ROI = 순익 / risked이므로 risked를 역산한다. 반올림 오차는 있다.
    risked = []
    for r in rows:
        roi = float(r["roi_pct"])
        net = float(r["net_units"])
        if roi and net / roi > 0:
            risked.append(net / (roi / 100))
    aggregate_roi = round(100 * sum(units) / sum(risked), 2) if risked and sum(risked) else None

    enriched = []
    for r in rows:
        n = int(r["n_picks"])
        win_pct = float(r.get("win_rate_pct") or 0)
        approx_wins = round(n * win_pct / 100)
        lo, hi = _wilson(approx_wins, n)
        # 사전분포가 아니라 단순 안정성 표시다. ROI 순위를 곧이곧대로 읽지 않기 위한
        # 보수적 수축이며 모델 변수로 사용하지 않는다.
        shrunk = float(r["roi_pct"]) * n / (n + 100)
        flags = []
        if n < 30:
            flags.append("very_small_sample")
        elif n < 100:
            flags.append("small_sample")
        if abs(float(r["roi_pct"])) >= 25 and n < 100:
            flags.append("extreme_roi_small_n")
        if int(r.get("deleted_count") or 0) > 0:
            flags.append("deleted_pick_indicator")
        enriched.append({
            "rank": r.get("rank"), "handle": r.get("handle"), "name": r.get("name"),
            "n_picks": n, "win_rate_pct": win_pct, "win_rate_wilson_95_pct": [lo, hi],
            "net_units": r.get("net_units"), "roi_pct": r.get("roi_pct"),
            "stability_shrunk_roi_pct": round(shrunk, 2), "trait": r.get("trait"),
            "offers_paid_picks": bool(r.get("offers_paid_picks")),
            "deleted_count": r.get("deleted_count", 0), "flags": flags,
        })
    stable = sorted(enriched, key=lambda r: r["stability_shrunk_roi_pct"], reverse=True)[:10]

    logn = [math.log1p(n) for n in ns]
    roi_ranks = _rank(rois)
    n_ranks = _rank(logn)
    deleted = [r for r in rows if int(r.get("deleted_count") or 0) > 0]
    clean = [r for r in rows if int(r.get("deleted_count") or 0) == 0]
    paid = [r for r in rows if r.get("offers_paid_picks")]
    public_only = [r for r in rows if not r.get("offers_paid_picks")]
    traits: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("trait"):
            trait = str(r["trait"]).lower()
            normalized = next((name for name in ("moneyline", "parlay", "prop", "spread", "total")
                               if name in trait), trait)
            traits[normalized].append(r)
    return {
        "available": True, "observed_at": snap.get("observed_at"),
        "window": snap.get("window", "last_30_days"), "n_ranked_cappers": len(rows),
        "total_displayed_picks": sum(ns), "median_picks_per_capper": _median(ns),
        "median_win_rate_pct": _median(wins), "median_roi_pct": _median(rois),
        "share_positive_roi_pct": round(100 * sum(x > 0 for x in rois) / len(rois), 2),
        "aggregate_roi_from_displayed_units_pct": aggregate_roi,
        "spearman_log_sample_vs_roi": _corr(n_ranks, roi_ranks),
        "deleted_indicator": {
            "n_with_deleted_indicator": len(deleted),
            "median_roi_with_indicator_pct": _median([float(r["roi_pct"]) for r in deleted]),
            "median_roi_without_indicator_pct": _median([float(r["roi_pct"]) for r in clean]),
        },
        "paid_pick_offer_indicator": {
            "n_offering_paid_picks": len(paid),
            "median_roi_offering_paid_pct": _median([float(r["roi_pct"]) for r in paid]),
            "median_roi_public_only_pct": _median([float(r["roi_pct"]) for r in public_only]),
            "warning": "공개 픽만 등급화된 표식이며 유료 픽 품질을 측정하지 않는다.",
        },
        "market_style_groups": {
            name: {"n_cappers": len(group),
                   "median_roi_pct": _median([float(r["roi_pct"]) for r in group]),
                   "median_win_rate_pct": _median([float(r["win_rate_pct"]) for r in group])}
            for name, group in sorted(traits.items())
        },
        "top_by_stability_shrunk_roi": stable,
        "interpretation": [
            "최근 30일 상위/활성 화면의 단면이며 전체 픽스터 모집단 성과가 아니다.",
            "win rate는 배당별 손익분기점이 달라 ROI를 대신할 수 없다.",
            "stability_shrunk_roi는 표본 경고용 휴리스틱이며 미래 수익 예측치가 아니다.",
        ],
    }


def _risked(row: dict) -> float | None:
    result, net, odds = row.get("result"), row.get("net_units"), row.get("american_odds")
    if net is None:
        return None
    net = float(net)
    if result == "L" and net < 0:
        return abs(net)
    if result == "W" and net > 0 and odds:
        return net * 100 / odds if odds > 0 else net * abs(odds) / 100
    return None


def _latest_picks() -> dict[str, dict]:
    latest: dict[str, dict] = {}
    eligibility: dict[str, bool] = {}
    for event in _jsonl(PICK_LOG):
        # v1은 score/status가 바뀔 때 pick_id도 바뀌어 전향 결과 연결에 쓸 수 없다.
        if event.get("identity_version") != 2:
            continue
        pid = event.get("pick_id")
        if not pid:
            continue
        if event.get("event_type") in {"baseline", "first_observed"}:
            eligibility[pid] = bool(event.get("eligible_pre_event"))
        latest[pid] = event
    for pid, row in latest.items():
        row["eligible_pre_event"] = eligibility.get(pid, bool(row.get("eligible_pre_event")))
    return latest


def _slate_profiles(latest: dict[str, dict]) -> dict:
    """현재 화면에 보인 선택 성향. baseline도 허용하지만 성과 주장에는 안 쓴다."""
    current_ids = None
    state = load_document("pickster_state", STATE)
    if state is not None:
        current_ids = set(state.get("current_pick_ids") or [])
    elif database_enabled():
        # Without a current slate, historical picks are not evidence of visibility.
        current_ids = set()
    visible = [r for pid, r in latest.items() if current_ids is None or pid in current_ids]
    straight = [r for r in visible if not r.get("is_parlay_leg")]
    markets = Counter(r.get("market_type") or "other" for r in straight)
    priced = [r for r in straight if r.get("american_odds")]
    favorite = sum(int(r["american_odds"]) < 0 for r in priced)
    underdog = sum(int(r["american_odds"]) > 0 for r in priced)
    by_capper: dict[str, list[dict]] = defaultdict(list)
    for row in straight:
        by_capper[row.get("handle") or row.get("slug") or "unknown"].append(row)
    profiles = []
    for handle, rows in by_capper.items():
        if len(rows) < 3:
            continue
        mc = Counter(r.get("market_type") or "other" for r in rows)
        odds = [int(r["american_odds"]) for r in rows if r.get("american_odds")]
        fav_share = sum(x < 0 for x in odds) / len(odds) if odds else None
        profiles.append({
            "handle": handle, "n_visible_straight_picks": len(rows),
            "dominant_market": mc.most_common(1)[0][0],
            "market_counts": dict(mc),
            "favorite_share_pct": round(100 * fav_share, 1) if fav_share is not None else None,
            "median_american_odds": _median(odds),
        })
    return {
        "n_visible_unique_picks": len(visible), "n_straight_picks": len(straight),
        "market_counts": dict(markets), "n_with_american_odds": len(priced),
        "favorite_share_pct": round(100 * favorite / len(priced), 2) if priced else None,
        "underdog_share_pct": round(100 * underdog / len(priced), 2) if priced else None,
        "capper_profiles_min_3_visible": sorted(
            profiles, key=lambda r: r["n_visible_straight_picks"], reverse=True
        )[:30],
        "warning": "현재 슬레이트에 보인 선택 구성이다. baseline 결과로 성공률을 주장하지 않는다.",
    }


def _prospective(latest: dict[str, dict]) -> dict:
    eligible = [r for r in latest.values() if r.get("eligible_pre_event") and not r.get("is_parlay_leg")]
    graded = [r for r in eligible if r.get("result") in {"W", "L", "P", "V"}]
    decisive = [r for r in graded if r.get("result") in {"W", "L"}]
    risks = [(r, _risked(r)) for r in graded]
    risked = sum(v for _, v in risks if v is not None)
    net = sum(float(r["net_units"]) for r, v in risks if v is not None and r.get("net_units") is not None)

    by_capper: dict[str, list[dict]] = defaultdict(list)
    for r in decisive:
        by_capper[r.get("handle") or r.get("slug") or "unknown"].append(r)
    capper_results = []
    for handle, rows in by_capper.items():
        wins = sum(r["result"] == "W" for r in rows)
        rr = [(r, _risked(r)) for r in rows]
        den = sum(v for _, v in rr if v is not None)
        pnl = sum(float(r["net_units"]) for r, v in rr if v is not None and r.get("net_units") is not None)
        capper_results.append({
            "handle": handle, "n_graded": len(rows), "wins": wins,
            "win_rate_pct": round(100 * wins / len(rows), 2),
            "roi_pct": round(100 * pnl / den, 2) if den else None,
            "net_units": round(pnl, 3),
        })

    # 직관적 군중 신호: 같은 경기의 full-game ML을 3명 이상이 골랐을 때 다수결.
    games: dict[str, list[dict]] = defaultdict(list)
    for r in decisive:
        selection = (r.get("selection") or "").upper()
        if r.get("market_type") == "moneyline" and "F5" not in selection:
            side = selection.split()[0] if selection else ""
            if side in {r.get("team_a"), r.get("team_b")}:
                games[r.get("game_header") or "unknown"].append(r)
    consensus = []
    for game, rows in games.items():
        if len(rows) < 3:
            continue
        sides = Counter((r.get("selection") or "").split()[0].upper() for r in rows)
        if not sides:
            continue
        (side, count), *rest = sides.most_common()
        if rest and count == rest[0][1]:
            continue
        side_rows = [r for r in rows if (r.get("selection") or "").split()[0].upper() == side]
        won = sum(r["result"] == "W" for r in side_rows) > sum(r["result"] == "L" for r in side_rows)
        consensus.append({"game_header": game, "majority_side": side,
                          "n_cappers": count, "won": won})

    wins = sum(r.get("result") == "W" for r in decisive)
    return {
        "n_eligible_pre_event": len(eligible), "n_graded": len(graded),
        "n_decisive": len(decisive), "wins": wins,
        "win_rate_pct": round(100 * wins / len(decisive), 2) if decisive else None,
        "net_units": round(net, 3), "inferred_risked_units": round(risked, 3),
        "roi_pct": round(100 * net / risked, 2) if risked else None,
        "n_consensus_games": len(consensus),
        "consensus_win_rate_pct": round(100 * sum(x["won"] for x in consensus) / len(consensus), 2)
        if consensus else None,
        "capper_results_minimum_not_applied": sorted(
            capper_results, key=lambda r: (r["n_graded"], r["net_units"]), reverse=True
        ),
        "status": "표본 축적 중" if len(decisive) < 100 else "초기 판정 가능",
        "eligibility_rule": "baseline 제외 + 결과 미정일 때 최초 관측 + parlay leg 제외",
    }


def build() -> dict:
    latest = _latest_picks()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "TailSlips public HTML (public X MLB picks)",
        "leaderboard_cross_section": _leaderboard(),
        "visible_slate_characteristics": _slate_profiles(latest),
        "prospective_validation": _prospective(latest),
        "decision_use": {
            "raw_capper_roi_as_model_feature": False,
            "allowed_after_minimum_sample": "전향 표본 100픽 이상에서도 crowd-vs-market offset이 개선될 때만",
            "suggested_features": [
                "moneyline capper count share", "독립 capper 수", "capper 의견 엔트로피",
                "고표본 capper와 전체 군중의 방향 일치", "픽 공개 후 시장확률 변화(CLV)",
            ],
        },
    }


def _fmt(x: object, suffix: str = "") -> str:
    return "—" if x is None else f"{x}{suffix}"


def write_outputs(result: dict) -> None:
    persist_document("processed_pickster_eval", result, OUT, indent=2)
    l = result["leaderboard_cross_section"]
    p = result["prospective_validation"]
    v = result["visible_slate_characteristics"]
    top = l.get("top_by_stability_shrunk_roi", [])[:5]
    lines = [
        "# 공개 픽스터 픽: 단면과 전향 검증을 분리한다", "",
        f"생성: `{result['generated_at']}`", "",
        "## 지금 화면에서 확인되는 것", "",
        f"- 최근 30일 리더보드: **{l.get('n_ranked_cappers', 0)}명**, 표시 픽 "
        f"**{l.get('total_displayed_picks', 0):,}건**",
        f"- 픽스터 중앙값: 적중률 **{_fmt(l.get('median_win_rate_pct'), '%')}**, "
        f"ROI **{_fmt(l.get('median_roi_pct'), '%')}**, 표본 **{_fmt(l.get('median_picks_per_capper'))}픽**",
        f"- 양(+) ROI 비중: **{_fmt(l.get('share_positive_roi_pct'), '%')}**; "
        f"화면 표시 순익/ROI 역산 합산 ROI: **{_fmt(l.get('aggregate_roi_from_displayed_units_pct'), '%')}**",
        f"- 현재 슬레이트 고유 픽 **{v.get('n_visible_unique_picks', 0)}건**; "
        f"가격이 읽힌 직선 픽 중 정배 비중 **{_fmt(v.get('favorite_share_pct'), '%')}**",
        "",
        "> 이 숫자는 전체 픽스터 평균이 아니다. 최근 30일 활성/상위 화면을 본 단면이라 "
        "잘한 사람을 사후 선택하는 편향과 소표본 극단값이 들어 있다.", "",
        "## 표본을 줄여 본 상위권(설명용)", "",
        "| @handle | n | 적중률 | 원 ROI | 안정성 수축 ROI | 95% 적중률 구간 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in top:
        lo, hi = r["win_rate_wilson_95_pct"]
        lines.append(
            f"| @{r['handle']} | {r['n_picks']} | {r['win_rate_pct']:.1f}% | "
            f"{r['roi_pct']:.1f}% | {r['stability_shrunk_roi_pct']:.1f}% | {lo:.1f}–{hi:.1f}% |"
        )
    lines += [
        "", "`안정성 수축 ROI = 원 ROI × n/(n+100)`은 과장 경고용 휴리스틱이다. "
        "예측치나 베팅 근거가 아니다.", "",
        "## 우리가 실제로 판정할 표본", "",
        f"- 경기 전 최초 관측 자격 픽: **{p['n_eligible_pre_event']}건**",
        f"- 판정 완료: **{p['n_decisive']}건**, 적중률 **{_fmt(p['win_rate_pct'], '%')}**, "
        f"ROI **{_fmt(p['roi_pct'], '%')}**, 순익 **{p['net_units']}u**",
        f"- 군중 다수결 판정 경기: **{p['n_consensus_games']}경기**, 적중률 "
        f"**{_fmt(p['consensus_win_rate_pct'], '%')}**",
        f"- 상태: **{p['status']}**", "",
        "첫 실행에 이미 끝난 477개 픽은 baseline이다. 성공률 계산에서 제외했다. "
        "이후 결과 미정일 때 처음 본 직선 픽만 들어간다.", "",
        "## 판정에 넣는 방법", "",
        "정배 픽스터 수가 많다는 이유만으로 확률을 더 올리지 않는다. 먼저 시장확률을 기준으로 "
        "`logit(p_model) = logit(p_market) + β·X_crowd`를 시간분리 학습하고, "
        "Brier/log-loss와 calibration이 동시에 좋아질 때만 β를 유지한다. 넣을 후보는 군중 점유율, "
        "독립 픽스터 수, 의견 엔트로피, 고표본군과 전체군의 일치, 공개 뒤 CLV다. 개선이 없으면 "
        "픽스터 쏠림은 예측 신호가 아니라 ‘이미 가격에 반영된 서사’로만 표시한다.", "",
    ]
    report = "\n".join(lines)
    if database_enabled():
        RuntimeDatabase().put_document("pickster_eval_report", {
            "generated_at": result.get("generated_at"), "markdown": report,
        })
    else:
        FINDING.parent.mkdir(parents=True, exist_ok=True)
        FINDING.write_text(report, encoding="utf-8")


def _selftest() -> None:
    assert _wilson(55, 100)[0] < 55 < _wilson(55, 100)[1]
    assert math.isclose(_risked({"result": "L", "net_units": -2, "american_odds": -110}) or 0, 2)
    assert math.isclose(_risked({"result": "W", "net_units": .91, "american_odds": -110}) or 0, 1.001)
    assert _corr([1, 2, 3], [3, 2, 1]) == -1.0
    print("✅ pickster_eval selftest 통과")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return 0
    result = build()
    write_outputs(result)
    print(json.dumps({
        "leaderboard_cappers": result["leaderboard_cross_section"].get("n_ranked_cappers", 0),
        "eligible": result["prospective_validation"]["n_eligible_pre_event"],
        "graded": result["prospective_validation"]["n_decisive"],
        "output": str(OUT),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
