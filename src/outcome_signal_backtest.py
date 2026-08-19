"""배당 신호가 실제 경기 결과와 수익으로 이어졌는지 검증한다.

이 스크립트는 두 질문을 분리한다.

1. 프로토 자체 배당이 움직인 방향을 따라가거나 거슬렀을 때 실제 ROI가 어땠는가.
2. 같은 시점의 해외 배당이 제시한 확률로 프로토에 양(+)의 EV가 있었을 때
   실제로 수익이 났는가.

모든 가격은 경기 시작 전에 관측된 값만 사용한다. 같은 실제 경기가 여러 회차에
중복 발매된 경우 한 경기당 한 번만 베팅한다. 결과는 오직 정산에만 쓴다.

사용:
    python src/outcome_signal_backtest.py
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
SNAP_DIR = ROOT / "data" / "raw" / "snapshots"
OVERSEAS_DIR = ROOT / "data" / "raw" / "overseas"

SNAP_COLS = [
    "ts", "year", "round", "game_no", "sport", "league", "market_family",
    "n_way", "market_label", "home", "away", "date_text", "odds", "result",
]
OUTCOME_IDX = {
    (2, "홈승"): 0,
    (2, "홈패"): 1,
    (3, "홈승"): 0,
    (3, "무승부"): 1,
    (3, "홈패"): 2,
}
MAIN_MARKETS = {("승패", 2), ("승무패", 3)}
TEAM_NUM_PRE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s+")
TEAM_NUM_SUF = re.compile(r"\s+-?\d+(?:\.\d+)?\s*$")
HOME_SCORE = re.compile(r"\s+(-?\d+)\s*$")
AWAY_SCORE = re.compile(r"^\s*(-?\d+)\s+")
DATE_TIME = re.compile(r"(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})")


def clean_team(value: object) -> str:
    text = str(value).strip()
    return TEAM_NUM_SUF.sub("", TEAM_NUM_PRE.sub("", text)).strip()


def parse_odds(value: object, n_way: int) -> np.ndarray | None:
    try:
        out = np.asarray([float(x) for x in str(value).split(",")], dtype=float)
    except (TypeError, ValueError):
        return None
    if len(out) != n_way or not np.isfinite(out).all() or (out <= 1.001).any():
        return None
    return out


def devig(odds: np.ndarray) -> np.ndarray:
    inv = 1.0 / odds
    return inv / inv.sum()


def load_snapshots() -> tuple[pd.DataFrame, dict]:
    files = sorted(SNAP_DIR.glob("odds_timeseries_*.csv"))
    if not files:
        raise FileNotFoundError("스냅샷 파일이 없습니다")

    frames = []
    raw_rows = 0
    for file in files:
        part = pd.read_csv(
            file,
            usecols=SNAP_COLS,
            dtype=str,
            on_bad_lines="skip",
            low_memory=False,
        )
        raw_rows += len(part)
        frames.append(part)
    data = pd.concat(frames, ignore_index=True)

    for col in ("year", "round", "game_no", "n_way"):
        data[col] = pd.to_numeric(data[col], errors="coerce")
    data["ts"] = pd.to_datetime(data["ts"], errors="coerce", utc=True)
    dt = data["date_text"].astype(str).str.extract(DATE_TIME)
    for col in dt.columns:
        dt[col] = pd.to_numeric(dt[col], errors="coerce")
    naive = pd.to_datetime(
        {
            "year": data["year"],
            "month": dt[0],
            "day": dt[1],
            "hour": dt[2],
            "minute": dt[3],
        },
        errors="coerce",
    )
    data["kickoff"] = naive.dt.tz_localize(
        "Asia/Seoul", ambiguous="NaT", nonexistent="NaT"
    ).dt.tz_convert("UTC")

    data = data.dropna(subset=["ts", "kickoff", "year", "round", "game_no", "n_way"])
    data[["year", "round", "game_no", "n_way"]] = data[
        ["year", "round", "game_no", "n_way"]
    ].astype(int)
    data = data[
        [
            (fam, nway) in MAIN_MARKETS
            for fam, nway in zip(data["market_family"], data["n_way"])
        ]
    ].copy()
    data["home_team"] = data["home"].map(clean_team)
    data["away_team"] = data["away"].map(clean_team)
    data["market_id"] = (
        data["year"].astype(str)
        + "-"
        + data["round"].astype(str)
        + "-"
        + data["game_no"].astype(str)
    )
    data["event_id"] = (
        data["league"].astype(str)
        + "|"
        + data["kickoff"].astype(str)
        + "|"
        + data["home_team"]
        + "|"
        + data["away_team"]
    )
    data = data.sort_values(["market_id", "ts"]).drop_duplicates(
        ["market_id", "ts"], keep="last"
    )
    meta = {
        "files": len(files),
        "raw_rows": raw_rows,
        "usable_main_rows": len(data),
        "first_observed": data["ts"].min().isoformat(),
        "last_observed": data["ts"].max().isoformat(),
    }
    return data, meta


def settled_markets(data: pd.DataFrame) -> pd.DataFrame:
    ok = data[
        [
            (int(n), str(r)) in OUTCOME_IDX
            for n, r in zip(data["n_way"], data["result"])
        ]
    ].copy()
    ok["winner_idx"] = [
        OUTCOME_IDX[(int(n), str(r))] for n, r in zip(ok["n_way"], ok["result"])
    ]
    out = ok.groupby("market_id", sort=False).tail(1).copy()
    out["home_score"] = pd.to_numeric(
        out["home"].astype(str).str.extract(HOME_SCORE)[0], errors="coerce"
    )
    out["away_score"] = pd.to_numeric(
        out["away"].astype(str).str.extract(AWAY_SCORE)[0], errors="coerce"
    )
    return out[
        [
            "market_id", "event_id", "kickoff", "sport", "league", "market_family",
            "n_way", "home_team", "away_team", "winner_idx", "result", "home_score",
            "away_score",
        ]
    ]


def proto_prices_at(
    data: pd.DataFrame,
    settled: pd.DataFrame,
    cutoff_min: int,
    stale_min: int = 35,
) -> pd.DataFrame:
    pre = data[data["ts"] < data["kickoff"]].copy()
    pre = pre.merge(
        settled[["market_id", "winner_idx"]], on="market_id", how="inner",
        validate="many_to_one",
    )
    opening = pre.groupby("market_id", sort=False).head(1)[
        ["market_id", "ts", "odds"]
    ].rename(columns={"ts": "open_ts", "odds": "open_odds"})

    target = pre["kickoff"] - pd.to_timedelta(cutoff_min, unit="m")
    age = target - pre["ts"]
    current = pre[(age >= pd.Timedelta(0)) & (age <= pd.Timedelta(minutes=stale_min))]
    current = current.groupby("market_id", sort=False).tail(1).copy()
    current = current.merge(opening, on="market_id", how="inner", validate="one_to_one")
    current = current[current["open_ts"] < current["ts"]].copy()

    rows = []
    for row in current.itertuples(index=False):
        old = parse_odds(row.open_odds, int(row.n_way))
        now = parse_odds(row.odds, int(row.n_way))
        if old is None or now is None:
            continue
        rows.append(
            {
                "market_id": row.market_id,
                "event_id": row.event_id,
                "kickoff": row.kickoff,
                "league": row.league,
                "n_way": int(row.n_way),
                "winner_idx": int(row.winner_idx),
                "observed_at": row.ts,
                "open_ts": row.open_ts,
                "odds": now,
                "p_proto": devig(now),
                "delta": devig(now) - devig(old),
            }
        )
    return pd.DataFrame(rows)


def movement_bets(prices: pd.DataFrame, threshold_pp: float, mode: str) -> pd.DataFrame:
    if prices.empty:
        return pd.DataFrame()
    rows = []
    threshold = threshold_pp / 100.0
    for row in prices.itertuples(index=False):
        if mode == "follow":
            selection = int(np.argmax(row.delta))
            strength = float(row.delta[selection])
        elif mode == "fade":
            selection = int(np.argmin(row.delta))
            strength = float(-row.delta[selection])
        else:
            raise ValueError(mode)
        if strength + 1e-12 < threshold:
            continue
        hit = int(selection == row.winner_idx)
        odds = float(row.odds[selection])
        rows.append(
            {
                "event_id": row.event_id,
                "market_id": row.market_id,
                "kickoff": row.kickoff,
                "league": row.league,
                "selection": selection,
                "signal_pp": strength * 100,
                "odds": odds,
                "market_p": float(row.p_proto[selection]),
                "hit": hit,
                "ret": odds - 1.0 if hit else -1.0,
            }
        )
    bets = pd.DataFrame(rows)
    if bets.empty:
        return bets
    # 같은 실제 경기가 여러 회차에 있으면 당시 관측 가능한 가장 강한 신호 하나만 쓴다.
    return (
        bets.sort_values(["event_id", "signal_pp", "odds"], ascending=[True, False, False])
        .drop_duplicates("event_id", keep="first")
        .sort_values("kickoff")
        .reset_index(drop=True)
    )


def bootstrap_ci(values: np.ndarray, seed: int = 42, n_boot: int = 12000) -> list[float | None]:
    values = np.asarray(values, dtype=float)
    if len(values) < 2:
        return [None, None]
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=float)
    chunk = 500
    for start in range(0, n_boot, chunk):
        size = min(chunk, n_boot - start)
        idx = rng.integers(0, len(values), size=(size, len(values)))
        means[start:start + size] = values[idx].mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return [float(lo), float(hi)]


def summarize(bets: pd.DataFrame, label: str) -> dict:
    if bets.empty:
        return {"label": label, "n": 0}
    return {
        "label": label,
        "n": int(len(bets)),
        "events": int(bets["event_id"].nunique()),
        "hit_rate": float(bets["hit"].mean()),
        "avg_odds": float(bets["odds"].mean()),
        "mean_market_p": float(bets["market_p"].mean()) if "market_p" in bets else None,
        "roi": float(bets["ret"].mean()),
        "roi_ci95": bootstrap_ci(bets["ret"].to_numpy()),
        "profit_units": float(bets["ret"].sum()),
        "first_kickoff": bets["kickoff"].min().isoformat(),
        "last_kickoff": bets["kickoff"].max().isoformat(),
    }


def movement_analysis(data: pd.DataFrame, settled: pd.DataFrame) -> dict:
    cutoffs = (360, 90, 30, 10)
    thresholds = (0.5, 1.0, 2.0)
    cached = {cutoff: proto_prices_at(data, settled, cutoff) for cutoff in cutoffs}
    sensitivity = []
    for cutoff in cutoffs:
        for threshold in thresholds:
            for mode in ("follow", "fade"):
                bets = movement_bets(cached[cutoff], threshold, mode)
                sensitivity.append(summarize(bets, f"{mode}|T-{cutoff}|{threshold:.1f}pp"))

    primary_all = movement_bets(cached[30], 1.0, "follow")
    primary_fade = movement_bets(cached[30], 1.0, "fade")
    if primary_all.empty:
        split_time = None
        early = primary_all
        holdout = primary_all
    else:
        ordered_events = (
            primary_all[["event_id", "kickoff"]]
            .drop_duplicates()
            .sort_values("kickoff")
            .reset_index(drop=True)
        )
        split_idx = max(1, int(len(ordered_events) * 2 / 3))
        split_time = ordered_events.iloc[min(split_idx, len(ordered_events) - 1)]["kickoff"]
        early = primary_all[primary_all["kickoff"] < split_time]
        holdout = primary_all[primary_all["kickoff"] >= split_time]

    return {
        "definition": "오프닝 대비 devig 확률 이동; 주 검정=T-30, 1.0%p 이상, 상승 방향 추종",
        "primary": summarize(primary_all, "주 검정: T-30 1.0pp 추종"),
        "primary_early": summarize(early, "시간순 앞 2/3"),
        "primary_holdout": summarize(holdout, "시간순 뒤 1/3"),
        "primary_fade": summarize(primary_fade, "대조: T-30 1.0pp 역행"),
        "split_time": split_time.isoformat() if split_time is not None else None,
        "sensitivity": sensitivity,
        "available_markets_by_cutoff": {str(k): int(len(v)) for k, v in cached.items()},
    }


def proto_events_for_aliases(settled: pd.DataFrame) -> pd.DataFrame:
    events = settled.dropna(subset=["home_score", "away_score"]).copy()
    events["date"] = events["kickoff"].dt.tz_convert("Asia/Seoul").dt.date
    events[["home_score", "away_score"]] = events[["home_score", "away_score"]].astype(int)
    return events.drop_duplicates("event_id")[
        [
            "event_id", "kickoff", "date", "league", "home_team", "away_team",
            "home_score", "away_score", "winner_idx", "n_way",
        ]
    ]


def infer_aliases(proto_events: pd.DataFrame) -> tuple[dict, dict]:
    """날짜+최종스코어가 양쪽에서 모두 유일한 경기만 팀명 교차표에 쓴다.

    최종스코어는 이름 동일성 확인에만 사용하며 베팅 신호에는 들어가지 않는다.
    한 영문 팀명이 둘 이상의 프로토 팀명으로 연결되면 그 팀은 버린다.
    """
    proto_index: dict[tuple, list] = defaultdict(list)
    for row in proto_events.itertuples(index=False):
        proto_index[(row.league, row.date, row.home_score, row.away_score)].append(row)

    pairs = []
    historical_rows = 0
    for file in sorted(OVERSEAS_DIR.glob("*.json")):
        league = file.stem
        if league not in {"MLB", "KBO", "NPB", "K리그1"}:
            continue
        rows = json.loads(file.read_text(encoding="utf-8"))
        historical_rows += len(rows)
        overseas_index: dict[tuple, list] = defaultdict(list)
        for row in rows:
            try:
                date = pd.Timestamp(row["date"]).date()
                key = (league, date, int(row["home_score"]), int(row["away_score"]))
            except (KeyError, TypeError, ValueError):
                continue
            overseas_index[key].append(row)
        for key, proto_rows in proto_index.items():
            if key[0] != league or len(proto_rows) != 1 or len(overseas_index.get(key, [])) != 1:
                continue
            p = proto_rows[0]
            o = overseas_index[key][0]
            pairs.extend(
                [
                    ((league, str(o["home_en"])), p.home_team),
                    ((league, str(o["away_en"])), p.away_team),
                ]
            )

    counts: dict[tuple, Counter] = defaultdict(Counter)
    for key, value in pairs:
        counts[key][value] += 1
    aliases = {}
    conflicts = {}
    for key, counter in counts.items():
        if len(counter) == 1:
            aliases[key] = next(iter(counter))
        else:
            conflicts[f"{key[0]}|{key[1]}"] = dict(counter)
    meta = {
        "historical_rows": historical_rows,
        "unique_match_pairs": len(pairs) // 2,
        "aliases": len(aliases),
        "conflicts": conflicts,
    }
    return aliases, meta


def load_live_odds() -> tuple[pd.DataFrame, dict]:
    file = OVERSEAS_DIR / "live_odds.csv"
    live = pd.read_csv(file, dtype=str, on_bad_lines="skip", engine="python")
    raw_rows = len(live)
    live["observed_at"] = pd.to_datetime(live["observed_at"], errors="coerce", utc=True)
    live = live.dropna(subset=["observed_at", "league", "home_en", "away_en", "odds"])
    dup = live.duplicated(["observed_at", "league", "home_en", "away_en"], keep=False)
    duplicated_fixture_rows = int(dup.sum())
    # 동시에 같은 팀 조합이 두 번 보이면 경기 ID가 없어 어느 경기인지 알 수 없다.
    live = live[~dup].copy()
    return live, {"raw_rows": raw_rows, "duplicated_fixture_rows_removed": duplicated_fixture_rows}


def map_live_to_events(
    live: pd.DataFrame,
    proto_events: pd.DataFrame,
    aliases: dict,
    horizon_hours: int = 48,
) -> tuple[pd.DataFrame, dict]:
    live = live.copy()
    live["home_team"] = [aliases.get((l, h)) for l, h in zip(live["league"], live["home_en"])]
    live["away_team"] = [aliases.get((l, a)) for l, a in zip(live["league"], live["away_en"])]
    mapped_names = live["home_team"].notna() & live["away_team"].notna()
    live = live[mapped_names].copy()

    by_matchup: dict[tuple, list] = defaultdict(list)
    for row in proto_events.itertuples(index=False):
        by_matchup[(row.league, row.home_team, row.away_team)].append(row)
    for rows in by_matchup.values():
        rows.sort(key=lambda x: x.kickoff)

    matched = []
    ambiguous = 0
    no_future = 0
    horizon = pd.Timedelta(hours=horizon_hours)
    for row in live.itertuples(index=False):
        candidates = [
            event for event in by_matchup.get((row.league, row.home_team, row.away_team), [])
            if pd.Timedelta(0) < event.kickoff - row.observed_at <= horizon
        ]
        if len(candidates) != 1:
            if len(candidates) > 1:
                ambiguous += 1
            else:
                no_future += 1
            continue
        event = candidates[0]
        odds = parse_odds(row.odds, int(event.n_way))
        if odds is None:
            continue
        matched.append(
            {
                "event_id": event.event_id,
                "kickoff": event.kickoff,
                "league": event.league,
                "n_way": int(event.n_way),
                "winner_idx": int(event.winner_idx),
                "observed_at": row.observed_at,
                "odds_os": odds,
                "p_os": devig(odds),
            }
        )
    out = pd.DataFrame(matched)
    meta = {
        "rows_with_both_team_aliases": int(len(live)),
        "matched_rows": int(len(out)),
        "ambiguous_future_rows_removed": ambiguous,
        "no_future_event_rows": no_future,
        "matched_events": int(out["event_id"].nunique()) if not out.empty else 0,
    }
    return out, meta


def external_gap_bets(
    proto_prices: pd.DataFrame,
    live_events: pd.DataFrame,
    cutoff_min: int,
    min_ev: float,
    stale_min: int = 35,
) -> pd.DataFrame:
    if proto_prices.empty or live_events.empty:
        return pd.DataFrame()
    target = live_events["kickoff"] - pd.to_timedelta(cutoff_min, unit="m")
    age = target - live_events["observed_at"]
    os_at = live_events[(age >= pd.Timedelta(0)) & (age <= pd.Timedelta(minutes=stale_min))]
    os_at = os_at.groupby("event_id", sort=False).tail(1).copy()
    if os_at.empty:
        return pd.DataFrame()

    proto = proto_prices.copy()
    joined = proto.merge(
        os_at[["event_id", "p_os"]], on="event_id", how="inner", validate="many_to_one"
    )
    rows = []
    for row in joined.itertuples(index=False):
        ev = row.p_os * row.odds - 1.0
        selection = int(np.argmax(ev))
        best_ev = float(ev[selection])
        if best_ev <= min_ev:
            continue
        hit = int(selection == row.winner_idx)
        odds = float(row.odds[selection])
        rows.append(
            {
                "event_id": row.event_id,
                "market_id": row.market_id,
                "kickoff": row.kickoff,
                "league": row.league,
                "selection": selection,
                "signal_pp": float((row.p_os[selection] - row.p_proto[selection]) * 100),
                "estimated_ev": best_ev,
                "odds": odds,
                "market_p": float(row.p_proto[selection]),
                "hit": hit,
                "ret": odds - 1.0 if hit else -1.0,
            }
        )
    bets = pd.DataFrame(rows)
    if bets.empty:
        return bets
    # 여러 회차가 있으면 외부확률 기준 EV가 가장 큰 실제 구매 선택지 하나만 사용한다.
    return (
        bets.sort_values(["event_id", "estimated_ev", "odds"], ascending=[True, False, False])
        .drop_duplicates("event_id", keep="first")
        .sort_values("kickoff")
        .reset_index(drop=True)
    )


def external_analysis(data: pd.DataFrame, settled: pd.DataFrame) -> dict:
    events = proto_events_for_aliases(settled)
    aliases, alias_meta = infer_aliases(events)
    live, live_meta = load_live_odds()
    mapped, map_meta = map_live_to_events(live, events, aliases)

    results = []
    for cutoff in (360, 90, 30, 10):
        proto = proto_prices_at(data, settled, cutoff)
        for min_ev in (0.0, 0.02, 0.05):
            bets = external_gap_bets(proto, mapped, cutoff, min_ev)
            results.append(summarize(bets, f"T-{cutoff}|EV>{min_ev:.0%}"))
    primary_proto = proto_prices_at(data, settled, 30)
    primary_bets = external_gap_bets(primary_proto, mapped, 30, 0.0)
    return {
        "definition": "동시점 해외 devig 확률 × 구매 가능한 프로토 배당 - 1; 주 검정=T-30, EV>0",
        "alias_meta": alias_meta,
        "live_meta": live_meta,
        "mapping_meta": map_meta,
        "primary": summarize(primary_bets, "주 검정: T-30 해외기준 EV>0"),
        "sensitivity": results,
    }


def main() -> int:
    data, source_meta = load_snapshots()
    settled = settled_markets(data)
    report = {
        "generated_from": "local historical snapshots; no network",
        "source": source_meta,
        "settled_markets": int(len(settled)),
        "settled_events": int(settled["event_id"].nunique()),
        "movement": movement_analysis(data, settled),
        "external_gap": external_analysis(data, settled),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
