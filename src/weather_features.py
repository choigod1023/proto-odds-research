"""날씨 스냅샷을 경기 예측시점 기준으로 결합한다.

가장 최신 예보를 고르는 것이 아니라 ``observed_at <= cutoff``를 만족하는 것 중
최신만 고른다. 그래서 경기 전날 백테스트가 경기 직전 갱신 예보를 훔쳐보지 않는다.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from runtime_db import RuntimeDatabase, database_enabled

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "data" / "raw" / "weather" / "forecast_snapshots.jsonl"

FIELDS = [
    "temperature_2m", "relative_humidity_2m", "precipitation_probability",
    "precipitation", "weather_code", "wind_speed_10m",
    "wind_direction_10m", "wind_gusts_10m",
]


def _aware(value: datetime | str, zone: str | None = None) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        if not zone:
            raise ValueError("timezone 없는 시각")
        value = value.replace(tzinfo=ZoneInfo(zone))
    return value.astimezone(timezone.utc)


def load_snapshots(path: Path = SNAPSHOTS) -> list[dict]:
    if database_enabled():
        return RuntimeDatabase().events("weather_forecasts")
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _hour(snapshot: dict, target: datetime, tolerance_minutes: int) -> dict | None:
    zone = snapshot.get("timezone") or "Asia/Seoul"
    times = [_aware(x, zone) for x in snapshot["hourly"]["valid_at"]]
    if not times:
        return None
    idx = min(range(len(times)), key=lambda i: abs((times[i] - target).total_seconds()))
    delta = abs((times[idx] - target).total_seconds()) / 60
    if delta > tolerance_minutes:
        return None
    return {field: snapshot["hourly"][field][idx] for field in FIELDS} | {
        "forecast_valid_at": times[idx].isoformat(timespec="seconds")
    }


def select_asof_forecast(
    snapshots: list[dict],
    *,
    venue_id: str,
    kickoff: datetime | str,
    cutoff: datetime | str,
    tolerance_minutes: int = 90,
) -> dict | None:
    """cutoff 당시 알 수 있던 가장 최근 킥오프 예보와 직전 예보 수정폭."""
    kickoff_utc, cutoff_utc = _aware(kickoff), _aware(cutoff)
    if cutoff_utc > kickoff_utc:
        raise ValueError("예측 cutoff가 킥오프보다 늦다")
    eligible = [
        s for s in snapshots
        if s.get("venue_id") == venue_id and _aware(s["observed_at"]) <= cutoff_utc
    ]
    eligible.sort(key=lambda s: _aware(s["observed_at"]), reverse=True)
    chosen = None
    for s in eligible:
        values = _hour(s, kickoff_utc, tolerance_minutes)
        if values is not None:
            chosen = (s, values)
            break
    if chosen is None:
        return None

    snapshot, values = chosen
    observed = _aware(snapshot["observed_at"])
    out = {
        "weather_source": snapshot["source"],
        "weather_observed_at": observed.isoformat(timespec="seconds"),
        "weather_lead_hours": (kickoff_utc - observed).total_seconds() / 3600,
        "weather_asof_ok": observed <= cutoff_utc,
        "weather_roof": snapshot.get("roof"),
        **values,
    }

    # 같은 cutoff 안의 바로 전 스냅샷과 비교해 예보 불안정성을 수치로 남긴다.
    previous = None
    passed_chosen = False
    for s in eligible:
        if s is snapshot:
            passed_chosen = True
            continue
        if not passed_chosen:
            continue
        prev_values = _hour(s, kickoff_utc, tolerance_minutes)
        if prev_values is not None:
            previous = prev_values
            break
    for field in ("temperature_2m", "precipitation_probability", "wind_speed_10m"):
        before = previous.get(field) if previous else None
        now = values.get(field)
        out[f"{field}_revision"] = (
            float(now) - float(before) if now is not None and before is not None else None
        )
    return out


def _snapshot(observed: datetime, temp: float) -> dict:
    valid = datetime(2026, 8, 20, 10, tzinfo=ZoneInfo("Asia/Seoul"))
    return {
        "source": "test", "venue_id": "stadium", "roof": "open",
        "timezone": "Asia/Seoul", "observed_at": observed.isoformat(),
        "hourly": {
            "valid_at": [valid.replace(tzinfo=None).isoformat(timespec="minutes")],
            **{field: [temp if field == "temperature_2m" else 1.0] for field in FIELDS},
        },
    }


def _selftest() -> int:
    kickoff = datetime(2026, 8, 20, 1, tzinfo=timezone.utc)
    early = _snapshot(kickoff - timedelta(hours=24), 24)
    allowed = _snapshot(kickoff - timedelta(hours=7), 26)
    future = _snapshot(kickoff - timedelta(hours=1), 31)
    result = select_asof_forecast(
        [early, allowed, future], venue_id="stadium", kickoff=kickoff,
        cutoff=kickoff - timedelta(hours=6),
    )
    assert result is not None
    assert result["temperature_2m"] == 26
    assert result["temperature_2m_revision"] == 2
    assert result["weather_asof_ok"] is True
    try:
        select_asof_forecast([], venue_id="stadium", kickoff=kickoff,
                             cutoff=kickoff + timedelta(minutes=1))
    except ValueError:
        pass
    else:
        raise AssertionError("킥오프 이후 cutoff를 허용했다")
    print("✅ 날씨 as-of 자기검사 통과 (미래 예보 차단·킥오프 정렬·수정폭)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    print(f"날씨 스냅샷 {len(load_snapshots()):,}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
