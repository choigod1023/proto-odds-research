"""경기 전 무료 컨텍스트를 홈페이지용 근거로 바꾼다.

이 모듈은 확률을 조정하지 않는다. 선발·라인업·최근 경기·날씨·공개 픽스터는
아직 전향 검증을 통과하지 않았으므로 ``beta``가 0인 상태다. 대신 사용자가
추천을 검토할 수 있도록, 경기 전에 실제로 관측된 사실만 구조화해 보여 준다.

가장 중요한 제약은 두 가지다.

* 경기 날짜와 양 팀이 모두 일치해야 한다. 같은 대진의 다음 날 경기를 섞지 않는다.
* 관측 시각이 경기 시작보다 앞서야 한다. 경기 후 정보는 설명에도 쓰지 않는다.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CONTEXT = ROOT / "data" / "raw" / "baseball_context" / "events.jsonl"
FEATURES = ROOT / "data" / "processed" / "live_baseball_features.json"
TEAM_MAP = ROOT / "data" / "processed" / "team_map.json"
KST = ZoneInfo("Asia/Seoul")
DATE_TIME = re.compile(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})")


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _jsonl(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            if line.strip():
                out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _dt(text: object, *, naive_tz=KST) -> datetime | None:
    if not text:
        return None
    try:
        value = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=naive_tz)
        return value.astimezone(timezone.utc)
    except ValueError:
        return None


def _kickoff(text: object, year: int) -> datetime | None:
    m = DATE_TIME.search(str(text or ""))
    if not m:
        return None
    month, day, hour, minute = map(int, m.groups())
    try:
        return datetime(year, month, day, hour, minute, tzinfo=KST).astimezone(timezone.utc)
    except ValueError:
        return None


def _source_date(text: object) -> date | None:
    digits = re.sub(r"\D", "", str(text or ""))[:8]
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return None


def _finite(value) -> float | None:
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None


def _fmt(value, digits=1) -> str:
    n = _finite(value)
    return "–" if n is None else f"{n:.{digits}f}"


def _entry(kind: str, text: str, *, source: str, observed_at: str | None = None,
           status: str = "observed") -> dict:
    return {"kind": kind, "text": text, "source": source,
            "observed_at": observed_at, "status": status}


class ContextStore:
    """원시 이벤트를 한 번만 읽고 여러 경기와 안전하게 결합한다."""

    def __init__(self, year: int | None = None,
                 context_path: Path = CONTEXT, feature_path: Path = FEATURES,
                 team_map_path: Path = TEAM_MAP):
        self.year = year or datetime.now(KST).year
        self.team_map = _json(team_map_path, {})
        self.rows = _jsonl(context_path)
        features = _json(feature_path, {})
        self.features = {r.get("game_id"): r for r in features.get("games", [])
                         if r.get("game_id")}
        self.coefficient_status = features.get("coefficient_status") or "not_fitted"
        self.coefficient_gate = features.get("coefficient_gate") or []

    def _mapped(self, league: str, team: str) -> str:
        return str((self.team_map.get(league) or {}).get(team, team)).strip()

    def _find(self, league: str, home: str, away: str,
              kickoff: datetime) -> dict | None:
        expected_home = self._mapped(league, home)
        expected_away = self._mapped(league, away)
        candidates = []
        for row in self.rows:
            if row.get("league") != league:
                continue
            if str(row.get("home") or "").strip() != expected_home:
                continue
            if str(row.get("away") or "").strip() != expected_away:
                continue
            game_time = _dt(row.get("game_datetime"))
            observed = _dt(row.get("observed_at"), naive_tz=timezone.utc)
            if not game_time or game_time.astimezone(KST).date() != kickoff.astimezone(KST).date():
                continue
            # 경기 후에 기록된 이벤트는 해설에 들어갈 수 없다.
            if not observed or observed >= kickoff:
                continue
            candidates.append((observed, row))
        return max(candidates, key=lambda item: item[0])[1] if candidates else None

    def evidence_for(self, game: dict) -> dict | None:
        if game.get("sport") != "bs":
            return None
        kickoff = _kickoff(game.get("date"), self.year)
        if not kickoff:
            return None
        row = self._find(game.get("league"), game.get("home"), game.get("away"), kickoff)
        if not row:
            return None

        observed_at = row.get("observed_at")
        source = "네이버 스포츠 공개 경기정보"
        internal: list[dict] = []
        external: list[dict] = []
        crowd: list[dict] = []
        limitations: list[str] = []
        home_name, away_name = game["home"], game["away"]
        home = row.get("home_features") or {}
        away = row.get("away_features") or {}
        hs, aws = home.get("starter") or {}, away.get("starter") or {}

        if hs.get("name") or aws.get("name"):
            internal.append(_entry(
                "starter",
                f"선발 예고는 {home_name} {hs.get('name') or '미확인'}, "
                f"{away_name} {aws.get('name') or '미확인'}다.",
                source=source, observed_at=observed_at,
            ))

        # 상세 프리뷰가 오래된 캐시일 수 있다. 경기 14일 이내 기준시점일 때만
        # 시즌/최근 통계를 노출한다. 선발 이름은 별도 일정 응답이라 그대로 쓸 수 있다.
        generated_date = _source_date(row.get("generated_date"))
        game_date = kickoff.astimezone(KST).date()
        detail_fresh = bool(generated_date and timedelta(0) <= game_date - generated_date <= timedelta(days=14))
        if row.get("preview_available") and not detail_fresh:
            limitations.append("선수 상세 통계의 기준일이 경기와 14일 넘게 떨어져 있어 수치는 제외했다.")

        if detail_fresh:
            hseason, aseason = hs.get("season") or {}, aws.get("season") or {}
            hip, aip = _finite(hseason.get("innings")), _finite(aseason.get("innings"))
            if hip is not None and aip is not None and hip >= 20 and aip >= 20:
                internal.append(_entry(
                    "starter_stats",
                    f"시즌 선발 지표는 {home_name} 선발 ERA {_fmt(hseason.get('era'), 2)}·"
                    f"WHIP {_fmt(hseason.get('whip'), 2)}·K-BB/9 {_fmt(hseason.get('k_minus_bb_per_9'), 1)}, "
                    f"{away_name} 선발 ERA {_fmt(aseason.get('era'), 2)}·"
                    f"WHIP {_fmt(aseason.get('whip'), 2)}·K-BB/9 {_fmt(aseason.get('k_minus_bb_per_9'), 1)}다.",
                    source=source, observed_at=observed_at,
                ))
            elif hs.get("name") or aws.get("name"):
                limitations.append("선발 시즌 표본이 20이닝 미만이거나 누락되어 선발 수치 비교는 제외했다.")

            hr, ar = home.get("recent_games") or {}, away.get("recent_games") or {}
            h_latest, a_latest = _source_date(hr.get("latest_game_date")), _source_date(ar.get("latest_game_date"))
            recent_fresh = all(d and timedelta(0) <= game_date - d <= timedelta(days=14)
                               for d in (h_latest, a_latest))
            if recent_fresh and hr.get("games") and ar.get("games"):
                internal.append(_entry(
                    "recent_games",
                    f"최근 집계에서 {home_name}는 {int(hr['games'])}경기 "
                    f"{int(hr.get('wins') or 0)}승·득실 {int(hr.get('runs_for') or 0)}:{int(hr.get('runs_against') or 0)}, "
                    f"{away_name}는 {int(ar['games'])}경기 {int(ar.get('wins') or 0)}승·"
                    f"득실 {int(ar.get('runs_for') or 0)}:{int(ar.get('runs_against') or 0)}이다.",
                    source=source, observed_at=observed_at,
                ))

        hlu, alu = home.get("lineup") or {}, away.get("lineup") or {}
        if hlu.get("confirmed") and alu.get("confirmed"):
            internal.append(_entry(
                "lineup",
                f"양 팀 선발 타순이 확인됐다({home_name} {int(hlu.get('batter_count') or 0)}명, "
                f"{away_name} {int(alu.get('batter_count') or 0)}명).",
                source=source, observed_at=observed_at,
            ))
        else:
            limitations.append("선발 타순은 아직 양 팀 모두 확정된 상태가 아니다.")

        feature = self.features.get(row.get("game_id")) or {}
        weather = feature.get("weather") or {}
        weather_observed = _dt(weather.get("weather_observed_at"), naive_tz=timezone.utc)
        if weather and weather_observed and weather_observed < kickoff:
            roof = weather.get("weather_roof")
            weather_at = weather.get("weather_observed_at")
            if roof == "dome":
                external.append(_entry(
                    "weather", "돔 경기장이라 외부 날씨 효과는 분석에서 제외한다.",
                    source="Open-Meteo + 경기장 지붕 분류", observed_at=weather_at,
                ))
            else:
                bits = []
                temp = _finite(weather.get("temperature_2m"))
                rain = _finite(weather.get("precipitation_probability"))
                wind = _finite(weather.get("wind_speed_10m"))
                if temp is not None:
                    bits.append(f"기온 {temp:.1f}°C")
                if rain is not None:
                    bits.append(f"강수확률 {rain:.0f}%")
                if wind is not None:
                    bits.append(f"바람 {wind:.1f}km/h")
                if bits:
                    prefix = "경기 시각 예보는 "
                    if roof == "retractable" and not weather.get("roof_state_known"):
                        prefix = "개폐식 지붕의 개방 여부는 미확인이며, 외부 기준 경기 시각 예보는 "
                    external.append(_entry(
                        "weather", prefix + "·".join(bits) + "다.",
                        source="Open-Meteo", observed_at=weather_at,
                    ))

        picksters = feature.get("pickster_crowd") or {}
        crowd_observed = _dt(picksters.get("observed_at"), naive_tz=timezone.utc)
        n = int(picksters.get("independent_capper_count") or 0)
        if n and crowd_observed and crowd_observed < kickoff:
            h = int(picksters.get("home_capper_count") or 0)
            a = int(picksters.get("away_capper_count") or 0)
            crowd.append(_entry(
                "public_picks",
                f"공개 픽스터 머니라인 {n}건은 홈 {h}건·원정 {a}건이다. "
                "표본 중복과 선택편향을 제거하지 못했으므로 인기 신호로만 본다.",
                source="TailSlips 공개 HTML", observed_at=picksters.get("observed_at"),
                status="unvalidated",
            ))

        limitations.append(
            "선수·최근 경기·날씨·공개 픽은 전향 검증 전이라 현재 추천 확률과 선택 점수에는 가산하지 않았다."
        )
        return {
            "source_game_id": row.get("game_id"),
            "observed_at": observed_at,
            "asof_ok": True,
            "coefficient_status": self.coefficient_status,
            "coefficient_gate": self.coefficient_gate,
            "protected_entities": list(dict.fromkeys(
                x for x in (home_name, away_name, hs.get("name"), aws.get("name")) if x
            )),
            "internal": internal,
            "external": external,
            "crowd": crowd,
            "limitations": list(dict.fromkeys(limitations)),
        }


def narrative(evidence: dict | None, limit: int = 650) -> str:
    """구조화 근거를 LLM에 넘길 사실확인 완료 문장으로 만든다."""
    if not evidence:
        return ""
    parts = []
    inside = [x.get("text", "") for x in evidence.get("internal", []) if x.get("text")]
    outside = [x.get("text", "") for x in evidence.get("external", []) if x.get("text")]
    crowd = [x.get("text", "") for x in evidence.get("crowd", []) if x.get("text")]
    if inside:
        parts.append("경기 내부 정보로는 " + " ".join(inside))
    if outside:
        parts.append("경기 외부 정보로는 " + " ".join(outside))
    if crowd:
        parts.append("시장 밖 공개 의견은 " + " ".join(crowd))
    # 이 문장은 LLM이 관측 사실을 추천의 원인으로 둔갑시키지 못하게 하는 안전장치다.
    parts.append("다만 이 추가 정보는 전향 검증 전이라 현재 픽의 확률이나 선택 점수에는 반영하지 않았다.")
    text = " ".join(parts).replace("..", ".")
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _selftest() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        context = root / "events.jsonl"
        features = root / "features.json"
        mapping = root / "map.json"
        rows = [
            {"game_id": "old", "league": "MLB", "game_datetime": "2026-08-20T18:00:00",
             "observed_at": "2026-08-20T08:00:00+00:00", "home": "피츠버그", "away": "보스턴"},
            {"game_id": "right", "league": "MLB", "game_datetime": "2026-08-21T18:00:00",
             "observed_at": "2026-08-21T06:00:00+00:00", "home": "피츠버그", "away": "보스턴",
             "home_features": {"starter": {"name": "홈투수"}, "lineup": {}},
             "away_features": {"starter": {"name": "원정투수"}, "lineup": {}}},
            # 경기 뒤 관측은 더 최신이어도 사용하면 안 된다.
            {"game_id": "leak", "league": "MLB", "game_datetime": "2026-08-21T18:00:00",
             "observed_at": "2026-08-21T10:00:00+00:00", "home": "피츠버그", "away": "보스턴"},
        ]
        context.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows), encoding="utf-8")
        features.write_text(json.dumps({"coefficient_status": "not_fitted", "games": [{
            "game_id": "right",
            "pickster_crowd": {"observed_at": "2026-08-21T10:00:00+00:00",
                                "independent_capper_count": 4,
                                "home_capper_count": 4, "away_capper_count": 0},
        }]}), encoding="utf-8")
        mapping.write_text(json.dumps({"MLB": {"피츠파이": "피츠버그", "보스레드": "보스턴"}}), encoding="utf-8")
        store = ContextStore(2026, context, features, mapping)
        found = store.evidence_for({"sport": "bs", "league": "MLB", "home": "피츠파이",
                                    "away": "보스레드", "date": "08.21(금) 18:00"})
        assert found and found["source_game_id"] == "right"
        assert not found["crowd"], "경기 뒤 픽스터 스냅샷이 사전 근거에 섞였다"
        assert "홈투수" in narrative(found)
        assert store.evidence_for({"sport": "bs", "league": "MLB", "home": "피츠파이",
                                   "away": "보스레드", "date": "08.20(목) 18:00"})["source_game_id"] == "old"
    print("✅ recommendation_context selftest 통과 (대진 날짜·경기 전 시점·팀명 매핑)")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
