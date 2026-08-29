"""팀·시즌·일정 컨텍스트 피처.

달력의 요일을 결과 신호로 외우지 않는다. 날짜는 경기 전 시점의 휴식, 일정 밀도,
시즌 단계와 표본 신뢰도를 계산하는 키다. 모든 집계는 ``as_of`` 미만 경기만 사용해
결과 누수를 막는다. 이 모듈의 출력은 shadow/backtest 용이며 검증 전 배당 확률을
직접 가감하지 않는다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
from collections import defaultdict, deque
from datetime import datetime, timezone
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TeamSeasonProfile:
    league: str
    team: str
    season: int
    games: int
    reliability: float
    points_rate: float | None
    score_margin: float | None
    attack_rate: float | None
    defence_rate: float | None
    volatility: float | None
    close_rate: float | None
    home_margin: float | None
    away_margin: float | None
    recent_margin: float | None
    recent_slope: float | None
    rest_days: int | None
    games_7d: int
    games_14d: int


def _season_rows(history: pd.DataFrame, league: str, season: int,
                 as_of: pd.Timestamp) -> pd.DataFrame:
    rows = history.copy()
    rows["date"] = pd.to_datetime(rows["date"])
    return rows[(rows["league"] == league) & (rows["year"] == season)
                & (rows["date"] < as_of)].sort_values("date")


def _team_games(rows: pd.DataFrame, team: str) -> pd.DataFrame:
    return rows[(rows["home_team"] == team) | (rows["away_team"] == team)]


def _team_view(games: pd.DataFrame, team: str) -> pd.DataFrame:
    if games.empty:
        return pd.DataFrame(columns=["date", "venue", "gf", "ga", "points", "margin"])
    home = games["home_team"] == team
    out = pd.DataFrame({
        "date": games["date"],
        "venue": home.map({True: "home", False: "away"}),
        "gf": games["home_score"].where(home, games["away_score"]).astype(float),
        "ga": games["away_score"].where(home, games["home_score"]).astype(float),
    })
    out["margin"] = out["gf"] - out["ga"]
    out["points"] = out["margin"].map(lambda x: 1.0 if x > 0 else (0.5 if x == 0 else 0.0))
    return out.sort_values("date")


def _mean(series: pd.Series) -> float | None:
    return float(series.mean()) if len(series) else None


def _slope(values: pd.Series) -> float | None:
    if len(values) < 4:
        return None
    y = values.astype(float).to_list()
    x_bar = (len(y) - 1) / 2
    denominator = sum((x - x_bar) ** 2 for x in range(len(y)))
    return sum((x - x_bar) * (value - sum(y) / len(y))
               for x, value in enumerate(y)) / denominator


def team_profile(history: pd.DataFrame, league: str, team: str,
                 as_of: pd.Timestamp, season: int | None = None,
                 prior_games: float = 8.0) -> TeamSeasonProfile:
    """현재 시즌 팀 특성을 리그 평균으로 축소해 계산한다.

    ``reliability=n/(n+prior_games)``를 함께 내보내 새 시즌·승격팀·표본 부족 팀의
    과잉반응을 막는다. 축소 강도 자체도 추후 워크포워드에서 튜닝해야 한다.
    """
    as_of = pd.Timestamp(as_of)
    season = int(season or as_of.year)
    league_rows = _season_rows(history, league, season, as_of)
    games = _team_games(league_rows, team)
    view = _team_view(games, team)
    n = len(view)
    reliability = n / (n + prior_games) if n else 0.0

    # 팀 경기당 지표를 같은 리그의 경기당 평균으로 부분 풀링한다.
    league_gf = (
        league_rows["home_score"].sum() + league_rows["away_score"].sum()
    ) / max(1, 2 * len(league_rows))

    def shrink(value: float | None, prior: float) -> float | None:
        return None if value is None else reliability * value + (1.0 - reliability) * prior

    margins = view["margin"]
    recent = view.tail(10)
    last_date = view["date"].max() if n else None
    rest = int((as_of.normalize() - last_date.normalize()).days) if last_date is not None else None
    home_margin = _mean(view.loc[view["venue"] == "home", "margin"])
    away_margin = _mean(view.loc[view["venue"] == "away", "margin"])
    return TeamSeasonProfile(
        league=league, team=team, season=season, games=n, reliability=round(reliability, 4),
        points_rate=shrink(_mean(view["points"]), 0.5),
        score_margin=shrink(_mean(margins), 0.0),
        attack_rate=shrink(_mean(view["gf"]), float(league_gf)),
        defence_rate=shrink(_mean(view["ga"]), float(league_gf)),
        volatility=_mean(margins.abs()),
        close_rate=_mean((margins.abs() <= 2).astype(float)) if n else None,
        home_margin=home_margin, away_margin=away_margin,
        recent_margin=_mean(recent["margin"]), recent_slope=_slope(recent["margin"]),
        rest_days=rest,
        games_7d=int((view["date"] >= as_of - pd.Timedelta(days=7)).sum()),
        games_14d=int((view["date"] >= as_of - pd.Timedelta(days=14)).sum()),
    )


def match_context(history: pd.DataFrame, fixture: dict,
                  *, data_updated_at: pd.Timestamp | None = None) -> dict:
    """두 팀 프로필과 예외상황을 묶고 적용 가능 수준을 판정한다."""
    kickoff = pd.Timestamp(fixture["date"])
    league, season = str(fixture["league"]), int(fixture.get("year") or kickoff.year)
    home = team_profile(history, league, str(fixture["home_team"]), kickoff, season)
    away = team_profile(history, league, str(fixture["away_team"]), kickoff, season)
    flags: list[str] = []
    if min(home.games, away.games) < 5:
        flags.append("low_season_sample")
    if fixture.get("neutral_venue"):
        flags.append("neutral_venue")
    if fixture.get("competition_stage") in {"knockout", "playoff", "final"}:
        flags.append("non_regular_stage")
    if fixture.get("lineup_status") not in {None, "confirmed"}:
        flags.append("lineup_unconfirmed")
    if fixture.get("weather_status") == "severe":
        flags.append("severe_weather")
    if data_updated_at is not None:
        age_hours = (kickoff - pd.Timestamp(data_updated_at)).total_seconds() / 3600
        if age_hours > 24:
            flags.append("stale_context_data")
    reliability = min(home.reliability, away.reliability)
    mode = "full" if reliability >= 0.6 and not flags else ("partial" if reliability >= 0.25 else "market_only")
    return {
        "as_of": kickoff.isoformat(), "league": league, "season": season,
        "calendar": {"weekday": kickoff.day_name(), "month": kickoff.month,
                     "season_progress_hint": kickoff.dayofyear / (366 if kickoff.is_leap_year else 365)},
        "home": asdict(home), "away": asdict(away),
        "differences": {
            "score_margin": _difference(home.score_margin, away.score_margin),
            "recent_margin": _difference(home.recent_margin, away.recent_margin),
            "rest_days": _difference(home.rest_days, away.rest_days),
            "schedule_7d": home.games_7d - away.games_7d,
        },
        "exceptions": flags, "application_mode": mode,
        "rule": "요일 자체는 설명 변수로 쓰지 않고 휴식·밀집·이동·환경의 키로만 사용",
    }


def _difference(left: float | int | None, right: float | int | None) -> float | None:
    if left is None or right is None:
        return None
    value = float(left) - float(right)
    return value if math.isfinite(value) else None


sys.path.insert(0, str(Path(__file__).resolve().parent))
from free_context import exp_workload, haversine_km  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VENUES = ROOT / "data" / "static" / "venues.csv"
VENUE_OVERRIDES = ROOT / "data" / "static" / "venue_overrides.csv"
SCHEDULE_OUT = ROOT / "data" / "processed" / "schedule_context.csv"
LINEUP_OUT = ROOT / "data" / "processed" / "lineup_workload.csv"


def load_team_venues() -> dict[tuple[str, str], dict]:
    df = pd.read_csv(VENUES)
    required = {"league", "team", "venue_id", "latitude", "longitude", "roof"}
    if not required.issubset(df.columns):
        raise ValueError(f"경기장 필드 부족: {sorted(required - set(df.columns))}")
    return {(r.league, r.team): r._asdict() for r in df.itertuples(index=False)}


def load_venue_overrides() -> list[dict]:
    if not VENUE_OVERRIDES.exists():
        return []
    df = pd.read_csv(VENUE_OVERRIDES, parse_dates=["valid_from", "valid_to"])
    return df.to_dict("records")


def resolve_venue(
    venue_map: dict[tuple[str, str], dict],
    key: tuple[str, str],
    at: datetime,
    overrides: list[dict],
) -> dict | None:
    """기본 홈구장 위에 날짜가 맞는 임시·과거 홈구장을 덮는다."""
    day = pd.Timestamp(at).tz_convert("UTC").tz_localize(None).normalize()
    eligible = [
        row for row in overrides
        if (row["league"], row["team"]) == key
        and pd.Timestamp(row["valid_from"]) <= day <= pd.Timestamp(row["valid_to"])
    ]
    if eligible:
        # 짧은 특별 시리즈가 시즌 전체 override보다 우선한다.
        return min(eligible, key=lambda x: pd.Timestamp(x["valid_to"]) - pd.Timestamp(x["valid_from"]))
    return venue_map.get(key)


def _dt(value) -> datetime:
    x = pd.Timestamp(value)
    if x.tzinfo is None:
        x = x.tz_localize("Asia/Seoul")
    return x.tz_convert("UTC").to_pydatetime()


def build_schedule_context(
    matches: pd.DataFrame,
    venues: dict | None = None,
    overrides: list[dict] | None = None,
) -> pd.DataFrame:
    """경기 직전 이동·혼잡도. 첫 경기 이동은 팀 기본 홈구장에서 출발한다고 본다."""
    venue_map = venues or load_team_venues()
    venue_overrides = load_venue_overrides() if overrides is None else overrides
    m = matches.sort_values(["date", "league", "home_team"]).reset_index(drop=True)
    last_at: dict[tuple[str, str], datetime] = {}
    last_coord: dict[tuple[str, str], tuple[float, float]] = {}
    recent: dict[tuple[str, str], deque] = defaultdict(deque)
    road_streak: dict[tuple[str, str], int] = defaultdict(int)
    rows = []

    for r in m.itertuples():
        now = _dt(r.date)
        home_key = (r.league, r.home_team)
        away_key = (r.league, r.away_team)
        # 시즌 휴식기를 '371일 휴식 우위'나 '원정 10연전'으로 해석하면 모델이
        # 확률 0/1까지 폭주한다. 30일 넘게 경기가 없으면 새 컨텍스트로 리셋한다.
        for key in (home_key, away_key):
            if key in last_at and (now - last_at[key]).total_seconds() > 30 * 86400:
                last_at.pop(key, None)
                last_coord.pop(key, None)
                recent[key].clear()
                road_streak[key] = 0
        venue = resolve_venue(venue_map, home_key, now, venue_overrides)
        current = ((float(venue["latitude"]), float(venue["longitude"])) if venue else None)

        def rest(key):
            if key not in last_at:
                return np.nan
            return (now - last_at[key]).total_seconds() / 86400.0

        def n_recent(key, days):
            q = recent[key]
            while q and (now - q[0]).total_seconds() > 14 * 86400:
                q.popleft()
            return sum(0 < (now - x).total_seconds() <= days * 86400 for x in q)

        def origin(key):
            if key in last_coord:
                return last_coord[key]
            base = resolve_venue(venue_map, key, now, venue_overrides)
            if base:
                return float(base["latitude"]), float(base["longitude"])
            return None

        def travel(key):
            start = origin(key)
            if start is None or current is None:
                return np.nan
            return haversine_km(start[0], start[1], current[0], current[1])

        rh, ra = rest(home_key), rest(away_key)
        th, ta = travel(home_key), travel(away_key)
        h3, a3 = n_recent(home_key, 3), n_recent(away_key, 3)
        h7, a7 = n_recent(home_key, 7), n_recent(away_key, 7)
        h14, a14 = n_recent(home_key, 14), n_recent(away_key, 14)
        rows.append({
            "date": pd.Timestamp(r.date), "league": r.league,
            "home_team": r.home_team, "away_team": r.away_team,
            "venue_id": venue.get("venue_id") if venue else None,
            "venue_roof": venue.get("roof") if venue else None,
            "rest_home": rh, "rest_away": ra,
            "rest_diff": rh - ra if np.isfinite(rh) and np.isfinite(ra) else np.nan,
            "travel_home_km": th, "travel_away_km": ta,
            "travel_diff_km": ta - th if np.isfinite(th) and np.isfinite(ta) else np.nan,
            "games_3d_home": h3, "games_3d_away": a3,
            "games_3d_diff": a3 - h3,
            "games_7d_home": h7, "games_7d_away": a7,
            "games_7d_diff": a7 - h7,
            "games_14d_home": h14, "games_14d_away": a14,
            "games_14d_diff": a14 - h14,
            "road_streak_home": road_streak[home_key],
            "road_streak_away": road_streak[away_key],
            "road_streak_diff": road_streak[away_key] - road_streak[home_key],
            "travel_quality": "default_home_venue" if current else "missing_venue",
        })

        for key in (home_key, away_key):
            last_at[key] = now
            recent[key].append(now)
            if current is not None:
                last_coord[key] = current
        road_streak[home_key] = 0
        road_streak[away_key] += 1
    return pd.DataFrame(rows)


def build_lineup_workload(lineups: pd.DataFrame, tau_days: float = 7.0) -> pd.DataFrame:
    """현재 선발 XI가 직전 경기들에서 쌓은 추정 부하를 경기 전 시점에 계산한다."""
    required = {"date", "team", "opp", "is_home", "xi"}
    if not required.issubset(lineups.columns):
        raise ValueError(f"라인업 필드 부족: {sorted(required - set(lineups.columns))}")
    lu = lineups.sort_values(["date", "team"]).reset_index(drop=True)
    history: dict[tuple[str, str], list[tuple[datetime, float]]] = defaultdict(list)
    rows = []
    for r in lu.itertuples():
        now = _dt(r.date)
        values = [exp_workload(history[(r.team, str(pid))], now, tau_days) for pid in r.xi]
        rows.append({
            "date": pd.Timestamp(r.date), "team": r.team, "opp": r.opp,
            "is_home": bool(r.is_home),
            "xi_workload_mean": float(np.mean(values)) if values else np.nan,
            "xi_workload_max": float(np.max(values)) if values else np.nan,
            "xi_fresh_share": float(np.mean([x < 45 for x in values])) if values else np.nan,
            "workload_tau_days": tau_days,
            "workload_quality": "starter_90min_proxy_postgame_reconstruction",
        })
        for pid in r.xi:
            history[(r.team, str(pid))].append((now, 90.0))
    return pd.DataFrame(rows)


def _selftest() -> int:
    matches = pd.DataFrame([
        {"date": "2026-08-01", "league": "K리그1", "home_team": "FC서울", "away_team": "전북현대"},
        {"date": "2026-08-04", "league": "K리그1", "home_team": "포항스틸", "away_team": "FC서울"},
        {"date": "2026-08-08", "league": "K리그1", "home_team": "울산HDFC", "away_team": "FC서울"},
    ])
    s = build_schedule_context(matches)
    assert len(s) == 3
    assert s.loc[1, "rest_away"] == 3
    assert s.loc[2, "road_streak_away"] == 1
    assert s.loc[1, "travel_away_km"] > 0

    mlb = pd.DataFrame([
        {"date": "2024-07-01", "league": "MLB", "home_team": "애슬레틱", "away_team": "LA다저스"},
        {"date": "2025-07-01", "league": "MLB", "home_team": "애슬레틱", "away_team": "LA다저스"},
        {"date": "2025-07-02", "league": "MLB", "home_team": "탬파레이", "away_team": "뉴욕양키"},
    ])
    ms = build_schedule_context(mlb)
    assert ms.loc[0, "venue_id"] == "oakland_coliseum"
    assert ms.loc[1, "venue_id"] == "sutter_health_park"
    assert ms.loc[2, "venue_id"] == "steinbrenner_field"

    xi = tuple(str(i) for i in range(11))
    lu = pd.DataFrame([
        {"date": "2026-08-01", "team": "서울", "opp": "전북", "is_home": True, "xi": xi},
        {"date": "2026-08-04", "team": "서울", "opp": "포항", "is_home": False, "xi": xi},
    ])
    w = build_lineup_workload(lu)
    assert w.loc[0, "xi_workload_mean"] == 0
    assert 50 < w.loc[1, "xi_workload_mean"] < 70
    print("✅ 무료 컨텍스트 자기검사 통과 (이동·휴식·연속원정·선수 workload)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="K리그1")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()

    from matches import load_matches
    from lineup_soccer import load as load_lineups

    matches = load_matches()
    matches = matches[matches["league"] == args.league]
    schedule = build_schedule_context(matches)
    SCHEDULE_OUT.parent.mkdir(parents=True, exist_ok=True)
    schedule.to_csv(SCHEDULE_OUT, index=False)
    print(f"일정·이동 피처 {len(schedule):,}행 → {SCHEDULE_OUT}")

    if args.league == "K리그1":
        workload = build_lineup_workload(load_lineups())
        workload.to_csv(LINEUP_OUT, index=False)
        print(f"선수 workload {len(workload):,}행 → {LINEUP_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
