"""무료 경기정보의 공통 계산과 시점 보존 규칙.

이 모듈은 특정 수집처에 묶이지 않는다. 날씨·라인업·일정처럼 서로 다른 원본을
동일한 규칙으로 저장하고, 프로토 확률과 독립 경기모델을 분리해 해석하는 데 쓰는
작은 정본이다.

핵심 규율
---------
* ``valid_at``: 값이 설명하는 실제 시점
* ``observed_at``: 우리 수집기가 그 값을 처음 알게 된 시점
* ``fetched_at``: HTTP 요청이 끝난 시점

백테스트에서는 ``observed_at <= prediction_cutoff`` 인 행만 사용할 수 있다.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | str) -> datetime:
    """문자열/시각을 timezone-aware UTC로 통일한다."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("timezone 없는 시각은 허용하지 않는다")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Observation:
    source: str
    entity_id: str
    field: str
    value: object
    valid_at: datetime
    observed_at: datetime
    fetched_at: datetime
    unit: str | None = None
    confidence: str = "observed"
    licence: str | None = None

    def __post_init__(self) -> None:
        valid = as_utc(self.valid_at)
        observed = as_utc(self.observed_at)
        fetched = as_utc(self.fetched_at)
        if fetched < observed:
            raise ValueError("fetched_at은 observed_at보다 이를 수 없다")
        object.__setattr__(self, "valid_at", valid)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "fetched_at", fetched)

    def to_dict(self) -> dict:
        out = asdict(self)
        for key in ("valid_at", "observed_at", "fetched_at"):
            out[key] = out[key].isoformat(timespec="seconds")
        return out


def append_jsonl(path: Path, rows: Iterable[dict], *, stream: str | None = None) -> int:
    """append-only 원장을 DB에 먼저 기록하고 JSONL을 호환 export로 만든다."""
    rows = list(rows)
    if not rows:
        return 0
    from runtime_db import RuntimeDatabase, database_enabled
    if database_enabled():
        stream = stream or "raw:" + "/".join(path.parts[-3:])
        db = RuntimeDatabase()
        inserted = db.append_events(stream, rows)
        # 파일은 더 이상 정본이 아니다. DB 전체를 원자적으로 export하므로 checkout
        # 동기화가 지워도 다음 수집 때 복원된다.
        db.export_events(stream, path)
        return inserted
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            n += 1
    return n


def _probabilities(values: Sequence[float]) -> list[float]:
    p = [float(x) for x in values]
    if len(p) < 2 or any((not math.isfinite(x)) or x <= 0 for x in p):
        raise ValueError("확률은 2개 이상의 양수여야 한다")
    total = sum(p)
    if total <= 0:
        raise ValueError("확률 합이 0이다")
    return [x / total for x in p]


def centered_log_ratio(probabilities: Sequence[float], index: int) -> float:
    """다지선다 확률에서 한 선택지의 상대적 강도를 잰다."""
    p = _probabilities(probabilities)
    if not 0 <= index < len(p):
        raise IndexError(index)
    mean_log = sum(math.log(x) for x in p) / len(p)
    return math.log(p[index]) - mean_log


def empirical_percentile(history: Sequence[float], value: float) -> float | None:
    """동률을 절반으로 처리한 경험적 퍼센타일."""
    clean = [float(x) for x in history if math.isfinite(float(x))]
    if not clean:
        return None
    below = sum(x < value for x in clean)
    ties = sum(x == value for x in clean)
    return (below + 0.5 * ties) / len(clean)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 위경도 사이 대권거리(km). 이동 부담의 무료 대리변수다."""
    r = 6371.0088
    a1, a2 = math.radians(lat1), math.radians(lat2)
    da = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(da / 2) ** 2 + math.cos(a1) * math.cos(a2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def exp_workload(
    events: Iterable[tuple[datetime, float]],
    as_of: datetime,
    tau_days: float = 7.0,
) -> float:
    """과거 출전시간의 지수감쇠 합. as_of 이후 값은 무조건 제외한다."""
    cutoff = as_utc(as_of)
    if tau_days <= 0:
        raise ValueError("tau_days는 양수여야 한다")
    total = 0.0
    for at, minutes in events:
        at = as_utc(at)
        if at >= cutoff:
            continue
        days = (cutoff - at).total_seconds() / 86400.0
        total += max(0.0, float(minutes)) * math.exp(-days / tau_days)
    return total


@dataclass(frozen=True)
class CrowdingAssessment:
    favorite_index: int
    proto_probability: float
    fundamental_probability: float
    overseas_probability: float | None
    market_percentile: float | None
    local_skew_clr: float | None
    unexplained_clr: float
    unexplained_z: float
    label: str

    def to_dict(self) -> dict:
        return asdict(self)


def assess_crowding(
    proto: Sequence[float],
    fundamental: Sequence[float],
    overseas: Sequence[float] | None = None,
    *,
    market_percentile: float | None = None,
    uncertainty_clr: float = 0.15,
    incremental_gain: float | None = None,
) -> CrowdingAssessment:
    """정배 지지와 설명되지 않는 쏠림을 분리한다.

    ``incremental_gain``은 시장 기준모델 대비 검증 손실 개선이다. 0 이하면 같은
    방향의 경기정보가 있더라도 ``이미 가격 반영``으로 분류한다.
    """
    pp = _probabilities(proto)
    pf = _probabilities(fundamental)
    if len(pp) != len(pf):
        raise ValueError("프로토와 독립모델 선택지 수가 다르다")
    po = _probabilities(overseas) if overseas is not None else None
    if po is not None and len(po) != len(pp):
        raise ValueError("해외시장 선택지 수가 다르다")
    if uncertainty_clr <= 0:
        raise ValueError("uncertainty_clr는 양수여야 한다")

    favorite = max(range(len(pp)), key=pp.__getitem__)
    proto_clr = centered_log_ratio(pp, favorite)
    fundamental_clr = centered_log_ratio(pf, favorite)
    unexplained = proto_clr - fundamental_clr
    z = unexplained / uncertainty_clr
    local = proto_clr - centered_log_ratio(po, favorite) if po is not None else None
    extreme = market_percentile is not None and market_percentile >= 0.90

    if z >= 1.64 and extreme:
        label = "설명되지 않는 쏠림"
    elif z <= -1.64:
        label = "시장 과소반영"
    elif incremental_gain is not None and incremental_gain <= 0 and pf[favorite] >= 1 / len(pf):
        label = "이미 가격 반영"
    elif extreme:
        label = "정당한 초강세"
    else:
        label = "시장과 경기정보 일치"

    return CrowdingAssessment(
        favorite_index=favorite,
        proto_probability=pp[favorite],
        fundamental_probability=pf[favorite],
        overseas_probability=(po[favorite] if po is not None else None),
        market_percentile=market_percentile,
        local_skew_clr=local,
        unexplained_clr=unexplained,
        unexplained_z=z,
        label=label,
    )
