"""전 마켓 스캔 — 프로토는 어느 시장을 가장 못 매기는가.

왜 이게 핵심인가
----------------
지금까지 승패·승무패만 봤다. 그건 프로토 물량의 **38.8%** 다.
나머지 61.2%(언더오버 26.1%, 핸디캡 29.3%, 승①패 4.9%)는 손도 안 댔다.

스코어 분포 모델(`score_dist.py`)이 있으면 **하나의 예측으로 전 마켓을 가격 매길 수 있다.**
그러면 이 질문에 답할 수 있다:

> **프로토가 승패는 잘 매기는데 언더오버는 못 매길 수도 있지 않나?**

시장마다 난이도가 다르다. 총득점 예측은 승패 예측보다 어렵고,
북메이커가 어려워하는 곳이 곧 기회다.

측정
----
각 마켓에서 **모델 확률 vs 프로토 devig 확률**의 Brier 를 비교한다.
모델이 프로토보다 정확한 마켓이 있다면 거기가 공략 지점이다.

⚠️ 라인·핸디캡 값은 마켓 라벨에서 파싱한다: `U 5.5`, `H -1.0`
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bets import _WINNER                                          # noqa: E402
from matches import clean_team                                    # noqa: E402
from score_dist import (joint, p_handicap, p_margin_band, p_odd,  # noqa: E402
                        p_one_run, p_over, p_win)

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
TRAIN_END = 2024

_LINE = re.compile(r"([-+]?\d+\.?\d*)")


def parse_line(label: str) -> float | None:
    m = _LINE.search(str(label or ""))
    return float(m.group(1)) if m else None


def load() -> pd.DataFrame:
    lam = pd.read_csv(PROC / "lambdas.csv", parse_dates=["date"])
    g = pd.read_csv(PROC / "games.csv")
    g = g[~g["is_void"].astype(bool)]
    md = g["date_text"].astype(str).str.extract(r"(\d{2})\.(\d{2})")
    g = g.assign(_mm=pd.to_numeric(md[0], errors="coerce"),
                 _dd=pd.to_numeric(md[1], errors="coerce")).dropna(subset=["_mm", "_dd"])
    g["date"] = pd.to_datetime(dict(year=g["year"], month=g["_mm"].astype(int),
                                    day=g["_dd"].astype(int)), errors="coerce")
    g = g.dropna(subset=["date"])

    # 팀명 정리 (스코어가 붙어 있다)
    g["home_team"] = [clean_team(x) for x in g["home"]]
    g["away_team"] = g["away"].map(
        lambda x: (re.match(r"^-?\d+\s+(.+?)\s*$", str(x).strip()) or [None, str(x).strip()])[1]
        if re.match(r"^-?\d+\s+(.+?)\s*$", str(x).strip()) else str(x).strip())

    key = ["date", "league", "home_team", "away_team"]
    return g.merge(lam[key + ["lam_home", "lam_away", "sport"]],
                   on=key, how="inner", suffixes=("", "_l"))


# ⚠️ 퇴화 확률 = 모델이 그 마켓을 못 매긴 것이다.
#    실측 2026-07-29: 배구 언더오버 라인이 140.5~185.5(총 **득점**)인데
#    score_dist 는 배구를 **세트**로 모델링한다. 그래서 p_over 가 그대로 0 이 되고
#    "언더 100% · 예상손익 +76%" 라는 가짜 우위가 화면에 찍혔다.
#    (연구 수치에도 샜다 — 배구 언더오버 모델 Brier 0.489 vs 시장 0.250)
#    유한 배당이 걸린 선택지에 확률 0/1 은 존재할 수 없다. 값을 버린다.
_EPS = 1e-6


def _sane(pm):
    if pm is None:
        return None
    if any((p <= _EPS or p >= 1 - _EPS) for p in pm):
        return None
    return pm


def model_probs(row) -> list[float] | None:
    """마켓별 모델 확률 벡터. 프로토 선택지 순서와 맞춘다."""
    return _sane(_model_probs(row))


def _model_probs(row) -> list[float] | None:
    M = joint(row["lam_home"], row["lam_away"], row["sport"])
    fam, nw = row["market_family"], int(row["n_way"])
    lab = row["market_label"]

    if fam == "승패" and nw == 2:
        h, d, a = p_win(M)
        s = h + a
        return [h / s, a / s] if s > 0 else None
    if fam == "승무패" and nw == 3:
        return list(p_win(M))
    if fam == "언더오버" and nw == 2:
        line = parse_line(lab)
        if line is None:
            return None
        po = p_over(M, line)
        return [1 - po, po]                 # [언더, 오버]
    if fam == "핸디캡":
        hc = parse_line(lab)
        if hc is None:
            return None
        w, d, l = p_handicap(M, hc)
        if nw == 2:
            s = w + l
            return [w / s, l / s] if s > 0 else None
        return [w, d, l]
    if fam == "승①패" and nw == 3:
        return list(p_one_run(M))
    if fam == "홀짝" and nw == 2:
        po = p_odd(M)
        return [po, 1 - po]                 # [홀, 짝]
    if fam == "승⑤패" and nw == 3:
        # 농구·배구의 3-way 일반 마켓. 무승부가 아니라 **5점차 이내**다.
        return list(p_margin_band(M, 5))
    return None


# 🔴 사본 금지 — 정본은 `bets._WINNER` 하나뿐이다.
#    여기 손으로 적어 두면 새 마켓이 생겼을 때 한쪽만 고치게 된다.
WIN_IDX = _WINNER


def main() -> int:
    df = load()
    print(f"프로토 게임행 × λ 결합 {len(df):,}건")

    rows = []
    for r in df.itertuples():
        d = r._asdict()
        wi = WIN_IDX.get((int(d["n_way"]), d["result"]))
        if wi is None:
            continue
        odds = [float(x) for x in str(d["odds"]).split(",") if x]
        if len(odds) != int(d["n_way"]) or any(o <= 1 for o in odds):
            continue
        pm = model_probs(d)
        if not pm or len(pm) != len(odds) or any(not np.isfinite(p) for p in pm):
            continue
        ov = sum(1 / o for o in odds)
        pp = [(1 / o) / ov for o in odds]        # 프로토 devig
        for i, (p_model, p_proto, o) in enumerate(zip(pm, pp, odds)):
            rows.append({
                # ⚠️ league 를 남기는 이유: 지금까지 종목(bs/sc/bk/vl)으로만
                #    쪼개 봤는데, 프로토 배당은 해외 북메이커를 참조해 만들어진다.
                #    같은 야구여도 MLB 는 해외 커버가 두껍고 NPB·KBO 는 얇다.
                #    시장이 허술한 곳을 찾으려면 리그 단위로 봐야 한다.
                "year": d["year"], "sport": d["sport"], "league": d["league"],
                "market": f"{d['market_family']}({int(d['n_way'])}-way)",
                "p_model": p_model, "p_proto": p_proto, "odds": o,
                # ⚠️ 선택지 위치가 없으면 '승①패의 1점차'처럼 **마켓 안의 특정
                #    선택지**를 못 집는다. Q0 의 최대 이상점(+6.46%p)이 바로
                #    그 형태였는데 마켓 단위로만 보면 +0.70% 로 희석된다.
                "sel_idx": i, "n_way": int(d["n_way"]),
                "won": 1.0 if i == wi else 0.0})

    L = pd.DataFrame(rows)
    te = L[L["year"] > TRAIN_END]
    print(f"선택지 {len(L):,} · 검증({TRAIN_END+1}~) {len(te):,}\n")

    print(f"{'마켓':<18}{'n':>9}{'모델 Brier':>12}{'프로토 Brier':>13}"
          f"{'차이':>10}  판정")
    print("-" * 74)
    out = []
    for mk, s in te.groupby("market"):
        if len(s) < 500:
            continue
        bm = float(np.mean((s["p_model"] - s["won"]) ** 2))
        bp = float(np.mean((s["p_proto"] - s["won"]) ** 2))
        out.append((mk, len(s), bm, bp))
    for mk, n, bm, bp in sorted(out, key=lambda x: x[2] - x[3]):
        v = "✅ 모델 우위" if bm < bp else "❌ 프로토 우위"
        print(f"{mk:<18}{n:>9,}{bm:>12.5f}{bp:>13.5f}{bm-bp:>+10.5f}  {v}")

    # 종목 × 마켓
    print(f"\n{'종목':<6}{'마켓':<18}{'n':>8}{'모델':>10}{'프로토':>10}{'차이':>10}")
    print("-" * 64)
    SP = {"bs": "야구", "sc": "축구", "bk": "농구", "vl": "배구"}
    for (sp, mk), s in te.groupby(["sport", "market"]):
        if len(s) < 400:
            continue
        bm = float(np.mean((s["p_model"] - s["won"]) ** 2))
        bp = float(np.mean((s["p_proto"] - s["won"]) ** 2))
        flag = " ⭐" if bm < bp else ""
        print(f"{SP.get(sp, sp):<6}{mk:<18}{len(s):>8,}{bm:>10.5f}"
              f"{bp:>10.5f}{bm-bp:>+10.5f}{flag}")

    L.to_csv(PROC / "market_scan.csv", index=False)
    print(f"\n저장: {PROC / 'market_scan.csv'}")
    return 0




def _selftest() -> None:
    """WIN_IDX 커버리지 + model_probs 정합성.

    ⚠️ 2026-07-28 의 가짜 ROI +30% 가 정확히 여기서 나왔다.
       `WIN_IDX` 에 `⑤` 가 없어 KBL 승⑤패의 중간 결과 32% 가 조용히 버려졌고,
       6점차 이상으로 갈린 경기만 남아 모델이 이기는 것처럼 보였다.
       **매핑에 없는 결과값은 예외 없이 사라진다.** 그게 결과값과 상관있으면 가짜다.
    """
    import collections
    fails: list[str] = []

    # ① 실제 데이터의 (n_way, result) 를 WIN_IDX 가 덮는가
    path = PROC.parent / "processed" / "games.csv"
    if path.exists():
        g = pd.read_csv(path)
        g = g[~g["is_void"].astype(bool)]
        SKIP = {"경기전", "하프타임", "취소", "연기", "중단", "무효", "nan"}
        cnt = collections.Counter()
        for nw, res in zip(g["n_way"], g["result"].astype(str)):
            try:
                nw = int(nw)
            except (TypeError, ValueError):
                continue
            if nw < 2 or res in SKIP:
                continue
            if not all("가" <= c <= "힣" or c in "①⑤" for c in res):
                continue                      # 인코딩 깨진 값은 별개 문제
            cnt[(nw, res)] += 1
        total = sum(cnt.values())
        miss = {k: v for k, v in cnt.items() if k not in WIN_IDX}
        n_miss = sum(miss.values())
        print(f"WIN_IDX 커버리지: {total - n_miss:,}/{total:,} ({1 - n_miss/total:.3%})")
        for k, v in sorted(miss.items(), key=lambda x: -x[1])[:6]:
            print(f"    미매핑 n_way={k[0]} {k[1]!r} {v:,}건")
        if n_miss > total * 0.001:
            fails.append(f"WIN_IDX 누락 {n_miss:,}건 ({n_miss/total:.2%}) — 조용히 버려진다")
    else:
        print("WIN_IDX 커버리지: games.csv 없음 — 건너뜀")

    # ② model_probs 가 마켓별로 올바른 길이의 확률 벡터를 주는가
    cases = [
        ({"market_family": "승패", "n_way": 2, "market_label": ""}, 2),
        ({"market_family": "승무패", "n_way": 3, "market_label": ""}, 3),
        ({"market_family": "승①패", "n_way": 3, "market_label": "승①패"}, 3),
        ({"market_family": "승⑤패", "n_way": 3, "market_label": ""}, 3),
        ({"market_family": "언더오버", "n_way": 2, "market_label": "U 8.5"}, 2),
        ({"market_family": "핸디캡", "n_way": 2, "market_label": "H -1.5"}, 2),
        # ⚠️ 3-way 핸디캡은 **정수 라인만** 존재한다(실측 24,230건 중 .5 라인 0건).
        #    .5 라인은 무승부가 불가능해 구조적으로 2-way 다.
        ({"market_family": "핸디캡", "n_way": 3, "market_label": "H -1.0"}, 3),
        ({"market_family": "핸디캡", "n_way": 2, "market_label": "H -1.5"}, 2),
        ({"market_family": "홀짝", "n_way": 2, "market_label": ""}, 2),
    ]
    for base, want in cases:
        row = {**base, "lam_home": 4.8, "lam_away": 4.3, "sport": "bs"}
        pm = model_probs(row)
        if pm is None:
            fails.append(f"{base['market_family']}({want}): None 반환")
            continue
        if len(pm) != want:
            fails.append(f"{base['market_family']}({want}): 길이 {len(pm)}")
        elif abs(sum(pm) - 1.0) > 1e-6:
            fails.append(f"{base['market_family']}({want}): 합 {sum(pm):.6f} ≠ 1")
        elif min(pm) < 0:
            fails.append(f"{base['market_family']}({want}): 음수 확률")

    for f in fails:
        print(f"  FAIL {f}")
    print(f"market_scan 자기검사: {'통과' if not fails else str(len(fails)) + '건 실패'}")
    if fails:
        raise SystemExit(1)

if __name__ == "__main__":
    # ⚠️ --selftest 를 main() 보다 먼저 검사한다.
    if "--selftest" in sys.argv:
        _selftest()
    else:
        raise SystemExit(main())
