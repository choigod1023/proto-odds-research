"""게임행 → 베팅 레코드 변환.

각 게임행은 선택지 2~3개를 갖는다. 선택지 하나하나가 '1,000원 걸 수 있는 대상'이므로
(배당, 적중여부) 쌍으로 펼쳐야 수익률 계산이 가능하다.

결과 라벨 → 배당 인덱스 매핑 (2026-07-26, `verify_mapping.py`로 데이터 확정):
    승패(2-way)      [홈, 원정]              홈승=0, 홈패=1
    승무패(3-way)     [홈, 무, 원정]          홈승=0, 무승부=1, 홈패=2
    언더오버(2-way)   [언더, 오버]            언더=0, 오버=1   ⚠️ 아래 주의 참조
    핸디캡(2-way)     [핸디승, 핸디패]         핸디승=0, 핸디패=1
    핸디캡(3-way)     [핸디승, 핸디무, 핸디패]  핸디승=0, 핸디무=1, 핸디패=2
    승①패(3-way)     [홈2점차+승, 1점차, 원정2점차+승]  홈승=0, ①=1, 홈패=2

⚠️ 언더오버 순서 주의
    초판에서 `[오버, 언더]`로 잘못 잡아 전체의 26%(46,088행)를 오염시켰다.
    화면상 첫 배당이 '오버'처럼 보이지만 실제로는 **언더**다. 마켓 라벨이 `U 2.5`인 것도
    첫 칸이 **U**nder라는 표시다. 확정 근거:
      · 언더 베팅 ROI  −12.36% (이론 −12.00%)  vs  뒤집으면 −9.22%
      · 라인 0.5처럼 낮은 라인에서 두 번째 배당이 1.54로 낮음(= 오버가 유력)
    **눈으로 순서를 추정하지 말 것.** 반드시 `verify_mapping.py` 로 확인한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dataclasses import dataclass

# (n_way, 결과라벨) → 적중 인덱스
_WINNER: dict[tuple[int, str], int] = {
    (2, "홈승"): 0, (2, "홈패"): 1,
    (2, "언더"): 0, (2, "오버"): 1,
    (2, "핸디승"): 0, (2, "핸디패"): 1,
    (3, "홈승"): 0, (3, "무승부"): 1, (3, "홈패"): 2,
    (3, "핸디승"): 0, (3, "핸디무"): 1, (3, "핸디패"): 2,
    (3, "①"): 1,
    # ⚠️ 2026-07-28 추가. 아래 둘이 빠져 있어 **조용히 버려지고 있었다.**
    #    같은 누락이 market_scan.WIN_IDX 에도 있었고, 그쪽에서 KBL 가짜 ROI +30% 가
    #    나왔다. 매핑 테이블이 두 군데 있으면 한쪽만 고치게 된다 — 실제로 그랬다.
    (3, "⑤"): 1,                      # 승⑤패 '5점차 이내' (농구·배구)
    (2, "홀"): 0, (2, "짝"): 1,        # 홀짝(SUM)
}

# 선택지 인덱스 → 사람이 읽는 이름
# ⚠️ 선택지 이름의 **단일 정본**. 예전엔 이 표가 bets·generate_today·generate_v2 에
#    따로 있었고(4중 사본), 승⑤패·홀짝을 추가할 때 한 군데만 고쳐 사이트에는
#    'sel0/sel1' 이 그대로 나갔다. 새 마켓은 여기에만 추가한다.
SEL_NAMES = {
    ("승패", 2): ("홈", "원정"),
    ("언더오버", 2): ("언더", "오버"),
    ("핸디캡", 2): ("핸디홈", "핸디원정"),
    ("승무패", 3): ("홈", "무", "원정"),
    ("핸디캡", 3): ("핸디홈", "핸디무", "핸디원정"),
    ("승①패", 3): ("홈2+", "1점차", "원정2+"),
    ("승⑤패", 3): ("홈6+", "5점차이내", "원정6+"),
    ("홀짝", 2): ("홀", "짝"),
    # 전반전 한정 마켓 — 이름은 필요하지만 모델은 값을 안 매긴다(위 주석 참고)
    ("전반승패", 2): ("전반홈", "전반원정"),
    ("전반승무패", 3): ("전반홈", "전반무", "전반원정"),
    ("전반언더오버", 2): ("전반언더", "전반오버"),
    ("전반핸디캡", 2): ("전반핸디홈", "전반핸디원정"),
    ("전반핸디캡", 3): ("전반핸디홈", "전반핸디무", "전반핸디원정"),
}


@dataclass(frozen=True)
class Bet:
    year: int
    round: int
    game_no: str
    sport: str
    league: str
    market_family: str
    booking_class: str
    n_way: int
    overround: float
    selection: str      # '홈' / '오버' 등
    sel_index: int
    odds: float
    won: bool

    @property
    def implied(self) -> float:
        """배당에서 읽은 내재확률 (마진 포함)"""
        return 1.0 / self.odds

    @property
    def fair(self) -> float:
        """마진을 균등 제거한 확률 (multiplicative devig)"""
        return self.implied / self.overround

    @property
    def profit(self) -> float:
        """1원 베팅 시 손익. 적중이면 odds-1, 실패면 -1"""
        return (self.odds - 1.0) if self.won else -1.0


def winner_index(n_way: int, result: str) -> int | None:
    return _WINNER.get((n_way, result))


def to_bets(rows) -> list[Bet]:
    """정산 완료된 게임행만 베팅 레코드로 펼친다."""
    out: list[Bet] = []
    for r in rows:
        if r.is_void:
            continue
        ov = r.overround
        if ov is None or not (1.0 <= ov <= 1.40):
            continue
        wi = winner_index(r.n_way, r.result)
        if wi is None or wi >= r.n_way:
            continue  # 미정산·미인식 결과는 제외
        names = SEL_NAMES.get((r.market_family, r.n_way))
        for i, o in enumerate(r.odds):
            out.append(Bet(
                year=r.year, round=r.round, game_no=r.game_no,
                sport=r.sport, league=r.league,
                market_family=r.market_family, booking_class=r.booking_class,
                n_way=r.n_way, overround=ov,
                selection=names[i] if names and i < len(names) else f"sel{i}",
                sel_index=i, odds=o, won=(i == wi),
            ))
    return out


def _selftest() -> None:
    """결과값 매핑 커버리지 검사 — **실제 데이터에 있는 결과값을 다 덮는가.**

    ⚠️ 2026-07-28 에 이 누락으로 가짜를 만들었다.
       `market_scan.WIN_IDX` 에 `⑤` 가 없어 KBL 승⑤패의 중간 결과 32% 가
       조용히 버려졌고, 6점차 이상으로 갈린 경기만 남아 **가짜 ROI +30%** 가 나왔다.
       Bonferroni·부트스트랩·시간분리를 다 통과했는데도 가짜였다.

    핵심: **매핑에 없는 결과값은 예외 없이 조용히 사라진다.**
    그 누락이 결과값과 상관있으면 표본이 편향돼 반드시 가짜가 나온다.
    → 데이터에 실재하는 (n_way, result) 조합을 세어 매핑 커버리지를 본다.
    """
    import collections
    import pandas as pd

    path = Path(__file__).resolve().parent.parent / "data" / "processed" / "games.csv"
    if not path.exists():
        print("bets 커버리지 검사: games.csv 없음 — build_dataset.py 먼저")
        return

    g = pd.read_csv(path)
    g = g[~g["is_void"].astype(bool)]
    # 정산된 것으로 보이는 결과만 (경기전·인코딩깨짐 제외)
    SKIP = {"경기전", "하프타임", "취소", "연기", "중단", "무효", "nan"}
    cnt = collections.Counter()
    for nw, res in zip(g["n_way"], g["result"].astype(str)):
        try:
            nw = int(nw)
        except (TypeError, ValueError):
            continue
        if res in SKIP or not res.isprintable():
            continue
        cnt[(nw, res)] += 1

    total = sum(cnt.values())
    # ⚠️ n_way=0 은 배당 자체가 없는 행이다. 선택지가 없으니 매핑할 수 없고
    #    `overround is None` 에서 어차피 걸러진다. 정당한 제외이지 누락이 아니다.
    no_odds = {k: v for k, v in cnt.items() if k[0] < 2}
    missing = {k: v for k, v in cnt.items() if k[0] >= 2 and k not in _WINNER}
    # 인코딩 깨진 값은 별도로 센다 (한글 자모가 깨진 형태)
    broken = {k: v for k, v in missing.items()
              if not all("가" <= c <= "힣" or c in "①⑤" for c in k[1])}
    real = {k: v for k, v in missing.items() if k not in broken}

    print(f"bets 결과값 커버리지: 전체 {total:,}건")
    mapped = total - sum(missing.values()) - sum(broken.values()) - sum(no_odds.values())
    print(f"  매핑됨      {mapped:,} ({mapped/total:.2%})")
    print(f"  인코딩깨짐  {sum(broken.values()):,} (파서 문제, 무작위라 편향 없음)")
    print(f"  배당없음    {sum(no_odds.values()):,} (n_way=0, 매핑 불가·정당한 제외)")
    n_miss = sum(real.values())
    if real:
        # 0.1% 미만은 파싱 이상치로 보고 경고만. 그 이상이면 실패.
        # ⚠️ 알려진 이상치: 축월드컵 10건은 **스코어와 결과가 모순**이다
        #    (한국 2:1 체코 → '원정', 잉글랜드 1:2 아르헨티나 → '홈').
        #    매핑할 값이 아니라 파서가 잘못 읽은 것이다.
        lvl = "🔴 매핑 누락" if n_miss > total * 0.001 else "⚠️ 미매핑(이상치)"
        print(f"  {lvl} {n_miss:,}건 ({n_miss/total:.3%}):")
        for k, v in sorted(real.items(), key=lambda x: -x[1])[:8]:
            print(f"       n_way={k[0]} result={k[1]!r}  {v:,}건")
        if n_miss > total * 0.001:
            print("       → 매핑 테이블(_WINNER)에 추가하거나 원인을 규명할 것")
            raise SystemExit(1)
        print("  ✅ 누락이 0.1% 미만 — 편향을 만들 크기가 아니다")
    else:
        print("  ✅ 실제 결과값을 모두 덮는다")

if __name__ == "__main__":
    # ⚠️ --selftest 는 main() 보다 **먼저** 검사해야 한다.
    #    아래 순서가 뒤바뀌면 자기검사가 영영 안 돌아간다(실제로 그랬다).
    if "--selftest" in sys.argv:
        _selftest()
