"""팀·시즌·일정 컨텍스트 피처.

달력의 요일을 결과 신호로 외우지 않는다. 날짜는 경기 전 시점의 휴식, 일정 밀도,
시즌 단계와 표본 신뢰도를 계산하는 키다. 모든 집계는 ``as_of`` 미만 경기만 사용해
결과 누수를 막는다. 이 모듈의 출력은 shadow/backtest 용이며 검증 전 배당 확률을
직접 가감하지 않는다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

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
