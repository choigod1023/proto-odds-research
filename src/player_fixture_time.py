"""Time contract for legacy Naver KBO announcement fixtures only."""
import re
from datetime import datetime, timedelta, timezone

NAVER_SOURCE = "네이버 스포츠"
LOCAL_SECONDS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\Z")


def fixture_time(value, league, source):
    """Do not infer a timezone for arbitrary sources or observation timestamps."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        if league != "KBO" or source != NAVER_SOURCE or not LOCAL_SECONDS.fullmatch(str(value)):
            return None
        dt = dt.replace(tzinfo=timezone(timedelta(hours=9)))
    return dt
