"""표본 축소 감사 — 오늘 네 번 속은 패턴을 자동으로 잡는다.

왜 필요한가
-----------
2026-07-28 하루에 **가짜 발견을 네 번** 만들었다. 전부 같은 구조였다.

| 가짜 | 겉보기 | 실제 | 원인 |
|---|---|---|---|
| KBL '승무패' | ROI +30.05% | 프로토 우위 | 결과 `⑤` 32% 가 조용히 버려짐 |
| FA컵 R1 | 라인 4/4 상위팀 | ROI −7.22% | 선택 효과 + 순환논리 |
| 승①패 중간 | 검증 −3.85% | 2024년 −16.52% | 단일 연도 현상 |
| 마켓 정합성 | ROI +12.48% | −8.21% | 무승부 2,343건 제외 |

**공통 패턴: 표본이 갑자기 줄어드는데 결과가 좋아진다.**
그리고 줄어드는 이유가 **결과값과 상관있으면** 반드시 가짜다.
높은 배당의 근거인 위험을 지우고 배당만 취하는 셈이기 때문이다.

Bonferroni·부트스트랩·시간분리를 다 통과해도 이건 안 잡힌다.
**통계 관문은 표본이 올바르다는 전제 위에서만 작동한다.**

쓰는 법
-------
    from guard import check_slice
    check_slice(parent_df, child_df, "승①패 중간만",
                outcome_col="won", ret_col="ret", year_col="year")

무엇을 보나
-----------
1. **표본 잔존율** — 많이 줄면 경고
2. **결과 개선폭** — 좋아지면 경고 (줄었는데 좋아지는 게 위험 신호)
3. ⭐ **버려진 행의 결과 분포** — 이게 핵심이다.
   버려진 쪽의 적중률이 남은 쪽과 다르면 **결과값에 따라 걸러진 것**이다.
4. **연도별 부호 일관성** — 한 해만 좋으면 우연이다
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def check_slice(parent: pd.DataFrame, child: pd.DataFrame, label: str,
                outcome_col: str = "won", ret_col: str = "ret",
                year_col: str | None = "year", verbose: bool = True) -> dict:
    """부분집합이 '표본 축소 + 결과 개선' 함정에 빠졌는지 검사한다.

    반환: {"verdict": "OK"|"WARN"|"FAIL", "reasons": [...], ...}
    """
    out: dict = {"label": label, "n_parent": len(parent), "n_child": len(child)}
    reasons: list[str] = []
    if len(parent) == 0 or len(child) == 0:
        out.update(verdict="FAIL", reasons=["표본 0"])
        return out

    keep = len(child) / len(parent)
    out["keep_ratio"] = keep

    # --- 버려진 행
    idx = parent.index.difference(child.index)
    dropped = parent.loc[idx]
    out["n_dropped"] = len(dropped)

    # 1) 결과 개선폭
    if ret_col in parent.columns and ret_col in child.columns:
        r_p, r_c = float(parent[ret_col].mean()), float(child[ret_col].mean())
        out["roi_parent"], out["roi_child"] = r_p, r_c
        out["roi_gain"] = r_c - r_p
        if keep < 0.5 and (r_c - r_p) > 0.02:
            reasons.append(f"표본 {keep:.0%} 로 줄었는데 ROI 가 {(r_c-r_p)*100:+.1f}%p 좋아졌다")

    # 2) ⭐ 버려진 행의 결과 분포 — 결과값 기반 누락 탐지
    if outcome_col in parent.columns and len(dropped):
        w_c = float(child[outcome_col].mean())
        w_d = float(dropped[outcome_col].mean())
        out["win_child"], out["win_dropped"] = w_c, w_d
        # 이항 표준오차로 정규화
        se = np.sqrt(max(w_c * (1 - w_c), 1e-9) / len(child)
                     + max(w_d * (1 - w_d), 1e-9) / len(dropped))
        z = (w_c - w_d) / se if se > 0 else 0.0
        out["z_outcome"] = float(z)
        if abs(z) > 3:
            reasons.append(
                f"🔴 버려진 행의 적중률이 다르다 (남은 {w_c:.1%} vs 버린 {w_d:.1%}, z={z:+.1f}) "
                f"— **결과값에 따라 걸러졌다.** 거의 확실히 가짜다")
        elif abs(z) > 2:
            reasons.append(f"버려진 행 적중률 차이 z={z:+.1f} — 결과 기반 누락 의심")

    # 3) 연도별 부호 일관성
    if year_col and year_col in child.columns and ret_col in child.columns:
        by = child.groupby(year_col)[ret_col].agg(["mean", "size"])
        by = by[by["size"] >= 30]
        if len(by) >= 2:
            out["by_year"] = {int(k): round(float(v), 4) for k, v in by["mean"].items()}
            signs = set(np.sign(by["mean"].values))
            if len(signs) > 1:
                reasons.append(
                    f"연도별 부호가 뒤집힌다 {out['by_year']} — 특정 연도 현상일 수 있다")

    out["reasons"] = reasons
    out["verdict"] = ("FAIL" if any("🔴" in r for r in reasons)
                      else "WARN" if reasons else "OK")

    if verbose:
        mark = {"OK": "✅", "WARN": "⚠️", "FAIL": "🔴"}[out["verdict"]]
        print(f"{mark} [{label}] 표본 {len(parent):,} → {len(child):,} ({keep:.1%})"
              + (f" · ROI {out['roi_parent']:+.2%} → {out['roi_child']:+.2%}"
                 if "roi_child" in out else ""))
        for r in reasons:
            print(f"      · {r}")
    return out


def self_test() -> None:
    """오늘의 가짜 넷 중 하나를 재현해 가드가 잡는지 확인한다."""
    rng = np.random.default_rng(0)
    n = 3000
    # 부모: 3-way. 중간 결과가 1/3
    outcome = rng.choice([0, 1, 2], size=n, p=[0.34, 0.33, 0.33])
    df = pd.DataFrame({
        "won": (outcome == 0).astype(int),
        "ret": np.where(outcome == 0, 2.0, -1.0),
        "year": rng.choice([2023, 2024, 2025, 2026], n),
        "outcome": outcome,
    })
    # 자식: '중간(1)' 결과를 통째로 버린다 = KBL 승⑤패 버그 재현
    child = df[df["outcome"] != 1]
    print("자기검사 — KBL 승⑤패 버그(결과값 기반 누락) 재현")
    r = check_slice(df, child, "중간 결과 누락")
    print(f"  → 판정 {r['verdict']} (FAIL 이어야 정상)\n")


if __name__ == "__main__":
    self_test()
