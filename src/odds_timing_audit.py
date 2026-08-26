"""KBO 정산 archive 배당의 관측 시점 오염 감사.

``games.csv``의 배당에는 수집 시각이 없다. 따라서 과거 정산 배당이 실제
구매 시점에 살 수 있던 가격이었다고 간주하면 안 된다. 이 스크립트는 2026
KBO 승패 2-way 정산행을 배당 스냅샷과 ``(year, round, game_no)``로 대조해
그 한계를 수치화한다.

실행 가능 가격 후보는 ``ts < kickoff``이면서 snapshot의 ``result`` 상태가
명시적으로 ``경기전``인 행으로만 제한한다. 경기 결과 라벨은 정산
archive에서만 가져오며 snapshot의 ``result``는 시장 상태 필터로만 쓴다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "data" / "processed" / "games.csv"
SNAPSHOT_DIR = ROOT / "data" / "raw" / "snapshots"
OUT = ROOT / "findings" / "odds_timing_audit.json"

KEY = ["year", "round", "game_no"]
ARCHIVE_COLS = [
    "year", "round", "game_no", "date_text", "sport", "league",
    "market_family", "n_way", "home", "away", "odds", "result", "is_void",
]
SNAPSHOT_COLS = [
    "ts", "year", "round", "game_no", "sport", "league",
    "market_family", "n_way", "odds", "result",
]
DATE_TIME = re.compile(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})")
SETTLED_RESULTS = {"홈승", "홈패"}
EXPLICIT_PREGAME_RESULT = "경기전"
PRIMARY_TARGET_MINUTES = 30
PRIMARY_STALE_MINUTES = 35


def parse_odds(value: object) -> tuple[float, float] | None:
    try:
        odds = tuple(float(part) for part in str(value).split(","))
    except (TypeError, ValueError):
        return None
    if len(odds) != 2 or not np.isfinite(odds).all() or any(x <= 1.0 for x in odds):
        return None
    return odds


def _numeric_keys(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in KEY + ["n_way"]:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.dropna(subset=KEY + ["n_way"])
    out[KEY + ["n_way"]] = out[KEY + ["n_way"]].astype(int)
    return out


def prepare_archive(raw: pd.DataFrame) -> pd.DataFrame:
    """정산 archive에서 범위·킥오프·가격·라벨을 확정한다."""
    missing = set(ARCHIVE_COLS) - set(raw.columns)
    if missing:
        raise ValueError(f"archive columns missing: {sorted(missing)}")
    data = _numeric_keys(raw[ARCHIVE_COLS])
    void = data["is_void"].astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes"}
    )
    data = data[
        (data["year"] == 2026)
        & (data["sport"] == "bs")
        & (data["league"] == "KBO")
        & (data["market_family"] == "승패")
        & (data["n_way"] == 2)
        & (~void)
        & (data["result"].isin(SETTLED_RESULTS))
    ].copy()

    parts = data["date_text"].astype(str).str.extract(DATE_TIME)
    parts = parts.apply(pd.to_numeric, errors="coerce")
    naive = pd.to_datetime(
        {
            "year": data["year"], "month": parts[0], "day": parts[1],
            "hour": parts[2], "minute": parts[3],
        },
        errors="coerce",
    )
    # 원문 시각은 한국 시각이다. 모든 비교는 UTC로 통일한다.
    data["kickoff_utc"] = naive.dt.tz_localize(
        "Asia/Seoul", ambiguous="NaT", nonexistent="NaT"
    ).dt.tz_convert("UTC")
    data["archive_odds"] = data["odds"].map(parse_odds)
    data = data.dropna(subset=["kickoff_utc", "archive_odds"])

    # 같은 key가 둘 이상이면 어떤 정산행이 원본인지 인증할 수 없으므로 제외한다.
    ambiguous = data.duplicated(KEY, keep=False)
    data = data.loc[~ambiguous].copy()
    return data.rename(
        columns={"odds": "archive_odds_text", "result": "archive_result"}
    )


def prepare_snapshots(raw: pd.DataFrame) -> pd.DataFrame:
    """관측시각·가격과 결과가 아닌 시장 상태용 ``result``를 보관한다."""
    missing = set(SNAPSHOT_COLS) - set(raw.columns)
    if missing:
        raise ValueError(f"snapshot columns missing: {sorted(missing)}")
    data = _numeric_keys(raw[SNAPSHOT_COLS])
    data = data[
        (data["year"] == 2026)
        & (data["sport"] == "bs")
        & (data["league"] == "KBO")
        & (data["market_family"] == "승패")
        & (data["n_way"] == 2)
    ].copy()
    data["ts"] = pd.to_datetime(data["ts"], errors="coerce", utc=True)
    data["snapshot_odds"] = data["odds"].map(parse_odds)
    data["snapshot_result_state"] = data["result"].astype(str).str.strip()
    data = data.dropna(subset=["ts", "snapshot_odds"])
    data = data.rename(columns={"odds": "snapshot_odds_text"})
    return (
        data.sort_values(KEY + ["ts"], kind="stable")
        .drop_duplicates(KEY + ["ts"], keep="last")
        [KEY + [
            "ts", "snapshot_odds_text", "snapshot_odds",
            "snapshot_result_state",
        ]]
    )


def join_observations(archive: pd.DataFrame, snapshots: pd.DataFrame) -> pd.DataFrame:
    columns = KEY + [
        "kickoff_utc", "archive_odds_text", "archive_odds", "archive_result",
        "home", "away",
    ]
    return snapshots.merge(
        archive[columns], on=KEY, how="inner", validate="many_to_one"
    )


def _explicit_pregame(observations: pd.DataFrame) -> pd.DataFrame:
    """명시적 경기전 상태 열이 없거나 값이 다르면 실행 후보에서 제외한다."""
    if "snapshot_result_state" not in observations.columns:
        return observations.iloc[0:0].copy()
    return observations[
        observations["snapshot_result_state"].eq(EXPLICIT_PREGAME_RESULT)
    ].copy()


def latest_pregame(observations: pd.DataFrame) -> pd.DataFrame:
    """명시적 경기전 상태이며 킥오프보다 엄격히 이른 마지막 가격."""
    pregame = _explicit_pregame(observations)
    eligible = pregame[pregame["ts"] < pregame["kickoff_utc"]]
    return (
        eligible.sort_values(KEY + ["ts"], kind="stable")
        .groupby(KEY, sort=False, as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )


def at_t_minus(
    observations: pd.DataFrame,
    target_minutes: int = 30,
    stale_minutes: int = 35,
) -> pd.DataFrame:
    """명시적 경기전인 T-target 가격(목표보다 최대 stale분 이전)."""
    pregame = _explicit_pregame(observations)
    target = pregame["kickoff_utc"] - pd.to_timedelta(target_minutes, unit="m")
    age = target - pregame["ts"]
    eligible = pregame[
        (pregame["ts"] < pregame["kickoff_utc"])
        & (age >= pd.Timedelta(0))
        & (age <= pd.Timedelta(minutes=stale_minutes))
    ]
    return (
        eligible.sort_values(KEY + ["ts"], kind="stable")
        .groupby(KEY, sort=False, as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )


def _favorite(odds: tuple[float, float]) -> str | None:
    if odds[0] < odds[1]:
        return "home"
    if odds[1] < odds[0]:
        return "away"
    return None


def compare(selected: pd.DataFrame) -> dict:
    mismatch = []
    reversal = []
    direction_comparable = []
    for row in selected.itertuples(index=False):
        archive_odds = tuple(row.archive_odds)
        snapshot_odds = tuple(row.snapshot_odds)
        mismatch.append(not np.allclose(archive_odds, snapshot_odds, rtol=0, atol=1e-12))
        old, seen = _favorite(archive_odds), _favorite(snapshot_odds)
        comparable = old is not None and seen is not None
        direction_comparable.append(comparable)
        reversal.append(comparable and old != seen)
    n = len(selected)
    direction_n = int(sum(direction_comparable))
    mismatch_n = int(sum(mismatch))
    reversal_n = int(sum(reversal))
    return {
        "comparable_n": int(n),
        "mismatch_n": mismatch_n,
        "mismatch_rate": mismatch_n / n if n else None,
        "favorite_direction_comparable_n": direction_n,
        "favorite_reversal_n": reversal_n,
        "favorite_reversal_rate": reversal_n / direction_n if direction_n else None,
    }


def archive_price_first_seen(archive: pd.DataFrame, observations: pd.DataFrame,
                             example_limit: int = 10) -> dict:
    same_price = observations[
        [np.allclose(a, b, rtol=0, atol=1e-12)
         for a, b in zip(observations["archive_odds"], observations["snapshot_odds"])]
    ]
    first = (
        same_price.sort_values(KEY + ["ts"], kind="stable")
        .groupby(KEY, sort=False, as_index=False)
        .head(1)
    )
    # ts==kickoff도 구매 가능한 경기 전 가격이 아니므로 여기에 포함한다.
    post = first[first["ts"] >= first["kickoff_utc"]].copy()
    post["lag_minutes"] = (
        (post["ts"] - post["kickoff_utc"]).dt.total_seconds() / 60
    )
    seen_keys = pd.MultiIndex.from_frame(first[KEY])
    archive_keys = pd.MultiIndex.from_frame(archive[KEY])
    never = int((~archive_keys.isin(seen_keys)).sum())
    examples = []
    for row in post.sort_values("lag_minutes", ascending=False).head(example_limit).itertuples():
        examples.append({
            "year": int(row.year), "round": int(row.round),
            "game_no": int(row.game_no),
            "home": str(row.home), "away": str(row.away),
            "kickoff_utc": row.kickoff_utc.isoformat(),
            "archive_price_first_observed_utc": row.ts.isoformat(),
            "snapshot_result_state": str(row.snapshot_result_state),
            "lag_minutes": float(row.lag_minutes),
            "archive_odds": list(row.archive_odds),
            "result_from_archive": str(row.archive_result),
        })
    return {
        "matching_price_ever_observed_n": int(len(first)),
        "never_observed_n": never,
        "first_observed_at_or_after_kickoff_n": int(len(post)),
        "examples": examples,
    }


def audit_frames(archive_raw: pd.DataFrame, snapshot_raw: pd.DataFrame) -> dict:
    archive = prepare_archive(archive_raw)
    snapshots = prepare_snapshots(snapshot_raw)
    observations = join_observations(archive, snapshots)
    latest = latest_pregame(observations)
    t30 = at_t_minus(
        observations,
        target_minutes=PRIMARY_TARGET_MINUTES,
        stale_minutes=PRIMARY_STALE_MINUTES,
    )
    primary = {
        "name": "t_minus_30",
        "target_minutes_before_kickoff": PRIMARY_TARGET_MINUTES,
        "maximum_staleness_minutes": PRIMARY_STALE_MINUTES,
        "required_snapshot_result_state": EXPLICIT_PREGAME_RESULT,
        # result=경기전은 경기 상태일 뿐 실제 발매 창구가 열려 있었다는
        # 독립적인 증거가 아니다. sale/open 필드가 없으므로 항상 닫힌다.
        "sale_open_status_observed": False,
        "operationally_valid": False,
        "operational_invalid_reason": (
            "snapshot has no independently observed sale/open status; "
            "result=경기전 alone does not prove the price was purchasable"
        ),
        **compare(t30),
    }
    markets_seen = observations[KEY].drop_duplicates()
    explicit_pregame_n = int(
        observations["snapshot_result_state"].eq(EXPLICIT_PREGAME_RESULT).sum()
    )
    return {
        "status": "research_only",
        "operationally_valid": False,
        "scope": "KBO 2026 승패 2-way settled archive vs timestamped snapshots",
        "protocol": {
            "join_key": KEY,
            "kickoff_conversion": "archive date_text interpreted as Asia/Seoul, converted to UTC",
            "pregame_rule": "strict ts < kickoff AND snapshot result exactly 경기전",
            "latest_pregame": (
                "last valid observed price strictly before kickoff with explicit "
                "snapshot result=경기전"
            ),
            "t_minus_30": (
                f"primary: last explicit-pregame price at or before kickoff-"
                f"{PRIMARY_TARGET_MINUTES}m, observed no more than "
                f"{PRIMARY_STALE_MINUTES}m before that target"
            ),
            "outcome_source": (
                "archive settlement row only; snapshot result is used only as "
                "a pregame-state eligibility field"
            ),
            "sale_open_evidence": (
                "not present in snapshots; explicit result=경기전 is necessary "
                "but not sufficient to prove purchasability"
            ),
        },
        "source_counts": {
            "archive_raw_rows": int(len(archive_raw)),
            "snapshot_raw_rows": int(len(snapshot_raw)),
            "settled_archive_markets": int(len(archive)),
            "archive_markets_seen_in_snapshots": int(len(markets_seen)),
            "snapshot_observations_joined": int(len(observations)),
            "explicit_pregame_observations_joined": explicit_pregame_n,
            "non_pregame_state_observations_excluded": int(
                len(observations) - explicit_pregame_n
            ),
        },
        "primary": primary,
        "latest_pregame": compare(latest),
        "t_minus_30": compare(t30),
        "archive_price_first_observed": archive_price_first_seen(archive, observations),
        "limitations": [
            "Archive odds has no collected_at and is not certified as a purchasable pregame price.",
            "A price first seen after kickoff may have existed earlier; it proves missing timing evidence, not when the bookmaker changed it.",
            "Snapshots cover only the watcher period and may miss opening prices or intervals between polls.",
            "Snapshot result must be exactly 경기전; blank, locked, in-play, and settled states are excluded even before nominal kickoff.",
            "Results label correctness is inherited from settlement archive and is never inferred from snapshot rows.",
            "No independent sale/open status is recorded, so even explicit-pregame prices are not operationally certified as purchasable.",
            "This audit is descriptive and research_only; it does not validate historical ROI at archive prices.",
        ],
    }


def load_snapshot_files(files: Iterable[Path]) -> tuple[pd.DataFrame, dict]:
    frames = []
    raw_rows = 0
    paths = list(files)
    for path in paths:
        part = pd.read_csv(
            path, usecols=SNAPSHOT_COLS, dtype=str, on_bad_lines="skip",
            low_memory=False,
        )
        raw_rows += len(part)
        # 메모리 사용을 줄이기 위해 샤드마다 범위를 먼저 자른다.
        part = part[
            (part["year"] == "2026")
            & (part["sport"] == "bs")
            & (part["league"] == "KBO")
            & (part["market_family"] == "승패")
            & (part["n_way"] == "2")
        ]
        if not part.empty:
            frames.append(part)
    empty = pd.DataFrame(columns=SNAPSHOT_COLS)
    return (pd.concat(frames, ignore_index=True) if frames else empty), {
        "snapshot_files": len(paths), "all_snapshot_file_rows": raw_rows,
    }


def main() -> int:
    files = sorted(SNAPSHOT_DIR.glob("odds_timeseries_*.csv"))
    legacy = SNAPSHOT_DIR / "odds_timeseries.csv"
    if legacy.exists():
        files.insert(0, legacy)
    if not files:
        raise FileNotFoundError(f"no snapshot files in {SNAPSHOT_DIR}")
    archive = pd.read_csv(ARCHIVE, usecols=ARCHIVE_COLS, low_memory=False)
    snapshots, source = load_snapshot_files(files)
    report = audit_frames(archive, snapshots)
    report["source_counts"].update(source)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
