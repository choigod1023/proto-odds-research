"""손실 축소 등급표 — 이 프로젝트의 최종 산출물.

방향 전환 (2026-07-28)
----------------------
"시장을 이긴다"는 목표는 **포기했다.** 근거:

    필요한 우위      6.8%p
    정보의 크기      2.4%p   (샤프 마켓 자신의 24h 스윙)
    구조 선택 이득   2.4%p   (규칙 누적의 최대)
    문헌상 최고 프로 +2~5%   (프로토 마진 12% 의 절반도 안 된다)

`문헌_상한.md` 가 결정적이다 — 세계 최고 엣지를 그대로 가져와도 프로토에서는 −7% 다.
**우리 모델이 부족한 게 아니라 이 시장은 아무도 못 이긴다.**

그래서 목표를 바꾼다: **덜 잃는 것.** 이건 예측이 아니라 **가격 사실**이라 확정적이다.

무엇이 확정됐나
---------------
배당대별 수익률은 **2023~2026 네 해 모두 단조**다. 오늘 세 번 속은
(KBL 가짜승리 · FA컵 R1 · 승①패 중간) 것들과 달리 **부호가 뒤집히지 않는다.**

    1.0-1.3   −9.23%   ← 가장 덜 나쁘다
    1.3-1.5  −10.59%
    1.5-1.8  −11.86%
    1.8-2.2  −12.77%
    2.2-3.0  −14.13%
    3.0-5.0  −15.22%
    5.0+     −33.49%   ← 절대 금지

전체 평균이 −13.89% 이므로 **저배당만 골라도 4.7%p** 를 아낀다.

⚠️ 여전히 마이너스다. 이건 **이기는 도구가 아니라 덜 지는 도구**다.
같은 돈으로 더 오래 논다. 그 이상을 약속하면 거짓말이다.

산출물: docs/data/loss_grades.json (사이트가 읽는다)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stack_filter import build                          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "loss_grades.json"

BINS = [1, 1.3, 1.5, 1.8, 2.2, 3.0, 5.0, 999]
LABELS = ["1.0-1.3", "1.3-1.5", "1.5-1.8", "1.8-2.2", "2.2-3.0", "3.0-5.0", "5.0+"]

# 등급 경계 — 실측 ROI 기준. 전체 평균이 −13.9% 이므로 그보다 나으면 의미가 있다.
def grade(roi: float) -> str:
    if roi >= -0.10:
        return "A"      # 가장 덜 나쁨
    if roi >= -0.12:
        return "B"
    if roi >= -0.15:
        return "C"
    return "D"          # 피할 것


def main() -> int:
    d = build()
    d["bin"] = pd.cut(d["odds"], BINS, labels=LABELS)
    years = sorted(d["year"].unique())

    # --- 배당대 등급 (연도별 안정성 포함)
    odds_rows = []
    for b in LABELS:
        s = d[d["bin"] == b]
        if len(s) < 500:
            continue
        by_year = {int(y): round(float(x["ret"].mean()), 4) for y, x in s.groupby("year")}
        signs = [v < 0 for v in by_year.values()]
        odds_rows.append({
            "bin": b, "n": int(len(s)),
            "roi": round(float(s["ret"].mean()), 4),
            "base": round(float(s["base"].mean()), 4),
            "edge": round(float(s["edge"].mean()), 4),
            "grade": grade(float(s["ret"].mean())),
            "by_year": by_year,
            "stable": bool(all(signs) or not any(signs)),
        })

    # --- 구조(booking) 등급
    st_rows = []
    for bk, s in d.groupby("booking"):
        if len(s) < 500:
            continue
        st_rows.append({"booking": bk, "n": int(len(s)),
                        "roi": round(float(s["ret"].mean()), 4),
                        "payout": round(float((1 + s["base"]).mean()) * 100, 2),
                        "grade": grade(float(s["ret"].mean()))})
    st_rows.sort(key=lambda r: -r["roi"])

    # --- 3-way 선택지 등급 (중간이 덜 나쁘다)
    sel_rows = []
    for (fam, sel), s in d[d["n_way"] == 3].groupby(["fam", "sel"]):
        if len(s) < 500:
            continue
        sel_rows.append({"fam": fam, "sel": sel, "n": int(len(s)),
                         "roi": round(float(s["ret"].mean()), 4),
                         "edge": round(float(s["edge"].mean()), 4),
                         "grade": grade(float(s["ret"].mean()))})
    sel_rows.sort(key=lambda r: -r["roi"])

    overall = float(d["ret"].mean())
    best = float(d[d["odds"] < 1.3]["ret"].mean())
    doc = {
        "generated_at": pd.Timestamp.now("UTC").isoformat(timespec="seconds"),
        "basis": {"n_selections": int(len(d)), "n_games": int(d["gid"].nunique()),
                  "years": [int(y) for y in years]},
        "headline": {
            "overall_roi": round(overall, 4),
            "best_bucket_roi": round(best, 4),
            "saving_pp": round((best - overall) * 100, 2),
        },
        "note": ("이기는 도구가 아니라 덜 지는 도구다. 모든 구간이 마이너스이며, "
                 "가장 좋은 구간도 −9%대다. 프로토 마진(12%)은 세계 최고 수준의 "
                 "베팅 엣지(+2~5%)보다 크다."),
        "odds_bins": odds_rows,
        "structures": st_rows,
        "three_way_selections": sel_rows,
        "rules": [
            {"rule": "배당 5.0 이상 금지", "why": "−33.5%. 다른 어떤 구간보다 20%p 나쁘다"},
            {"rule": "배당 1.5 미만 우선", "why": "−10.0%. 전체 평균보다 3.9%p 낫다"},
            {"rule": "2-way 우선", "why": "환급률 88% (3-way 87% · 3-way핸디캡 86%)"},
            {"rule": "3-way 는 '중간' 선택지", "why": "승①패 중간이 기준선 대비 +4.7%"},
            {"rule": "조합 금지 · 단폴만", "why": "마진이 곱해진다 (2폴 77% · 4폴 60%)"},
            {"rule": "회차 환급률 확인", "why": "회차마다 86~89% 로 다르다"},
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"선택지 {len(d):,} · 경기 {d['gid'].nunique():,} · {years[0]}~{years[-1]}")
    print(f"\n전체 ROI {overall:+.2%}  →  최선 구간(배당<1.3) {best:+.2%}  "
          f"= {(best - overall) * 100:+.2f}%p 절감\n")
    print(f"{'배당대':<10}{'n':>8}{'ROI':>9}{'초과':>9}  등급  연도별 안정")
    print("-" * 60)
    for r in odds_rows:
        print(f"{r['bin']:<10}{r['n']:>8,}{r['roi']:>+9.2%}{r['edge']:>+9.2%}"
              f"   {r['grade']}    {'O' if r['stable'] else 'X'}")
    print(f"\n저장: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
