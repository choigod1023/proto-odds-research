"""연도가 바뀌어도 최신 경기 상세 캐시를 자동으로 찾는다."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DETAIL_ROOT = ROOT / "data" / "raw" / "detail"
KST = ZoneInfo("Asia/Seoul")


def latest_detail_path(league: str, kind: str, start_year: int = 2023,
                       root: Path = DETAIL_ROOT, *,
                       now: datetime | None = None) -> Path:
    """가장 큰 종료연도의 캐시. 아직 없으면 KST 현재연도 대상 경로."""
    pattern = re.compile(
        rf"^{re.escape(league)}_{re.escape(kind)}_{int(start_year)}_(\d{{4}})\.json$")
    candidates = []
    for path in root.glob(f"{league}_{kind}_{int(start_year)}_*.json"):
        match = pattern.match(path.name)
        if match:
            candidates.append((int(match.group(1)), path))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    observed = datetime.now(KST) if now is None else now
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=KST)
    else:
        observed = observed.astimezone(KST)
    current = observed.year
    return root / f"{league}_{kind}_{int(start_year)}_{current}.json"
