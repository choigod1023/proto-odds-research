"""Archive-only inputs for the nonlinear research lab; no runtime IO or defaults.

One favorite per full-time market. Identity includes scheduled KST kickoff and
exact line, but not reissue round/game number. Unknown score history stays NaN.
Team history is available next KST calendar day, capped at 20 games and 180 days.
Own/opponent follow the selected team; totals and draws use home/away order.
Form is mean win=1/draw=.5/loss=0; rest is KST calendar days; std uses ddof=0.
Only the exported feature lists are model inputs (never outcome/identity fields).
Archive prices have no observation timestamp: these are retrospective inputs.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict, deque
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

if __package__:  # Support package imports and the repository's script convention.
    from .bets import winner_index
    from .devig import market_probabilities
    from .matches import actual_game_year, clean_team
else:
    from bets import winner_index
    from devig import market_probabilities
    from matches import actual_game_year, clean_team

MARKET_FEATURES = [
    "q", "logit_q", "log_odds", "overround", "favorite_gap", "entropy",
    "line", "direction", "n_way",
]
_STATS = ["scored_mean", "scored_std", "conceded_mean", "conceded_std",
          "form", "rest_days", "sample_count"]
TEAM_FEATURES = ([f"{side}_{stat}" for side in ("own", "opponent") for stat in _STATS]
                 + [f"{stat}_diff" for stat in _STATS]
                 + ["own_missing", "opponent_missing"])
_COLUMNS = list(dict.fromkeys([
    "row_id", "event_key", "kickoff", "sport", "league", "market", "market_label",
    "n_way", "sel", "odds", "q", "y", "winner", "is_void",
    *MARKET_FEATURES, *TEAM_FEATURES,
]))
_SCHEMA = ("year round game_no date_text sport league market_tag market_label "
           "market_family booking_class market_type n_way home away odds "
           "overround result is_void").split()
_SELECTIONS = {
    ("승패", 2): ("홈승", "홈패"),
    ("승무패", 3): ("홈승", "무승부", "홈패"),
    ("핸디캡", 2): ("핸디승", "핸디패"),
    ("핸디캡", 3): ("핸디승", "핸디무", "핸디패"),
    ("언더오버", 2): ("언더", "오버"),
}
_VOID = {"취소", "연기", "중단", "무효"}
_DATETIME = re.compile(r"^\s*(\d{2})\.(\d{2})(?:\([^)]*\))?\s+(\d{2}):(\d{2})\s*$")
_PARTIAL = re.compile(
    r"전반|후반|쿼터|세트|이닝|피리어드|\d+\s*회|half|quarter|period|inning|"
    r"\bset\b|\b[1-9][hq]\b|\bf[1-9]\b", re.I)
_NUMBER = r"([+-]?(?:\d+(?:\.\d+)?|\.\d+))"
_HOME_SCORE = re.compile(r"^(.+?)\s+(\d+)$")
_AWAY_SCORE = re.compile(r"^(\d+)\s+(.+?)$")


def _stable_id(parts: tuple) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _market(row) -> tuple[str, int, str, str]:
    """Canonical family, option count, exact decimal line, display label."""
    descriptors = (row.market_family, row.market_label, row.market_type,
                   row.market_tag, row.booking_class)
    if any(_PARTIAL.search(text) or re.match(r"^h(?:\s|\(|$)", text)
           for text in descriptors):
        raise ValueError("partial_period")
    family = row.market_family
    try:
        n_way = int(row.n_way)
    except ValueError:
        raise ValueError("unsupported_market") from None
    if (family, n_way) not in _SELECTIONS:
        raise ValueError("unsupported_market")
    declared_type = re.sub(r"\s*\(\d+-way\)$", "", row.market_type).strip()
    if declared_type and declared_type != family:
        raise ValueError("conflicting_market_type")
    if (row.market_tag == "d1" or
            (row.market_tag == "un" and family != "언더오버") or
            (row.market_tag == "hp" and family != "핸디캡")):
        raise ValueError("conflicting_market_tag")
    if family in ("핸디캡", "언더오버"):
        prefix = r"(?:H|핸디캡)" if family == "핸디캡" else r"(?:U|U/O|언더오버)"
        match = re.fullmatch(prefix + r"\s*" + _NUMBER, row.market_label)
        if match is None:
            raise ValueError("invalid_line")
        number = Decimal(match[1])
        if not math.isfinite(float(number)) or (family == "언더오버" and number < 0):
            raise ValueError("invalid_line")
        line = format(number, "f")
        if "." in line:
            line = line.rstrip("0").rstrip(".")
        line = "0" if number == 0 else line
        return family, n_way, line, f"{'H' if family == '핸디캡' else 'U'} {line}"
    if row.market_label not in ("", "-", "일반", family):
        raise ValueError("unsupported_market_label")
    return family, n_way, "0", family


def _team(text: str, home: bool) -> str:
    # Protect the non-score edge: e.g. home '1860 뮌헨 2', away '1 샬케 04'.
    # Score stripping still delegates to the canonical clean_team helper.
    return (clean_team("@" + text).removeprefix("@") if home else
            clean_team(text + "@").removesuffix("@"))


def _profile(history: deque, day: pd.Timestamp) -> dict[str, float]:
    cutoff = day - pd.Timedelta(days=180)
    while history and history[0][0] < cutoff:
        history.popleft()
    profile = dict.fromkeys(_STATS, np.nan)
    profile["sample_count"] = float(len(history))
    if history:
        scores = np.asarray([(item[1], item[2]) for item in history], dtype=float)
        profile.update(scored_mean=float(scores[:, 0].mean()),
                       scored_std=float(scores[:, 0].std()),
                       conceded_mean=float(scores[:, 1].mean()),
                       conceded_std=float(scores[:, 1].std()),
                       form=float(np.mean(np.where(scores[:, 0] > scores[:, 1], 1.,
                                          np.where(scores[:, 0] == scores[:, 1], .5, 0.)))),
                       rest_days=float((day - history[-1][0]).days))
    return profile


def load_research_rows(games_path: Path) -> tuple[pd.DataFrame, dict]:
    """Read only the explicitly supplied archive CSV; return favorite rows and QC.

    Invalid/unsupported/unsettled rows are counted in metadata. Reissue price or
    result conflicts exclude the entire market. Conflicting scores or mismatched
    score/result evidence exclude event history, without discarding other markets.
    Void prices remain eligible inputs with winner=-1 and y=NaN.
    """
    path = Path(games_path)
    raw = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    missing = sorted(set(_SCHEMA) - set(raw.columns))
    if missing:
        raise ValueError(f"Archive CSV missing columns: {', '.join(missing)}")
    raw = raw[_SCHEMA].apply(lambda column: column.str.strip())
    parts = raw.date_text.str.extract(_DATETIME).apply(pd.to_numeric, errors="coerce")
    years = actual_game_year(raw.year, raw["round"], parts[0])
    kickoff = pd.to_datetime(dict(year=years, month=parts[0], day=parts[1],
                                 hour=parts[2], minute=parts[3]), errors="coerce")
    valid_clock = parts[2].between(0, 23) & parts[3].between(0, 59)
    raw["kickoff"] = kickoff.where(valid_clock)
    skipped = Counter()
    markets, event_info = {}, {}
    market_counts = Counter()
    conflicts, blocked_history = set(), set()
    score_evidence = defaultdict(set)
    outcome_evidence = defaultdict(set)

    for row in raw.itertuples(index=False):
        if row.sport not in ("bs", "bk", "sc"):
            skipped["unsupported_sport"] += 1
            continue
        try:
            family, n_way, line, label = _market(row)
        except ValueError as error:
            skipped[str(error)] += 1
            continue
        home, away = _team(row.home, True), _team(row.away, False)
        if pd.isna(row.kickoff) or not row.league or not home or not away or home == away:
            skipped["invalid_event"] += 1
            continue
        event = (row.kickoff.isoformat(), row.sport, row.league, home, away)
        event_info[event] = (row.kickoff, row.sport, row.league, home, away)
        flag = row.is_void.lower()
        if flag not in ("true", "false", "1", "0"):
            skipped["invalid_void_flag"] += 1
            continue
        is_void = flag in ("true", "1") or row.result in _VOID
        source = family in ("승패", "승무패")
        # Inspect score conflicts before filtering malformed odds/result rows, so
        # an otherwise rejected reissue cannot make bad score evidence disappear.
        if source:
            if is_void or row.result in _SELECTIONS[(family, n_way)]:
                outcome_evidence[event].add("void" if is_void else row.result)
            hs, aws = _HOME_SCORE.fullmatch(row.home), _AWAY_SCORE.fullmatch(row.away)
            if not is_void and hs and aws:
                scores = (int(hs[2]), int(aws[1]))
                score_evidence[event].add(scores)
                expected = "홈승" if scores[0] > scores[1] else (
                    "홈패" if scores[0] < scores[1] else "무승부")
                if row.result != expected:
                    blocked_history.add(event)
        if not is_void and row.result not in _SELECTIONS[(family, n_way)]:
            skipped["unsettled_or_invalid_result"] += 1
            continue
        winner = -1 if is_void else winner_index(n_way, row.result)
        try:
            odds = tuple(float(value) for value in row.odds.split(","))
            if len(odds) != n_way or any(not math.isfinite(o) or o <= 1 for o in odds):
                raise ValueError
        except ValueError:
            skipped["invalid_odds"] += 1
            continue
        key = (*event, family, n_way, line)
        record = dict(event=event, market=family, market_label=label, n_way=n_way,
                      odds=odds, winner=int(winner), is_void=is_void, line=float(line))
        market_counts[key] += 1
        previous = markets.get(key)
        if previous is None:
            markets[key] = record
        elif any(previous[field] != record[field] for field in ("odds", "winner", "is_void")):
            conflicts.add(key)
            if source:
                blocked_history.add(event)

    blocked_history.update(event for event, values in score_evidence.items() if len(values) > 1)
    blocked_history.update(event for event, values in outcome_evidence.items() if len(values) > 1)
    clean = [(key, record) for key, record in markets.items() if key not in conflicts]
    confirmed = {record["event"] for _, record in clean
                 if record["market"] in ("승패", "승무패") and not record["is_void"]}
    history_events = sorted(event for event in confirmed
                            if len(score_evidence[event]) == 1 and event not in blocked_history)
    histories = defaultdict(lambda: deque(maxlen=20))
    cursor = 0
    rows = []
    for key, record in sorted(clean):
        event = record["event"]
        stamp, sport, league, home, away = event_info[event]
        day = stamp.normalize()
        while cursor < len(history_events):
            past = history_events[cursor]
            past_stamp, sp, lg, ht, at = event_info[past]
            past_day = past_stamp.normalize()
            if past_day >= day:
                break
            hs, aws = next(iter(score_evidence[past]))
            histories[(sp, lg, ht)].append((past_day, hs, aws))
            histories[(sp, lg, at)].append((past_day, aws, hs))
            cursor += 1
        odds = record["odds"]
        probability = market_probabilities(list(odds))
        favorite = min(range(record["n_way"]), key=lambda index: odds[index])
        q = probability[favorite]
        logit_q = min(max(q, 1e-12), 1 - 1e-12)
        selection = _SELECTIONS[(record["market"], record["n_way"])][favorite]
        own_team, opponent = (away, home) if selection in ("홈패", "핸디패") else (home, away)
        own = _profile(histories[(sport, league, own_team)], day)
        opp = _profile(histories[(sport, league, opponent)], day)
        features = {f"own_{stat}": own[stat] for stat in _STATS}
        features.update({f"opponent_{stat}": opp[stat] for stat in _STATS})
        features.update({f"{stat}_diff": own[stat] - opp[stat] for stat in _STATS})
        features.update(own_missing=float(own["sample_count"] == 0),
                        opponent_missing=float(opp["sample_count"] == 0))
        direction = 0 if selection in ("무승부", "핸디무") else (1 if favorite == 0 else -1)
        rows.append(dict(
            row_id=_stable_id(key), event_key=_stable_id(event), kickoff=stamp,
            sport=sport, league=league, market=record["market"],
            market_label=record["market_label"], n_way=record["n_way"], sel=selection,
            odds=odds[favorite], q=q, y=np.nan if record["is_void"] else float(favorite == record["winner"]),
            winner=record["winner"], is_void=record["is_void"],
            logit_q=math.log(logit_q / (1 - logit_q)), log_odds=math.log(odds[favorite]),
            overround=sum(1 / o for o in odds),
            favorite_gap=q - sorted(probability, reverse=True)[1],
            entropy=-sum(p * math.log(p) for p in probability),
            line=record["line"], direction=direction, **features))
    frame = pd.DataFrame(rows, columns=_COLUMNS)
    frame["kickoff"] = pd.to_datetime(frame["kickoff"])
    frame = frame.astype({**{name: float for name in MARKET_FEATURES + TEAM_FEATURES},
                          "n_way": int, "winner": int, "is_void": bool, "odds": float, "y": float})
    metadata = dict(
        input_rows=len(raw), output_rows=len(frame), unique_events=frame.event_key.nunique(),
        void_rows=int(frame.is_void.sum()), skipped_rows=dict(sorted(skipped.items())),
        duplicate_reissues_removed=sum(market_counts[key] - 1 for key, _ in clean),
        conflicting_reissues_excluded=len(conflicts),
        conflicting_rows_excluded=sum(market_counts[key] for key in conflicts),
        score_history_events=len(history_events), score_history_conflicts=len(blocked_history),
        history_last_n=20, history_max_age_days=180,
        history_available="next KST calendar day", source_path=str(path),
        market_features=list(MARKET_FEATURES), team_features=list(TEAM_FEATURES),
    )
    return frame, metadata
