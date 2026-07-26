"""게임행 → 베팅 레코드 변환.

각 게임행은 선택지 2~3개를 갖는다. 선택지 하나하나가 '1,000원 걸 수 있는 대상'이므로
(배당, 적중여부) 쌍으로 펼쳐야 수익률 계산이 가능하다.

결과 라벨 → 배당 인덱스 매핑 (2026-07-26 실측 검증):
    승패(2-way)      [홈, 원정]              홈승=0, 홈패=1
    승무패(3-way)     [홈, 무, 원정]          홈승=0, 무승부=1, 홈패=2
    언더오버(2-way)   [오버, 언더]            오버=0, 언더=1
    핸디캡(2-way)     [핸디승, 핸디패]         핸디승=0, 핸디패=1
    핸디캡(3-way)     [핸디승, 핸디무, 핸디패]  핸디승=0, 핸디무=1, 핸디패=2
    승①패(3-way)     [홈2점차+승, 1점차, 원정2점차+승]  홈승=0, ①=1, 홈패=2
"""
from __future__ import annotations

from dataclasses import dataclass

# (n_way, 결과라벨) → 적중 인덱스
_WINNER: dict[tuple[int, str], int] = {
    (2, "홈승"): 0, (2, "홈패"): 1,
    (2, "오버"): 0, (2, "언더"): 1,
    (2, "핸디승"): 0, (2, "핸디패"): 1,
    (3, "홈승"): 0, (3, "무승부"): 1, (3, "홈패"): 2,
    (3, "핸디승"): 0, (3, "핸디무"): 1, (3, "핸디패"): 2,
    (3, "①"): 1,
}

# 선택지 인덱스 → 사람이 읽는 이름
_SEL_NAMES = {
    ("승패", 2): ("홈", "원정"),
    ("언더오버", 2): ("오버", "언더"),
    ("핸디캡", 2): ("핸디홈", "핸디원정"),
    ("승무패", 3): ("홈", "무", "원정"),
    ("핸디캡", 3): ("핸디홈", "핸디무", "핸디원정"),
    ("승①패", 3): ("홈2+", "1점차", "원정2+"),
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
        names = _SEL_NAMES.get((r.market_family, r.n_way))
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
