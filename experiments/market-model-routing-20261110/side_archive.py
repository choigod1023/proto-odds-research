"""Replay the audited Betman side-market snapshot from local Git objects.

The archive has final grades and retrospective prices without observation or
settlement timestamps. The generic candidate treats results as available only
after seven complete KST calendar days have elapsed after the fixture day.
"""

import hashlib
import json
import math
import subprocess
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from datetime import date, timedelta
from pathlib import Path


REVISION = "12729dc3dcbcdcce25a41fb973efac895a6553da"
ARCHIVE_PATH = "experiments/expanded-market-baselines-20261105/primary.jsonl"
ARCHIVE_SHA256 = "6aa3319c0195cc123d2c1c8fe85eaf297f8c0daeaacbf982e40ca51d4ea56cc9"
FALLBACK_REF = "refs/remotes/origin/codex/expanded-multimarket-study-20261105"
PROVENANCE = f"betman-side:{REVISION}:{ARCHIVE_PATH}"

CANDIDATE = "generic_score_distribution_lag7"
HISTORY_GAP = timedelta(days=8)
MIN_HISTORY = 20
MARKET_PRIOR = 50


def _read_archive(repo: Path) -> bytes:
    for ref in (REVISION, FALLBACK_REF):
        result = subprocess.run(
            ["git", "cat-file", "blob", f"{ref}:{ARCHIVE_PATH}"],
            cwd=repo,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            if hashlib.sha256(result.stdout).hexdigest() != ARCHIVE_SHA256:
                raise ValueError(f"Unexpected Betman archive contents at {ref}:{ARCHIVE_PATH}")
            return result.stdout
    raise RuntimeError(f"Betman archive unavailable in local Git: {REVISION}:{ARCHIVE_PATH}")


def _outcome_codes(record: dict) -> tuple[int, ...]:
    # Official slots are home/draw/away or under/unused/over.
    return (0, 2) if record["prices"][1] == 0 else (0, 1, 2)


def _score_statistic(record: dict) -> int:
    home, away = record["score"]
    return home + away if record["market"] == "total" else home - away


def _score_grade(statistic: int, line: float, market: str) -> int:
    if market == "total":
        return 0 if statistic < line else 2
    margin = statistic + line
    return 0 if margin > 0 else 2 if margin < 0 else 1


def _candidate(
    history: Counter[int],
    market: str,
    line: float,
    codes: tuple[int, ...],
    baseline: list[float],
) -> list[float] | None:
    count = history.total()
    if count < MIN_HISTORY:
        return None
    grades = Counter()
    for statistic, frequency in history.items():
        grades[_score_grade(statistic, line, market)] += frequency
    return [
        (grades[code] + MARKET_PRIOR * probability) / (count + MARKET_PRIOR)
        for code, probability in zip(codes, baseline)
    ]


def _cohort(record: dict) -> tuple:
    return (
        record["sport"], record["leagueCode"], record["market"], record["period"],
        _outcome_codes(record),
    )


def _replay(records: Iterable[dict]) -> Iterator[dict]:
    entries = [
        (date.fromisoformat(record["kickoff"][:10]), record)
        for record in records
        if not (
            record["sport"] == "BK"
            and (record["leagueCode"] == "BK001" or record["league"].strip().upper() == "KBL")
        )
    ]
    entries.sort(key=lambda entry: (entry[1]["kickoff"], entry[1]["round"], entry[1]["matchSeq"]))
    histories = defaultdict(Counter)
    next_history = 0

    for day, record in entries:
        cutoff = day - HISTORY_GAP
        while next_history < len(entries) and entries[next_history][0] <= cutoff:
            _, past = entries[next_history]
            histories[_cohort(past)][_score_statistic(past)] += 1
            next_history += 1

        market, line = record["market"], record["line"]
        codes = _outcome_codes(record)
        odds = [record["prices"][code] for code in codes]
        inverse_odds = [1 / price for price in odds]
        total_weight = math.fsum(inverse_odds)
        baseline = [weight / total_weight for weight in inverse_odds]
        prob = {"market": baseline}
        # Regrade only past scores at today's line. Shrink toward today's market
        # with 50 prior observations and require 20 past fixtures in the cohort.
        candidate = _candidate(histories[_cohort(record)], market, line, codes, baseline)
        if candidate is not None:
            prob[CANDIDATE] = candidate

        yield {
            "id": f"betman:{record['round']}:{record['matchSeq']}",
            "day": day.isoformat(),
            "sport": record["sport"].lower(),
            "league": record["league"],
            "market": market,
            "period": record["period"],
            "odds": odds,
            "y": codes.index(record["result"]),
            "prob": prob,
            "line": line,
            "provenance": PROVENANCE,
        }


def rows(repo: Path) -> Iterator[dict]:
    """Yield audited products in KST kickoff order, excluding KBL."""
    return _replay(json.loads(line) for line in _read_archive(repo).splitlines())
