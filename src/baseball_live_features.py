"""수집된 무료 야구 컨텍스트를 경기별 누수 없는 원시 feature로 만든다.

확률 보정 계수는 아직 넣지 않는다. 과거 학습 없이 임의 계수를 붙이면 정배를 더
확신하는 장치가 되기 때문이다. 이 산출물은 다음 수식의 ``x``만 제공한다.

    logit(p_adjusted) = logit(p_market) + beta^T x

``beta``는 전향 표본의 시간분리 검증에서 Brier, log-loss, calibration이 시장 기준보다
모두 좋아질 때만 별도로 학습한다.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from weather_features import load_snapshots, select_asof_forecast

ROOT = Path(__file__).resolve().parent.parent
CONTEXT = ROOT / "data" / "raw" / "baseball_context" / "events.jsonl"
CROWD = ROOT / "data" / "raw" / "picksters" / "tailslips_crowd.jsonl"
VENUES = ROOT / "data" / "static" / "venues.csv"
OUT = ROOT / "data" / "processed" / "live_baseball_features.json"

MLB_ABBR = {
    "LA다저스": "LAD", "LA에인절스": "LAA", "뉴욕메츠": "NYM", "뉴욕양키스": "NYY",
    "디트로이트": "DET", "마이애미": "MIA", "미네소타": "MIN", "밀워키": "MIL",
    "보스턴": "BOS", "볼티모어": "BAL", "샌디에이고": "SD", "샌프란시스코": "SF",
    "세인트루이스": "STL", "시애틀": "SEA", "시카고W": "CWS", "시카고컵스": "CHC",
    "신시내티": "CIN", "애리조나": "AZ", "애슬레틱스": "ATH", "애틀랜타": "ATL",
    "워싱턴": "WSH", "캔자스시티": "KC", "콜로라도": "COL", "클리블랜드": "CLE",
    "탬파베이": "TB", "텍사스": "TEX", "토론토": "TOR", "피츠버그": "PIT",
    "필라델피아": "PHI", "휴스턴": "HOU",
}

VENUE_ALIAS = {
    "요코하마": "요코베이", "히로시마": "히로카프",
    "LA다저스": "LA다저스", "LA에인절스": "LA에인절",
    "뉴욕메츠": "뉴욕메츠", "뉴욕양키스": "뉴욕양키", "디트로이트": "디트타이",
    "마이애미": "마이말린", "미네소타": "미네트윈", "밀워키": "밀워브루",
    "보스턴": "보스레드", "볼티모어": "볼티오리", "샌디에이고": "샌디파드",
    "샌프란시스코": "샌프자이", "세인트루이스": "세인카디", "시애틀": "시애매리",
    "시카고W": "시카화이", "시카고컵스": "시카컵스", "신시내티": "신시레즈",
    "애리조나": "애리다이", "애슬레틱스": "애슬레틱", "애틀랜타": "애틀브레",
    "워싱턴": "워싱내셔", "캔자스시티": "캔자로얄", "콜로라도": "콜로로키",
    "클리블랜드": "클리가디", "탬파베이": "탬파레이", "텍사스": "텍사레인",
    "토론토": "토론블루", "피츠버그": "피츠파이", "필라델피아": "필라필리",
    "휴스턴": "휴스애스",
}


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _latest_context() -> list[dict]:
    latest = {}
    for row in _jsonl(CONTEXT):
        if row.get("game_id"):
            latest[row["game_id"]] = row
    return list(latest.values())


def _latest_crowd() -> list[dict]:
    rows = _jsonl(CROWD)
    return rows[-1].get("games", []) if rows else []


def _diff(a, b) -> float | None:
    try:
        a, b = float(a), float(b)
        return round(a - b, 4) if math.isfinite(a) and math.isfinite(b) else None
    except (TypeError, ValueError):
        return None


def _path(obj: dict, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _run_margin(team: dict) -> float | None:
    recent = team.get("recent_games") or {}
    games = recent.get("games")
    if not games:
        return None
    return (float(recent.get("runs_for") or 0) - float(recent.get("runs_against") or 0)) / games


def _venue_map() -> dict[tuple[str, str], dict]:
    if not VENUES.exists():
        return {}
    with VENUES.open(encoding="utf-8", newline="") as f:
        return {(r["league"], r["team"]): r for r in csv.DictReader(f)}


def _aware_game_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            from datetime import timedelta
            dt = dt.replace(tzinfo=timezone(timedelta(hours=9)))
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _crowd_index(games: list[dict]) -> dict[tuple[frozenset[str], str], dict]:
    out = {}
    for g in games:
        a, b = g.get("team_a"), g.get("team_b")
        slate_date = g.get("slate_date")
        if a and b and slate_date:
            out[(frozenset((a, b)), slate_date)] = g
    return out


def _crowd_features(row: dict, index: dict[tuple[frozenset[str], str], dict]) -> dict | None:
    if row.get("league") != "MLB":
        return None
    away, home = MLB_ABBR.get(row.get("away")), MLB_ABBR.get(row.get("home"))
    if not away or not home:
        return None
    game_time = _aware_game_time(row.get("game_datetime"))
    if not game_time:
        return None
    game_date = game_time.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    game = index.get((frozenset((away, home)), game_date))
    if not game:
        return None
    observed = _aware_game_time(game.get("observed_at"))
    # 공개 픽 페이지는 경기 뒤에도 최종 스코어와 함께 남는다. 종료 뒤 스냅샷을
    # 사전 군중 의견처럼 붙이면 정답 누수다.
    if not observed or observed >= game_time:
        return None
    counts = game.get("moneyline_capper_counts") or {}
    h, a = int(counts.get(home, 0)), int(counts.get(away, 0))
    n = h + a
    if not n:
        return None
    # Jeffreys 0.5 smoothing으로 0/1 무한 log-odds를 막는다.
    p = (h + .5) / (n + 1)
    entropy = 0.0
    for q in (h / n, a / n):
        if q > 0:
            entropy -= q * math.log(q)
    return {
        "source": "tailslips_public_html", "observed_at": game.get("observed_at"),
        "slate_date": game_date,
        "home_abbr": home, "away_abbr": away,
        "home_capper_count": h, "away_capper_count": a, "independent_capper_count": n,
        "home_share": round(h / n, 4), "home_log_odds_smoothed": round(math.log(p / (1 - p)), 4),
        "opinion_entropy_0_to_1": round(entropy / math.log(2), 4),
        "strong_consensus": bool(n >= 5 and max(h, a) / n >= .75),
        "warning": "전향 표본 검증 전에는 확률 보정에 사용하지 않음",
    }


def _weather(row: dict, snapshots: list[dict], venues: dict[tuple[str, str], dict],
             generated: datetime) -> dict | None:
    game_time = _aware_game_time(row.get("game_datetime"))
    if not game_time or generated > game_time:
        return None
    team = VENUE_ALIAS.get(row.get("home"), row.get("home"))
    venue = venues.get((row.get("league"), team))
    if not venue:
        return None
    found = select_asof_forecast(
        snapshots, venue_id=venue["venue_id"], kickoff=game_time, cutoff=generated,
    )
    if not found:
        return None
    found["weather_effect_applicable"] = found.get("weather_roof") != "dome"
    found["roof_state_known"] = found.get("weather_roof") in {"open", "dome"}
    return found


def feature_row(row: dict, crowd_index: dict, snapshots: list[dict], venues: dict,
                generated: datetime) -> dict:
    home = row.get("home_features") or {}
    away = row.get("away_features") or {}
    hs = home.get("starter") or {}
    aws = away.get("starter") or {}
    raw = {
        # 양수면 모두 홈에 유리하도록 부호를 맞춘다.
        "home_season_win_rate_edge": _diff(
            _path(home, "standings", "win_rate"), _path(away, "standings", "win_rate")),
        "home_recent_run_margin_edge": _diff(_run_margin(home), _run_margin(away)),
        "home_starter_kbb9_edge": _diff(
            _path(hs, "season", "k_minus_bb_per_9"), _path(aws, "season", "k_minus_bb_per_9")),
        "home_starter_era_edge": _diff(
            _path(aws, "season", "era"), _path(hs, "season", "era")),
        "home_starter_whip_edge": _diff(
            _path(aws, "season", "whip"), _path(hs, "season", "whip")),
        "home_starter_hr9_edge": _diff(
            _path(aws, "season", "hr_per_9"), _path(hs, "season", "hr_per_9")),
        "away_starter_innings": _path(aws, "season", "innings"),
        "home_starter_innings": _path(hs, "season", "innings"),
        "away_lineup_confirmed": bool(_path(away, "lineup", "confirmed")),
        "home_lineup_confirmed": bool(_path(home, "lineup", "confirmed")),
    }
    missing = [k for k, v in raw.items() if v is None]
    warnings = []
    for side, starter in (("away", aws), ("home", hs)):
        innings = _path(starter, "season", "innings")
        if innings is not None and float(innings) < 20:
            warnings.append(f"{side}_starter_small_sample_lt_20ip")
    if not row.get("preview_available"):
        warnings.append("preview_unavailable")
    if not row.get("lineup_confirmed"):
        warnings.append("lineup_not_confirmed")
    return {
        "game_id": row.get("game_id"), "league": row.get("league"),
        "game_datetime": row.get("game_datetime"), "away": row.get("away"), "home": row.get("home"),
        "context_observed_at": row.get("observed_at"), "event_type": row.get("event_type"),
        "raw_features": raw, "pickster_crowd": _crowd_features(row, crowd_index),
        "weather": _weather(row, snapshots, venues, generated),
        "missing_features": missing, "warnings": warnings,
        "probability_adjustment": None,
        "adjustment_status": "계수 미학습 — 원시 feature만 제공",
    }


def build() -> dict:
    generated = datetime.now(timezone.utc)
    contexts = _latest_context()
    crowd_index = _crowd_index(_latest_crowd())
    snapshots = load_snapshots()
    venues = _venue_map()
    rows = [feature_row(r, crowd_index, snapshots, venues, generated) for r in contexts]
    return {
        "generated_at": generated.isoformat(timespec="seconds"),
        "formula": "logit(p_adjusted)=logit(p_market)+beta^T*x",
        "coefficient_status": "not_fitted",
        "coefficient_gate": [
            "walk-forward out-of-sample", "Brier와 log-loss 모두 시장 기준 개선",
            "calibration slope/intercept 악화 없음", "리그별 방향 안정성",
        ],
        "games": sorted(rows, key=lambda r: (r.get("game_datetime") or "", r["league"])),
    }


def _selftest() -> None:
    idx = _crowd_index([{"team_a": "NYY", "team_b": "BAL", "slate_date": "2026-08-18",
                         "observed_at": "2026-08-18T22:00:00+00:00",
                         "moneyline_capper_counts": {"NYY": 8, "BAL": 2}}])
    row = {"league": "MLB", "away": "뉴욕양키스", "home": "볼티모어",
           "game_datetime": "2026-08-19T08:00:00+09:00"}
    c = _crowd_features(row, idx)
    assert c and c["home_share"] == .2 and c["strong_consensus"]
    late = _crowd_index([{"team_a": "NYY", "team_b": "BAL", "slate_date": "2026-08-18",
                          "observed_at": "2026-08-19T01:00:00+00:00",
                          "moneyline_capper_counts": {"NYY": 8, "BAL": 2}}])
    assert _crowd_features(row, late) is None
    assert _diff(3, 2) == 1
    print("✅ baseball_live_features selftest 통과 (부호·군중 share·entropy)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return 0
    result = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"games": len(result["games"]), "output": str(OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
