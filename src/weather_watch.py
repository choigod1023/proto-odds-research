"""무료 Open-Meteo 경기장 예보 스냅샷 수집기.

API 키 없이 K리그·KBO·NPB·MLB 경기장의 시간별 예보를 6시간 간격으로 저장한다. 한 줄은
한 경기장의 한 수집시점 전체 예보다. 예보가 나중에 바뀌어도 과거 줄을 덮지 않아
T-24h/T-6h 시점의 정보를 그대로 재현할 수 있다.

Open-Meteo 무료 endpoint는 비상업 용도와 출처표시 조건이다. 상업 서비스로
전환하면 이 수집기를 기상청 API 또는 Open-Meteo 상업 endpoint로 바꿔야 한다.

사용:
    python3 src/weather_watch.py --league K리그1
    python3 src/weather_watch.py --league baseball
    python3 src/weather_watch.py --league K리그1 --loop 21600
    python3 src/weather_watch.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from free_context import append_jsonl, utc_now  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VENUES = ROOT / "data" / "static" / "venues.csv"
OUT = ROOT / "data" / "raw" / "weather" / "forecast_snapshots.jsonl"
ENDPOINT = "https://api.open-meteo.com/v1/forecast"
HOURLY = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation_probability",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
]


def load_venues(league: str | None = None, team: str | None = None) -> list[dict]:
    with VENUES.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if league:
        rows = [r for r in rows if r["league"] == league]
    if team:
        rows = [r for r in rows if r["team"] == team]
    for r in rows:
        r["latitude"] = float(r["latitude"])
        r["longitude"] = float(r["longitude"])
    return rows


def fetch_forecast(venue: dict, session: requests.Session | None = None) -> dict:
    sess = session or requests.Session()
    observed = utc_now()
    response = sess.get(
        ENDPOINT,
        params={
            "latitude": venue["latitude"],
            "longitude": venue["longitude"],
            "hourly": ",".join(HOURLY),
            "forecast_days": 4,
            "timezone": "auto",
            "wind_speed_unit": "ms",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    hourly = payload.get("hourly") or {}
    valid = hourly.get("time") or []
    if not valid:
        raise ValueError(f"{venue['venue_id']}: 시간별 예보가 비었다")
    for field in HOURLY:
        if len(hourly.get(field) or []) != len(valid):
            raise ValueError(f"{venue['venue_id']}: {field} 길이가 time과 다르다")
    fetched = utc_now()
    return {
        "source": "open-meteo",
        "licence": "CC BY 4.0; free endpoint non-commercial",
        "model": "best_match",
        "league": venue["league"],
        "team": venue["team"],
        "venue_id": venue["venue_id"],
        "venue_name": venue["venue_name"],
        "latitude": venue["latitude"],
        "longitude": venue["longitude"],
        "roof": venue["roof"],
        "coordinate_quality": venue["coordinate_quality"],
        "observed_at": observed.isoformat(timespec="seconds"),
        "fetched_at": fetched.isoformat(timespec="seconds"),
        "timezone": payload.get("timezone") or "Asia/Seoul",
        "valid_from": valid[0],
        "valid_to": valid[-1],
        "hourly_units": payload.get("hourly_units") or {},
        "hourly": {"valid_at": valid, **{field: hourly[field] for field in HOURLY}},
    }


def poll(league: str, team: str | None = None, *, session=None, output: Path = OUT) -> int:
    venues = load_venues(league, team)
    if not venues:
        raise ValueError(f"경기장 없음: league={league!r}, team={team!r}")
    rows = []
    for venue in venues:
        try:
            rows.append(fetch_forecast(venue, session=session))
            print(f"  {venue['team']:<8} {venue['venue_name']} 예보 확보", flush=True)
        except Exception as exc:  # noqa: BLE001 - 한 경기장 실패가 전체 수집을 죽이면 안 된다
            print(f"  [{venue['team']}] {type(exc).__name__}: {exc}", flush=True)
        time.sleep(0.15)
    n = append_jsonl(output, rows)
    stamp = utc_now().astimezone(timezone.utc).isoformat(timespec="seconds")
    print(f"[{stamp}] 무료 날씨 {n}/{len(venues)}개 경기장 기록", flush=True)
    return n


def leagues_from_arg(value: str) -> list[str]:
    if value == "baseball":
        return ["KBO", "NPB", "MLB"]
    if value == "all":
        return ["K리그1", "KBO", "NPB", "MLB"]
    return [x.strip() for x in value.split(",") if x.strip()]


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        times = ["2026-08-19T12:00", "2026-08-19T13:00"]
        return {
            "timezone": "Asia/Seoul",
            "hourly_units": {x: "unit" for x in HOURLY},
            "hourly": {"time": times, **{x: [1.0, 2.0] for x in HOURLY}},
        }


class _FakeSession:
    def get(self, *args, **kwargs):
        return _FakeResponse()


def _selftest() -> int:
    venues = load_venues("K리그1")
    assert len(venues) == 12, f"K리그1 기본 경기장 12개가 아님: {len(venues)}"
    assert len({v["team"] for v in venues}) == 12
    assert all(33 <= v["latitude"] <= 39 and 124 <= v["longitude"] <= 132 for v in venues)
    snap = fetch_forecast(venues[0], session=_FakeSession())
    assert snap["observed_at"] <= snap["fetched_at"]
    assert len(snap["hourly"]["valid_at"]) == 2
    assert all(len(snap["hourly"][x]) == 2 for x in HOURLY)
    expected = {"KBO": 10, "NPB": 12, "MLB": 30}
    assert {lg: len(load_venues(lg)) for lg in expected} == expected
    assert leagues_from_arg("baseball") == ["KBO", "NPB", "MLB"]
    print("✅ 무료 날씨 자기검사 통과 (K리그·KBO·NPB·MLB 경기장 · 시점/필드 정합성)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="K리그1")
    parser.add_argument("--team")
    parser.add_argument("--loop", type=int, default=0)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    leagues = leagues_from_arg(args.league)
    while True:
        for league in leagues:
            poll(league, args.team)
        if not args.loop:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
