"""중복 발매를 이용한 오프닝 vs 클로징 판정 — 기다릴 필요가 없었다.

착상
----
`정보시차_전제.md` 에서 오프닝/클로징 비교를 시도했는데 **n=69** 라 판정이 안 됐고
"2~3주 기다리면 표본이 쌓인다"고 적었다. 그런데 기다릴 필요가 없다.

**프로토는 같은 경기를 여러 회차에 중복 발매한다**(`HANDOFF §6` 함정 1).
회차는 순차적으로 열리므로 — **같은 경기가 회차 N 과 N+1 에 모두 있으면
N 의 배당은 더 이른 가격, N+1 은 더 늦은 가격**이다.
과거 3년치에서 그대로 뽑을 수 있다.

    같은 경기·같은 마켓이 2개 이상 회차에 발매   1,323건
    그중 회차마다 배당이 다른 것                 530건   ← 실시간 n=69 의 7.7배

⚠️ 규율
-------
- 무효(배당 1.0) 제외
- 결과가 정산된 것만
- 회차 번호가 클수록 나중에 열린 것으로 본다.
  ⚠️ `HANDOFF` 함정 1 은 "회차 순서 ≠ 경기 시간 순서"를 말하는 것이지
     "회차 개설 순서"를 부정하는 게 아니다. 회차 자체는 순차 개설된다.
- 게임행 단위 클러스터 부트스트랩
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"

WIN_IDX = {(2, "홈승"): 0, (2, "홈패"): 1, (2, "언더"): 0, (2, "오버"): 1,
           (2, "핸디승"): 0, (2, "핸디패"): 1,
           (3, "홈승"): 0, (3, "무승부"): 1, (3, "홈패"): 2,
           (3, "핸디승"): 0, (3, "핸디무"): 1, (3, "핸디패"): 2,
           (3, "①"): 1, (3, "⑤"): 1}


def load() -> pd.DataFrame:
    g = pd.read_csv(PROC / "games.csv")
    g = g[~g["is_void"].astype(bool)].copy()
    g["home_team"] = g["home"].astype(str).str.replace(r"\s+-?\d+\s*$", "", regex=True).str.strip()
    g["away_team"] = g["away"].astype(str).str.replace(r"^\s*-?\d+\s+", "", regex=True).str.strip()
    md = g["date_text"].astype(str).str.extract(r"(\d{2})\.(\d{2})")
    g["mmdd"] = md[0] + md[1]
    g["mlab"] = g["market_label"].fillna("")
    return g


KEY = ["year", "league", "mmdd", "home_team", "away_team",
       "market_family", "n_way", "mlab"]


def main() -> int:
    g = load()
    rows = []
    for k, s in g.groupby(KEY):
        if s["round"].nunique() < 2:
            continue
        s = s.sort_values("round")
        nw = int(k[6])
        wi = WIN_IDX.get((nw, s["result"].iloc[-1]))
        if wi is None:
            continue
        first, last = s.iloc[0], s.iloc[-1]
        try:
            o0 = [float(x) for x in str(first["odds"]).split(",")]
            o1 = [float(x) for x in str(last["odds"]).split(",")]
        except ValueError:
            continue
        if len(o0) != nw or len(o1) != nw:
            continue
        if any(x <= 1.001 for x in o0 + o1):        # 무효
            continue
        if str(first["odds"]) == str(last["odds"]):  # 안 움직였으면 비교 의미 없음
            continue
        p0 = np.array([1 / x for x in o0]); p0 /= p0.sum()
        p1 = np.array([1 / x for x in o1]); p1 /= p1.sum()
        y = np.zeros(nw); y[wi] = 1
        rows.append({
            "gid": "|".join(str(x) for x in k),
            "league": k[1], "fam": k[5], "n_way": nw,
            "r0": int(first["round"]), "r1": int(last["round"]),
            "move": float(np.abs(p1 - p0).max()),
            "b0": float(((p0 - y) ** 2).sum()),
            "b1": float(((p1 - y) ** 2).sum()),
            # 나중 회차 배당으로 이겼을 때 수익 (이른 회차 가격에 걸었다고 가정)
            "ret_early": (o0[wi] - 1) if True else 0,
        })
    d = pd.DataFrame(rows)
    if d.empty:
        print("표본 0 — 중복 발매 추출 실패")
        return 1

    rng = np.random.default_rng(42)

    def boot(x):
        x = np.asarray(x, dtype=float)
        idx = rng.integers(0, len(x), size=(5000, len(x)))
        b = x[idx].mean(axis=1)
        return float(np.percentile(b, 2.5)), float(np.percentile(b, 97.5))

    print(f"중복 발매·배당 변동·정산 완료: {len(d):,}건  "
          f"(실시간 수집 n=69 의 {len(d)/69:.1f}배)")
    print(f"회차 간격 중앙 {int((d['r1'] - d['r0']).median())}회차 · "
          f"이동폭 중앙 {d['move'].median()*100:.2f}%p\n")

    print("=" * 72)
    print("나중 회차(클로징) 가 이른 회차(오프닝) 보다 정확한가")
    print("=" * 72)
    diff = d["b1"].values - d["b0"].values
    lo, hi = boot(diff)
    print(f"  오프닝 Brier {d['b0'].mean():.5f}  →  클로징 Brier {d['b1'].mean():.5f}")
    print(f"  차이 {diff.mean():+.5f}  95%CI [{lo:+.5f}, {hi:+.5f}]")
    print(f"  클로징이 더 정확할 확률 "
          f"{(np.array([diff[rng.integers(0,len(diff),len(diff))].mean() for _ in range(2000)]) < 0).mean():.1%}")
    print(f"  → {'✅ 클로징이 낫다 = 오프닝에 기회가 남아 있었다' if hi < 0 else '❌ 판정 불가 (CI 가 0 포함)'}")

    print(f"\n{'구분':<16}{'n':>7}{'오프닝':>10}{'클로징':>10}{'차이':>10}  95%CI")
    print("-" * 72)
    for lab, sub in [("이동 2%p+", d[d["move"] >= 0.02]),
                     ("이동 5%p+", d[d["move"] >= 0.05]),
                     ("2-way", d[d["n_way"] == 2]),
                     ("3-way", d[d["n_way"] == 3])]:
        if len(sub) < 40:
            continue
        df = sub["b1"].values - sub["b0"].values
        l, h = boot(df)
        star = " ⭐" if h < 0 else ""
        print(f"{lab:<16}{len(sub):>7,}{sub['b0'].mean():>10.5f}{sub['b1'].mean():>10.5f}"
              f"{df.mean():>+10.5f}  [{l:+.5f}, {h:+.5f}]{star}")

    print("\n" + "=" * 72)
    print("⭐ = CI 상한 < 0 (클로징이 확실히 더 정확 = 오프닝에 미반영 정보가 있었다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
