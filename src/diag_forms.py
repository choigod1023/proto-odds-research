"""최근폼이 왜 안 붙는지 진단한다.

2026-08-08: picks_v2.json 249경기 **전부** form_src=None 이었고, 해설의 56% 가
"이번 시즌 기록이 충분히 쌓이지 않았다" 로 나갔다. MLB 는 98경기 중 58경기가
그랬다 — 4년치를 모아 둔 프로젝트에서 나올 수 없는 말이다.

폼이 붙는 경로는 두 갈래다:
    FORMS[(리그, 팀)]  →  없으면  FORM_BY_TEAM[팀]  (그 팀이 가장 많이 뛴 리그)
둘 다 비면 form_src=None 이 된다. 어디서 끊기는지 단계별로 찍는다.

    python src/diag_forms.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from team_form import build_forms, load_history          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _raw() -> None:
    """load_matches 의 필터를 걷어내고 games.csv 원본을 본다.

    2026 이 통째로 사라지는 게 **원본에 없어서인지, 필터가 지워서인지** 가른다.
    """
    import pandas as pd
    p = ROOT / "data" / "processed" / "games.csv"
    print(f"=== 0. games.csv 원본  ({p})")
    if not p.exists():
        print("  🔴 파일 없음")
        return
    g = pd.read_csv(p)
    print(f"  행수        : {len(g):,}")
    print(f"  연도 분포   : {dict(sorted(Counter(g['year'].tolist()).items()))}")
    cur = g[g["year"] == datetime.now().year]
    print(f"  {datetime.now().year}년 행수: {len(cur):,}")
    if not len(cur):
        print("  🔴 원본에 올해가 없다 — build_dataset 단계의 문제다")
        return
    # 필터를 하나씩 걸어 어디서 죽는지 본다
    steps = [
        ("is_void=False", ~cur["is_void"].astype(bool)),
        ("market_family∈{승패,승무패}", cur["market_family"].isin(["승패", "승무패"])),
        ("result∈{홈승,홈패,무승부}", cur["result"].isin(["홈승", "홈패", "무승부"])),
    ]
    m = pd.Series(True, index=cur.index)
    for name, cond in steps:
        m = m & cond
        print(f"    {name:<28} 남은 행 {int(m.sum()):,}")
    print(f"  올해 result 값 분포 : {dict(Counter(cur['result'].astype(str)).most_common(8))}")
    print(f"  올해 market_family  : {dict(Counter(cur['market_family'].astype(str)).most_common(8))}")


def main() -> int:
    season = datetime.now().year
    _raw()
    print(f"\n=== 1. 원본 이력 (season={season})")
    hist = load_history()
    print(f"  load_history 행수 : {len(hist):,}")
    if not len(hist):
        print("  🔴 이력이 비었다 — games.csv 를 확인할 것")
        return 1
    print(f"  컬럼              : {list(hist.columns)}")
    yrs = Counter(hist['year'].tolist())
    print(f"  연도 분포         : {dict(sorted(yrs.items()))}")
    n_season = int((hist['year'] == season).sum())
    print(f"  {season}년 행수     : {n_season:,}")
    if not n_season:
        print(f"  🔴 {season}년 경기가 0건 — build_forms 가 시즌으로 걸러 전부 버린다")

    print(f"\n=== 2. build_forms(season={season})")
    forms, h2h = build_forms(hist, season=season)
    print(f"  FORMS 엔트리      : {len(forms):,}")
    print(f"  H2H 엔트리        : {len(h2h):,}")
    if forms:
        lg_c = Counter(k[0] for k in forms)
        print(f"  리그별 팀 수      : {dict(lg_c.most_common(10))}")
        sample = list(forms.items())[:5]
        for (lg, tm), fm in sample:
            print(f"    ({lg}, {tm}) → {fm.w}승 {fm.l}패 {fm.d}무 · last10={len(fm.last10)}")

    print("\n=== 3. 화면 데이터와 이름이 맞나")
    p = ROOT / "docs" / "data" / "picks_v2.json"
    if not p.exists():
        print("  picks_v2.json 없음 — 건너뜀")
        return 0
    d = json.loads(p.read_text(encoding="utf-8"))
    games = (d.get("live") or []) + (d.get("past") or [])
    by_team = {}
    for (lg_, tm_), fm_ in forms.items():
        n_ = fm_.w + fm_.l + fm_.d
        if tm_ not in by_team or n_ > by_team[tm_][0]:
            by_team[tm_] = (n_, lg_)

    hit_lg = hit_pool = miss = 0
    miss_names: Counter = Counter()
    for g in games:
        for side in ("home", "away"):
            nm, lg = g[side], g["league"]
            if (lg, nm) in forms:
                hit_lg += 1
            elif nm in by_team:
                hit_pool += 1
            else:
                miss += 1
                miss_names[f"{lg}/{nm}"] += 1
    tot = hit_lg + hit_pool + miss
    print(f"  팀-경기 {tot}건 중")
    print(f"    (리그,팀) 정확 일치 : {hit_lg:,} ({hit_lg/tot*100:.0f}%)")
    print(f"    팀 이름만 일치(풀링): {hit_pool:,} ({hit_pool/tot*100:.0f}%)")
    print(f"    아예 못 찾음        : {miss:,} ({miss/tot*100:.0f}%)")
    if miss_names:
        print("  못 찾은 이름 상위 15:")
        for k, v in miss_names.most_common(15):
            print(f"    {k}  x{v}")
    if forms:
        print("\n  참고 — FORMS 에 있는 이름 표본 20개:")
        print("   ", ", ".join(sorted({k[1] for k in forms})[:20]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
