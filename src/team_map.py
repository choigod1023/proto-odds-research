"""프로토 팀명 ↔ 네이버 팀명 자동 매핑.

문제
----
프로토는 팀명을 4글자로 줄인다(`필라필리`, `뉴욕양키`, `LA다저스`).
네이버는 정식 표기를 쓴다(`필라델피아`, `뉴욕 양키스`, `LA다저스`).
KBO는 둘 다 짧아서 그냥 맞지만, MLB·NPB는 문자열로는 이어지지 않는다.

해결 — 경기 결과로 잇는다
-------------------------
같은 날 같은 스코어인 경기는 같은 경기일 가능성이 매우 높다.
날짜 + (홈점수, 원정점수) 로 후보를 맞춘 뒤, 팀명 쌍의 **동시 출현 횟수**를 세고
가장 많이 함께 나온 이름을 대응으로 확정한다.

이름 표기 규칙을 추측하지 않고 **데이터가 매핑을 만들게** 한다.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
OUT = Path(__file__).resolve().parent.parent / "data" / "processed" / "team_map.json"

# 프로토 리그명 → 네이버 선발 파일
LEAGUE_FILE = {"KBO": "kbo_starters.json", "MLB": "mlb_starters.json",
               "NPB": "npb_starters.json"}

MIN_SUPPORT = 3        # 이만큼 이상 함께 나와야 대응으로 인정


def load_naver(league: str) -> pd.DataFrame:
    p = RAW / LEAGUE_FILE[league]
    df = pd.DataFrame(json.loads(p.read_text(encoding="utf-8")))
    df = df[(df["home_starter"] != "") & (df["away_starter"] != "")]
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df.dropna(subset=["date", "home_score", "away_score"])


def build_map(league: str) -> dict:
    from matches import load_matches
    proto = load_matches()
    proto = proto[proto["league"] == league]
    nav = load_naver(league)

    # (날짜, 홈점수, 원정점수) → 네이버 경기들
    idx: dict = defaultdict(list)
    for r in nav.itertuples():
        idx[(r.date.date(), int(r.home_score), int(r.away_score))].append(r)

    pair = Counter()
    ambiguous = 0
    for r in proto.itertuples():
        key = (r.date.date(), int(r.home_score), int(r.away_score))
        cands = idx.get(key, [])
        if len(cands) != 1:
            # 같은 날 같은 스코어가 여럿이면 어느 쪽인지 알 수 없다 → 버린다
            ambiguous += len(cands) > 1
            continue
        c = cands[0]
        pair[("H", r.home_team, c.home)] += 1
        pair[("A", r.away_team, c.away)] += 1

    # 프로토 팀 → 가장 많이 함께 나온 네이버 팀
    votes: dict = defaultdict(Counter)
    for (_side, pteam, nteam), n in pair.items():
        votes[pteam][nteam] += n

    mapping, weak = {}, []
    for pteam, c in votes.items():
        nteam, n = c.most_common(1)[0]
        total = sum(c.values())
        if n >= MIN_SUPPORT and n / total >= 0.6:
            mapping[pteam] = nteam
        else:
            weak.append((pteam, c.most_common(3), total))

    print(f"[{league}] 프로토 {proto['home_team'].nunique()}팀 · "
          f"매핑 확정 {len(mapping)} · 모호 {len(weak)} · 스코어중복 스킵 {ambiguous}")
    for pteam, top, total in weak[:8]:
        print(f"    ⚠️ {pteam}: {top} (총 {total})")
    return mapping


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    leagues = argv[1:] or ["KBO", "MLB", "NPB"]
    out = {}
    for lg in leagues:
        if not (RAW / LEAGUE_FILE.get(lg, "")).exists():
            print(f"[{lg}] 선발 파일 없음 — 건너뜀")
            continue
        out[lg] = build_map(lg)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {OUT}")
    for lg, m in out.items():
        sample = list(m.items())[:5]
        print(f"  {lg}: {len(m)}팀  예) " +
              ", ".join(f"{a}→{b}" for a, b in sample))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
