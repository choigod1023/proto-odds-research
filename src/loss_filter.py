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
from runtime_db import persist_artifact

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stack_filter import WIN_IDX, build                  # noqa: E402
from matches import clean_team                          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data" / "loss_grades.json"

BINS = [1, 1.3, 1.5, 1.8, 2.2, 3.0, 5.0, 999]
LABELS = ["1.0-1.3", "1.3-1.5", "1.5-1.8", "1.8-2.2", "2.2-3.0", "3.0-5.0", "5.0+"]


def _bin_of(o: float) -> str:
    """배당 → 배당대 라벨. 화면 `fmt.js:gradeOf` 와 경계가 같아야 한다."""
    for i in range(len(LABELS)):
        if o < BINS[i + 1]:
            return LABELS[i]
    return LABELS[-1]

# 등급 경계 — 실측 ROI 기준. 전체 평균이 −13.9% 이므로 그보다 나으면 의미가 있다.
def grade(roi: float) -> str:
    if roi >= -0.10:
        return "A"      # 가장 덜 나쁨
    if roi >= -0.12:
        return "B"
    if roi >= -0.15:
        return "C"
    return "D"          # 피할 것


# ⚠️ 등급을 주기 전에 통과해야 하는 관문.
#    이게 없으면 "표본 작고 최근 몇 년만 있는 구간"이 A 등급을 받아 사이트에 나간다.
#    실제로 그랬다: 승①패 '1점차' 가 A(−6.54%) 였는데 연도별로 −1.8 / −14.4 / +0.1 / −12.6
#    (진폭 14.5%p, 2025 년엔 **양수**). 예전에 가짜로 판정했던 바로 그 구간이
#    등급표를 통해 되살아나 있었다. 전반핸디무(−0.43%)는 2026 년 한 해 n=198 이었다.
MIN_N = 500          # 표본 하한


def stability(s, base_by_year: dict[int, float]) -> tuple[dict, bool, str]:
    """연도별 ROI 와 '등급을 줘도 되는가' 판정.

    ⚠️ 절대 진폭으로 재면 안 된다. 해마다 시장 전체 수익률이 오르내리므로
       모든 구간이 같이 흔들린다. 실제로 그렇게 쟀더니 '배당 5.0 이상 금지'
       (매년 −26~−37%, 언제나 최악)가 탈락하고, 이 도구의 핵심 규칙이 사라졌다.

    올바른 질문은 **"그 해 평균 대비 방향이 매년 같은가"** 다.
    이게 곧 '내년에도 재현되는가' 이고, 등급이 주장하는 바로 그것이다.
    """
    by_year = {int(y): round(float(x["ret"].mean()), 4) for y, x in s.groupby("year")}
    if len(by_year) < len(base_by_year):
        missing = sorted(set(base_by_year) - set(by_year))
        return by_year, False, f"연도 부족({len(by_year)}/{len(base_by_year)}년, 없음: {missing})"
    diffs = {y: v - base_by_year[y] for y, v in by_year.items()}
    if not (all(x > 0 for x in diffs.values()) or all(x < 0 for x in diffs.values())):
        d = " ".join(f"{y}:{x*100:+.1f}" for y, x in sorted(diffs.items()))
        return by_year, False, f"그 해 평균 대비 방향이 뒤집힌다 ({d})"
    return by_year, True, ""


def odds_caps() -> list[dict]:
    """최저배당 상한별 — 경기를 버릴수록 적중률이 어떻게 오르나.

    ⚠️ 적중률을 올리는 지렛대는 '경기 안에서 뭘 고르나' 가 아니라
       **'어느 경기를 버리나'** 다. 실측: 전부 사면 65.86%, 최저배당 ≤1.3 인
       경기만 사면 **77.61%**. 그리고 이 축에서는 적중률과 ROI 가 **같이** 좋아진다
       (−10.25% → −9.55%). 지금까지 본 교환관계와 반대다.

    🔴 경기 키에 home 을 그대로 쓰면 안 된다. 형식이 마켓마다 다르다
       ("두산 12" vs "두산") — 같은 경기가 마켓별로 쪼개져 경기 수가 두 배가 된다.
       **이 경고를 적어두고도 당했다.** 정수만 벗기는 정규식을 써서 핸디캡 행의
       소수("맨체스C -1.5")를 못 벗겼고, 경기 키가 44,915 → 71,282 로 부풀었다
       (유령 26,367개). 그래서 정본 `matches.clean_team` 하나만 쓴다.
    """
    import re as _re
    g = pd.read_csv(ROOT / "data" / "processed" / "games.csv")
    g = g[(~g["is_void"].astype(bool)) & (g["n_way"] > 0)].copy()
    g["ht"] = [clean_team(x) for x in g["home"]]
    g["at"] = [clean_team(x) for x in g["away"]]

    rows = []
    for r in g.itertuples():
        wi = WIN_IDX.get((int(r.n_way), str(r.result)))
        if wi is None:
            continue
        try:
            od = [float(x) for x in str(r.odds).split(",")]
        except ValueError:
            continue
        if len(od) != int(r.n_way) or any(o <= 1.001 for o in od):
            continue
        m = _re.search(r"(\d{2})\.(\d{2})", str(r.date_text))
        mmdd = (m.group(1) + m.group(2)) if m else ""
        key = f"{r.year}|{r.league}|{r.ht}|{r.at}|{mmdd}"
        for i, o in enumerate(od):
            rows.append({"m": key, "odds": o,
                         "hit": 1.0 if i == wi else 0.0,
                         "ret": (o - 1) if i == wi else -1.0})
    d = pd.DataFrame(rows)
    if d.empty:
        return []
    pick = d.sort_values(["m", "odds"]).groupby("m").head(1)   # 경기별 최저 배당
    n_all = len(pick)
    out = []
    for cap in (99, 1.8, 1.5, 1.4, 1.3, 1.25, 1.2, 1.15):
        s2 = pick[pick["odds"] <= cap]
        if len(s2) < 200:
            continue
        h = float(s2["hit"].mean())
        out.append({"cap": (None if cap == 99 else cap), "n": int(len(s2)),
                    "share": round(len(s2) / n_all, 4),
                    "hit": round(h, 4), "roi": round(float(s2["ret"].mean()), 4),
                    "hit2": round(h * h, 4), "hit3": round(h ** 3, 4)})
    return out


def pick_modes(odds_rows, mkt_rows) -> dict:
    """사이트의 두 픽 기준을 **실제로 재서** 돌려준다.

    화면(`web/src/lib/fmt.js:lessBadPick`)이 경기마다 하나를 고르는 기준이 둘이다.
        hit — 가장 낮은 배당대 (동률이면 환급률 높은 쪽 → 낮은 배당)
        roi — (마켓, 배당대) 실측 ROI 가 가장 덜 나쁜 것

    🔴 이 두 수치가 **화면에 손으로 박혀 있었다.** 파이프라인을 고치면 조용히 틀린
       숫자가 남는다 — 실제로 62.34%/58.80% 이 유령 경기 시절 값 그대로였다.
       여기서 계산해 JSON 으로 넘긴다.
    """
    bins = [r["bin"] for r in odds_rows]
    grade_roi = {r["bin"]: r["roi"] for r in odds_rows}
    cell = {(r["fam"], r["bin"]): r for r in mkt_rows}

    import re as _re
    g = pd.read_csv(ROOT / "data" / "processed" / "games.csv")
    g = g[(~g["is_void"].astype(bool)) & (g["n_way"] > 0)].copy()
    g["ht"] = [clean_team(x) for x in g["home"]]
    g["at"] = [clean_team(x) for x in g["away"]]

    games: dict = {}
    for r in g.itertuples():
        nw = int(r.n_way)
        wi = WIN_IDX.get((nw, str(r.result)))
        if wi is None:
            continue
        try:
            od = [float(x) for x in str(r.odds).split(",")]
        except ValueError:
            continue
        if len(od) != nw or any(o <= 1.001 for o in od):
            continue
        m = _re.search(r"(\d{2})\.(\d{2})", str(r.date_text))
        key = f"{r.year}|{r.league}|{r.ht}|{r.at}|{(m.group(1)+m.group(2)) if m else ''}"
        # 환급률 — 화면의 payout() 과 같은 값이어야 한다
        pay = 87.8 if nw == 2 else (86.8 if "핸디" in str(r.market_family) else 87.0)
        for i, o in enumerate(od):
            b = _bin_of(o)
            c = cell.get((str(r.market_family), b))
            games.setdefault(key, []).append({
                "odds": o, "bin": b, "pay": pay,
                "roi": (c or {}).get("roi", grade_roi.get(b)),
                "hit": 1.0 if i == wi else 0.0,
                "ret": (o - 1) if i == wi else -1.0})

    out = {}
    for mode in ("hit", "roi"):
        n = h = 0
        ret = 0.0
        for opts in games.values():
            o = [x for x in opts if x["roi"] is not None]
            if not o:
                continue
            if mode == "roi":
                o.sort(key=lambda x: (-x["roi"], x["odds"]))
            else:
                o.sort(key=lambda x: (bins.index(x["bin"]), -x["pay"], x["odds"]))
            n += 1; h += o[0]["hit"]; ret += o[0]["ret"]
        out[mode] = {"n": n, "hit": round(h / n, 4), "roi": round(ret / n, 4)} if n else None
    return out


def _rules(odds_rows, st_rows, sel_rows) -> list[dict]:
    """등급표에서 규칙을 뽑아낸다. 안정성 관문을 통과한 것만 규칙이 된다."""
    ok_bins = [r for r in odds_rows if r["stable"]]
    # ⚠️ 지렛대 크기 순으로 적는다. 예전엔 배당대 규칙을 맨 앞에 두고 단폴을
    #    '지정 경기만 되니까' 라며 뒤로 미뤘는데, 실측하니 **단폴이 압도적으로 크다.**
    #      단폴 vs 2폴  8.86%p   (−9.83% vs −18.69%)
    #      배당대       3.86%p
    #      2-way 구조   2.39%p
    #      회차 환급률   2.36%p — 단, 배당대 규칙과 겹쳐 추가 이득이 없다
    #                          (둘 다 −9.38% vs 배당대만 −9.23%)
    #    다리를 하나 줄이는 게 나머지 규칙을 다 합친 것보다 크다.
    rules = [{"rule": "단폴이 가능하면 무조건 단폴",
              "why": "다리 하나가 −9.8%, 두 다리면 −18.7% — 8.9%p 차이다. "
                     "다른 어떤 규칙보다 크다. 단 '한경기' 로 지정된 경기만 되고 "
                     "단위투표금액이 1,000원이다(조합은 100원)"}]
    worst = min(ok_bins, key=lambda r: r["roi"]) if ok_bins else None
    best = max(ok_bins, key=lambda r: r["roi"]) if ok_bins else None
    if worst:
        others = [r["roi"] for r in ok_bins if r is not worst]
        gap = (max(others) - worst["roi"]) * 100 if others else 0
        rules.append({"rule": f"배당 {worst['bin']} 금지",
                      "why": f"{worst['roi']*100:.1f}%. 다른 어떤 구간보다 {gap:.0f}%p 나쁘다"})
    if best:
        rules.append({"rule": f"배당 {best['bin']} 우선",
                      "why": f"{best['roi']*100:.1f}%. 전체 평균보다 "
                             f"{(best['roi'] - OVERALL[0])*100:.1f}%p 낫다"})
    if st_rows:
        top = max(st_rows, key=lambda r: r["roi"])
        rest = " · ".join(f"{r['booking']} {r['payout']:.1f}%" for r in st_rows if r is not top)
        rules.append({"rule": f"{top['booking']} 우선",
                      "why": f"환급률 {top['payout']:.1f}% ({rest})"})
    good_sel = [r for r in sel_rows if r["stable"] and r["grade"] in ("A", "B")]
    if good_sel:
        r = good_sel[0]
        rules.append({"rule": f"3-way 는 '{r['sel']}' 선택지",
                      "why": f"{r['fam']} {r['sel']} {r['roi']*100:.1f}%, 네 해 모두 평균보다 낫다"})
    else:
        n_unstable = sum(1 for r in sel_rows if not r["stable"])
        rules.append({"rule": "3-way 선택지 규칙 없음",
                      "why": f"{len(sel_rows)}개 중 {n_unstable}개가 연도별로 방향이 "
                             f"뒤집힌다. 어느 선택지가 덜 나쁜지는 해마다 달라진다"})
    # ⚠️ 예전엔 여기 "조합 금지 · 단폴만" 이 있었다. 두 번 틀렸다.
    #    (1) 단폴(한경기구매)은 **'한경기' 로 지정된 경기만** 가능해 아무 경기나 못 산다.
    #    (2) 배당을 올리려면 조합해야 하고, 조합하면 다리마다 마진이 한 번씩 물린다.
    #    자세한 조합 설계는 src/combo.py · src/today_combo.py 가 담당한다.
    rules += [
        {"rule": "다리는 최소로",
         "why": "단폴은 '한경기' 지정 경기만. 조합은 2~10경기이고 다리를 하나 더 "
                "붙일 때마다 약 −6%p — 목표 배당은 다리 수가 아니라 다리당 배당으로 맞춘다"},
        {"rule": "위 배당대 등급은 '최소 손실' 기준",
         "why": "목표 배당이 있으면 뒤집힌다. 저배당은 다리 하나로는 최선이지만 "
                "배당을 만드는 효율은 최악이다 (src/combo.py 참고)"},
        {"rule": "회차 환급률 확인", "why": "회차마다 86~89% 로 다르다"},
    ]
    return rules


OVERALL = [0.0]      # main() 이 채운다 (규칙 문구에 전체 평균이 필요하다)


def main() -> int:
    d = build()
    d["bin"] = pd.cut(d["odds"], BINS, labels=LABELS)
    years = sorted(int(y) for y in d["year"].unique())
    # 해마다 시장 전체가 흔들리므로 '그 해 평균' 을 기준선으로 쓴다
    base_by_year = {int(y): float(x["ret"].mean()) for y, x in d.groupby("year")}

    # --- 배당대 등급 (연도별 안정성 포함)
    odds_rows = []
    for b in LABELS:
        s = d[d["bin"] == b]
        if len(s) < MIN_N:
            continue
        by_year, ok, why = stability(s, base_by_year)
        odds_rows.append({
            "bin": b, "n": int(len(s)),
            "roi": round(float(s["ret"].mean()), 4),
            "base": round(float(s["base"].mean()), 4),
            "edge": round(float(s["edge"].mean()), 4),
            "grade": grade(float(s["ret"].mean())) if ok else "?",
            # 적중률 — 수익률과 반대로 움직인다. "덜 잃기" 와 "자주 맞기" 는
            # 같은 방향이지만(둘 다 낮은 배당), 목표 배당을 올리면 갈라진다.
            "hit": round(float(s["hit"].mean()), 4) if "hit" in s else None,
            "by_year": by_year, "stable": ok, "why_unstable": why,
        })

    # --- 구조(booking) 등급
    st_rows = []
    for bk, s in d.groupby("booking"):
        if len(s) < MIN_N:
            continue
        by_year, ok, why = stability(s, base_by_year)
        st_rows.append({"booking": bk, "n": int(len(s)),
                        "roi": round(float(s["ret"].mean()), 4),
                        "payout": round(float((1 + s["base"]).mean()) * 100, 2),
                        "grade": grade(float(s["ret"].mean())) if ok else "?",
                        "by_year": by_year, "stable": ok, "why_unstable": why})
    st_rows.sort(key=lambda r: -r["roi"])

    # --- 3-way 선택지 등급 (중간이 덜 나쁘다)
    sel_rows = []
    for (fam, sel), s in d[d["n_way"] == 3].groupby(["fam", "sel"]):
        if len(s) < MIN_N:
            continue
        by_year, ok, why = stability(s, base_by_year)
        sel_rows.append({"fam": fam, "sel": sel, "n": int(len(s)),
                         "roi": round(float(s["ret"].mean()), 4),
                         "edge": round(float(s["edge"].mean()), 4),
                         "grade": grade(float(s["ret"].mean())) if ok else "?",
                         "by_year": by_year, "stable": ok, "why_unstable": why})
    sel_rows.sort(key=lambda r: -r["roi"])

    # --- 마켓 × 배당대 셀
    # ⚠️ 같은 배당대라도 마켓마다 ROI 가 1~3%p 갈린다. 배당대만 보면 그걸 버린다.
    #    실측: 1.0–1.3 에서 승무패 −6.29% vs 언더오버 −13.26% (7%p 차이).
    #    안정성 관문(그 해 평균 대비 방향이 네 해 모두 같은가)을 통과한 셀만 싣는다.
    cell_rows = []
    for (fam, b), s2 in d.groupby(["fam", "bin"], observed=True):
        if len(s2) < 800:
            continue
        by_year, ok, why = stability(s2, base_by_year)
        cell_rows.append({
            "fam": fam, "bin": str(b), "n": int(len(s2)),
            "roi": round(float(s2["ret"].mean()), 4),
            "hit": round(float(s2["hit"].mean()), 4),
            "stable": ok, "why_unstable": why,
        })
    cell_rows.sort(key=lambda r: -r["roi"])

    overall = float(d["ret"].mean())
    OVERALL[0] = overall
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
        "odds_caps": odds_caps(),
        "market_bins": cell_rows,
        # 화면의 두 픽 기준 실적 — 손으로 박아두면 파이프라인 고칠 때 어긋난다
        "pick_modes": pick_modes(odds_rows, cell_rows),
        "structures": st_rows,
        "three_way_selections": sel_rows,
        # ⚠️ 규칙은 **위 표에서 파생**한다. 손으로 적으면 표와 어긋난다.
        #    실제로 어긋나 있었다: "3-way 는 '중간' 선택지" 를 확정 규칙으로 적어놨는데
        #    연도 관문을 세우니 승①패 중간이 그 해 평균 대비 2023 +11.9 / 2024 −1.1 /
        #    2025 +13.9 로 방향이 뒤집혔다. 재현되지 않는 규칙이었다.
        "rules": _rules(odds_rows, st_rows, sel_rows),
    }
    persist_artifact("loss_grades", doc, OUT)

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
