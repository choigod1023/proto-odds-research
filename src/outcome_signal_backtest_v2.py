"""결과와 무관한 팀명 교차표를 쓰는 배당 신호 백테스트 v2.

`outcome_signal_backtest.py`의 프로토 이동 검정은 그대로 재사용하되, 해외 경기
결합은 날짜+최종스코어 추론을 금지하고 명시적 팀명 교차표만 사용한다.
"""
from __future__ import annotations

import json

import pandas as pd

import outcome_signal_backtest as core


TEAM_ALIASES = {
    ("KBO", "Doosan Bears"): "두산", ("KBO", "Hanwha Eagles"): "한화",
    ("KBO", "KIA Tigers"): "KIA", ("KBO", "KT Wiz Suwon"): "KT",
    ("KBO", "Kiwoom Heroes"): "키움", ("KBO", "LG Twins"): "LG",
    ("KBO", "Lotte Giants"): "롯데", ("KBO", "NC Dinos"): "NC",
    ("KBO", "SSG Landers"): "SSG", ("KBO", "Samsung Lions"): "삼성",

    ("NPB", "Chiba Lotte Marines"): "지바롯데",
    ("NPB", "Chunichi Dragons"): "주니치",
    ("NPB", "Fukuoka S. Hawks"): "소프트뱅",
    ("NPB", "Hanshin Tigers"): "한신", ("NPB", "Hiroshima Carp"): "히로카프",
    ("NPB", "Nippon Ham Fighters"): "닛폰햄",
    ("NPB", "Orix Buffaloes"): "오릭스",
    ("NPB", "Rakuten Gold. Eagles"): "라쿠텐",
    ("NPB", "Seibu Lions"): "세이부", ("NPB", "Yakult Swallows"): "야쿠르트",
    ("NPB", "Yokohama BayStars"): "요코베이",
    ("NPB", "Yomiuri Giants"): "요미우리",

    ("K리그1", "Anyang"): "FC안양", ("K리그1", "Bucheon FC 1995"): "부천FC",
    ("K리그1", "Daejeon"): "대전하나", ("K리그1", "Gangwon"): "강원FC",
    ("K리그1", "Gimcheon Sangmu"): "김천상무",
    ("K리그1", "Gwangju FC"): "광주FC", ("K리그1", "Incheon"): "인천유나",
    ("K리그1", "Jeju SK"): "제주SKFC", ("K리그1", "Jeonbuk"): "전북현대",
    ("K리그1", "Pohang"): "포항스틸", ("K리그1", "Seoul"): "FC서울",
    ("K리그1", "Ulsan HD"): "울산HDFC",

    ("MLB", "Arizona Diamondbacks"): "애리다이", ("MLB", "Athletics"): "애슬레틱",
    ("MLB", "Atlanta Braves"): "애틀브레", ("MLB", "Baltimore Orioles"): "볼티오리",
    ("MLB", "Boston Red Sox"): "보스레드", ("MLB", "Chicago Cubs"): "시카컵스",
    ("MLB", "Chicago White Sox"): "시카화이", ("MLB", "Cincinnati Reds"): "신시레즈",
    ("MLB", "Cleveland Guardians"): "클리가디", ("MLB", "Colorado Rockies"): "콜로로키",
    ("MLB", "Detroit Tigers"): "디트타이", ("MLB", "Houston Astros"): "휴스애스",
    ("MLB", "Kansas City Royals"): "캔자로얄",
    ("MLB", "Los Angeles Angels"): "LA에인절",
    ("MLB", "Los Angeles Dodgers"): "LA다저스", ("MLB", "Miami Marlins"): "마이말린",
    ("MLB", "Milwaukee Brewers"): "밀워브루", ("MLB", "Minnesota Twins"): "미네트윈",
    ("MLB", "New York Mets"): "뉴욕메츠", ("MLB", "New York Yankees"): "뉴욕양키",
    ("MLB", "Philadelphia Phillies"): "필라필리",
    ("MLB", "Pittsburgh Pirates"): "피츠파이", ("MLB", "San Diego Padres"): "샌디파드",
    ("MLB", "San Francisco Giants"): "샌프자이",
    ("MLB", "Seattle Mariners"): "시애매리", ("MLB", "St.Louis Cardinals"): "세인카디",
    ("MLB", "Tampa Bay Rays"): "탬파레이", ("MLB", "Texas Rangers"): "텍사레인",
    ("MLB", "Toronto Blue Jays"): "토론블루",
    ("MLB", "Washington Nationals"): "워싱내셔",
}


def load_live_odds() -> tuple[pd.DataFrame, dict]:
    file = core.OVERSEAS_DIR / "live_odds.csv"
    live = pd.read_csv(file, dtype=str, on_bad_lines="skip", engine="python")
    raw_rows = len(live)
    live["observed_at"] = pd.to_datetime(live["observed_at"], errors="coerce", utc=True)
    live = live.dropna(subset=["observed_at", "league", "home_en", "away_en", "odds"])
    keys = ["observed_at", "league", "home_en", "away_en"]
    variants = live.groupby(keys, dropna=False)["odds"].transform("nunique")
    conflicting = variants > 1
    # 같은 시각·팀 조합에 서로 다른 가격이 있으면 연전의 복수 경기인데 경기 ID가 없다.
    live = live[~conflicting].drop_duplicates(keys, keep="last").copy()
    return live, {
        "raw_rows": raw_rows,
        "conflicting_fixture_rows_removed": int(conflicting.sum()),
        "usable_rows_after_conflicts": int(len(live)),
    }


def coverage_at(
    proto_prices: pd.DataFrame,
    mapped: pd.DataFrame,
    cutoff_min: int,
    stale_min: int = 35,
) -> dict:
    if proto_prices.empty or mapped.empty:
        return {"overseas_events_at_cutoff": 0, "joined_events": 0}
    join_key = ["event_id", "n_way", "market_family"]
    if (not set(join_key).issubset(proto_prices.columns)
            or not set(join_key).issubset(mapped.columns)):
        return {"overseas_events_at_cutoff": 0, "joined_events": 0}
    proto = proto_prices[
        proto_prices["market_family"].eq(
            proto_prices["n_way"].map(core.EXTERNAL_MARKET_BY_NWAY))
    ]
    live = mapped[
        mapped["market_family"].eq(
            mapped["n_way"].map(core.EXTERNAL_MARKET_BY_NWAY))
    ]
    target = live["kickoff"] - pd.to_timedelta(cutoff_min, unit="m")
    age = target - live["observed_at"]
    os_at = live[(age >= pd.Timedelta(0)) & (age <= pd.Timedelta(minutes=stale_min))]
    os_at = os_at.groupby(join_key, sort=False).tail(1)
    joined = proto[join_key].drop_duplicates().merge(
        os_at[join_key].drop_duplicates(), on=join_key, how="inner",
        validate="one_to_one",
    )
    return {
        "overseas_events_at_cutoff": int(os_at["event_id"].nunique()),
        "joined_events": int(joined["event_id"].nunique()),
    }


def external_analysis(data: pd.DataFrame, settled: pd.DataFrame) -> dict:
    events = core.proto_events_for_aliases(settled)
    live, live_meta = load_live_odds()

    proto_teams = {
        (row.league, team)
        for row in events.itertuples(index=False)
        for team in (row.home_team, row.away_team)
    }
    bad_alias_values = [
        f"{league}|{english}->{korean}"
        for (league, english), korean in TEAM_ALIASES.items()
        if (league, korean) not in proto_teams
    ]
    mapped, map_meta = core.map_live_to_events(live, events, TEAM_ALIASES)

    results = []
    coverage = {}
    for cutoff in (360, 90, 30, 10):
        proto = core.proto_prices_at(data, settled, cutoff)
        coverage[str(cutoff)] = coverage_at(proto, mapped, cutoff)
        for min_ev in (0.0, 0.02, 0.05):
            bets = core.external_gap_bets(proto, mapped, cutoff, min_ev)
            results.append(core.summarize(bets, f"T-{cutoff}|EV>{min_ev:.0%}"))

    primary_proto = core.proto_prices_at(data, settled, 30)
    primary = core.external_gap_bets(primary_proto, mapped, 30, 0.0)
    samples = []
    for row in primary.head(10).itertuples(index=False):
        samples.append({
            "event_id": row.event_id,
            "selection": int(row.selection),
            "odds": float(row.odds),
            "estimated_ev": float(row.estimated_ev),
            "hit": int(row.hit),
            "ret": float(row.ret),
        })
    return {
        "definition": "명시적 팀명 매핑 + 동시점 해외 devig 확률 × 구매 가능한 프로토 배당 - 1",
        "primary_rule": "T-30, 추정 EV>0, 한 실제 경기당 최대 1회",
        "aliases": len(TEAM_ALIASES),
        "bad_alias_values": bad_alias_values,
        "live_meta": live_meta,
        "mapping_meta": map_meta,
        "coverage": coverage,
        "primary": core.summarize(primary, "주 검정: T-30 해외기준 EV>0"),
        "primary_samples": samples,
        "sensitivity": results,
    }


def main() -> int:
    data, source_meta = core.load_snapshots()
    settled = core.settled_markets(data)
    report = {
        "generated_from": "local historical snapshots; no network",
        "source": source_meta,
        "settled_markets": int(len(settled)),
        "settled_events": int(settled["event_id"].nunique()),
        "movement": core.movement_analysis(data, settled),
        "external_gap": external_analysis(data, settled),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
