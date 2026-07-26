"""결과 라벨 → 배당 인덱스 매핑을 데이터로 확정한다.

왜 필요한가
------------
배당 순서를 눈으로 추정하면 틀린다(실제로 언더오버에서 틀렸다).
다행히 **매핑이 맞는지 판정하는 객관적 기준**이 있다:

    배당이 대체로 정확하다면, 어느 선택지에 걸든 ROI ≈ 1/오버라운드 − 1

매핑이 뒤바뀌면 어떤 선택지는 지나치게 좋고 어떤 선택지는 지나치게 나빠진다.
그래서 **가능한 모든 순열을 시도해 이론값에서 가장 적게 벗어나는 배치**를 고르면
매핑이 데이터로 확정된다.

⚠️ 한계: 시장에 실제 편향(favorite-longshot 등)이 있으면 선택지별 ROI가 원래
   조금씩 다르다. 그래서 이 방법은 '완벽한 매핑 발견'이 아니라
   **'명백히 뒤집힌 매핑 탐지'** 용이다. 판정 여유를 크게 둔다.
"""
from __future__ import annotations

import sys
from itertools import permutations
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bets import _WINNER                                    # noqa: E402

GAMES = Path(__file__).resolve().parent.parent / "data" / "processed" / "games.csv"

THEORETICAL = {"2-way": 1 / 1.1364 - 1,
               "3-way": 1 / 1.1494 - 1,
               "3-way-핸디캡": 1 / 1.1629 - 1}

# 마켓 계열 → 그 계열에서 나타나는 결과 라벨들
LABELS = {
    ("승패", 2):     ["홈승", "홈패"],
    ("언더오버", 2): ["언더", "오버"],
    ("핸디캡", 2):   ["핸디승", "핸디패"],
    ("승무패", 3):   ["홈승", "무승부", "홈패"],
    ("핸디캡", 3):   ["핸디승", "핸디무", "핸디패"],
    ("승①패", 3):   ["홈승", "①", "홈패"],
}


def evaluate(sub: pd.DataFrame, labels: list[str], perm: tuple[int, ...],
             theo: float) -> tuple[float, list[float]]:
    """labels[i] 가 배당 인덱스 perm[i] 에 대응한다고 가정했을 때의 선택지별 ROI."""
    odds = np.stack([sub[f"o{i+1}"].to_numpy() for i in range(len(labels))], axis=1)
    res = sub["result"].to_numpy()

    win_idx = np.full(len(sub), -1)
    for li, lab in enumerate(labels):
        win_idx[res == lab] = perm[li]

    rois = []
    for i in range(len(labels)):
        won = (win_idx == i)
        prof = np.where(won, odds[:, i] - 1.0, -1.0)
        rois.append(float(prof.mean()))
    # 이론값에서의 총 이탈
    dev = sum(abs(r - theo) for r in rois)
    return dev, rois


def main() -> int:
    if not GAMES.exists():
        print("먼저 python src/build_dataset.py 를 실행하세요.")
        return 1
    g = pd.read_csv(GAMES)
    g = g[~g["is_void"].astype(bool)]

    print("각 마켓의 결과라벨 → 배당인덱스 매핑을 전 순열 탐색으로 확정한다.")
    print("판정 기준: 선택지별 ROI가 이론값에서 총 얼마나 벗어나는가 (작을수록 옳음)\n")

    fixes = []
    for (fam, nway), labels in LABELS.items():
        sub = g[(g["market_family"] == fam) & (g["n_way"] == nway)
                & (g["result"].isin(labels))].copy()
        if len(sub) < 500:
            continue
        parts = sub["odds"].str.split(",", expand=True)
        if parts.shape[1] < nway:
            continue
        for i in range(nway):
            sub[f"o{i+1}"] = parts[i].astype(float)
        sub = sub.dropna(subset=[f"o{i+1}" for i in range(nway)])

        bc = sub["booking_class"].mode()[0]
        theo = THEORETICAL.get(bc, -0.12)

        results = []
        for perm in permutations(range(nway)):
            dev, rois = evaluate(sub, labels, perm, theo)
            results.append((dev, perm, rois))
        results.sort()

        best_dev, best_perm, best_rois = results[0]
        # ⚠️ 현재 매핑은 bets.py 에서 직접 읽는다.
        #    여기 LABELS 순서를 '현재값'으로 착각하면 검증이 무의미해진다.
        cur_perm = tuple(_WINNER[(nway, lab)] for lab in labels)

        flag = "✅ 현재 매핑이 최적" if best_perm == cur_perm else "❌ 현재 매핑이 틀림"
        print(f"■ {fam}({nway}-way)  n={len(sub):,}  이론 ROI={theo:.2%}   {flag}")
        for dev, perm, rois in results[:3]:
            mapping = "  ".join(f"{labels[i]}→idx{perm[i]}" for i in range(nway))
            roi_s = "  ".join(f"{r:+.2%}" for r in rois)
            mark = " ← 최적" if perm == best_perm else ""
            cur_mark = " (현재)" if perm == cur_perm else ""
            print(f"    이탈={dev:.4f}  [{mapping}]  선택지ROI: {roi_s}{mark}{cur_mark}")
        if best_perm != cur_perm:
            fixes.append((fam, nway, labels, best_perm))
        print()

    if fixes:
        print("=" * 70)
        print("수정이 필요한 매핑:")
        for fam, nway, labels, perm in fixes:
            print(f"  {fam}({nway}-way): " +
                  ", ".join(f"{labels[i]} → 인덱스 {perm[i]}" for i in range(nway)))
    else:
        print("모든 매핑이 최적입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
