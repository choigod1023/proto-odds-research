"""Read the settled domestic primary archive in source-date order.

The archived models freeze each month's fits at month start minus four days
and embargo results for four days in their rolling features.
Cold-start forecasts are the archived market fallback. The date lag is an
availability assumption. The archive does not certify price or result receipt.
"""

from collections import Counter
from collections.abc import Iterator
from datetime import date, timedelta
import gzip
import hashlib
import heapq
import json
import math
from operator import itemgetter
from pathlib import Path


SOURCE = Path("experiments/all-sports-underdog-20261031/results")
MARKETS = {"sc": ("1x2", 3), "bs": ("win_loss", 2), "bk": ("win_loss", 2), "vl": ("win_loss", 2)}
FITTED_MODELS = ("outcome_form", "market_residual")
MODELS = ("market", *FITTED_MODELS, "half_market_form")
COUNTS = (
    "source", "included", "market_fallback", "kbl_excluded", "void_push_or_unlabeled",
    "invalid_odds", "invalid_probability", "unverified_model_timing",
)


def _verified_states(path: Path) -> dict:
    verified = {}
    seen = set()
    for state in json.loads(path.read_text(encoding="utf-8")):
        month = state["month"]
        if month in seen:
            raise ValueError(f"Duplicate monthly state: {path}:{month}")
        seen.add(month)
        cutoff = date.fromisoformat(f"{month}-01") - timedelta(days=4)
        models = state["states"]
        digest = hashlib.sha256(json.dumps(models, sort_keys=True).encode()).hexdigest()
        fallback = state["fallback"]
        trained = (set(models) == set(FITTED_MODELS)
                   and models["outcome_form"]["offset"] is False
                   and models["market_residual"]["offset"] is True
                   and state["training_rows"] >= 200
                   and all(model["training_rows"] == state["training_rows"] for model in models.values()))
        if (state["training_cutoff_inclusive"] == cutoff.isoformat()
                and state["state_sha256"] == digest
                and (fallback is True and not models or fallback is False and trained)):
            verified[month] = (digest, fallback)
    return verified


def _probability(values, size: int) -> bool:
    return (isinstance(values, list) and len(values) == size
            and all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in values)
            and math.isclose(sum(values), 1, rel_tol=0, abs_tol=1e-12))


def _sport_rows(repo: Path, sport: str, counts: Counter) -> Iterator[dict]:
    folder = SOURCE / sport / "domestic_archive"
    states = _verified_states(repo / folder / "states.json")
    ledger = folder / "ledger.jsonl.gz"
    provenance = f"{ledger.as_posix()}:frozen_monthly"
    market, size = MARKETS[sport]
    with gzip.open(repo / ledger, "rt", encoding="utf-8") as stream:
        for raw in map(json.loads, stream):
            counts["source"] += 1
            ident, day, league = raw["id"], raw["day"], raw["league"]
            if (raw["sport"] != sport or raw["cohort"] != "domestic_archive"
                    or date.fromisoformat(day).isoformat() != day):
                raise ValueError(f"Unexpected primary archive identity or date: {ledger}:{ident}")
            if sport == "bk" and league == "KBL":
                counts["kbl_excluded"] += 1
                continue
            y = raw.get("y")
            if raw.get("is_void") or raw.get("is_push") or type(y) is not int or not 0 <= y < size:
                counts["void_push_or_unlabeled"] += 1
                continue
            odds = raw.get("odds")
            if (not isinstance(odds, list) or len(odds) != size
                    or not all(type(o) in (int, float) and math.isfinite(o) and o > 1 for o in odds)):
                counts["invalid_odds"] += 1
                continue
            fallback = raw.get("fallback")
            if (type(fallback) is not bool
                    or states.get(day[:7]) != (raw.get("month_state_sha256"), fallback)):
                counts["unverified_model_timing"] += 1
                continue
            predictions = raw["predictions"]
            market_p = raw["market_p"]
            prob = {name: predictions.get(name, {}).get("p") for name in MODELS}
            reciprocal = [1 / o for o in odds]
            total = sum(reciprocal)
            if (not all(_probability(vector, size) for vector in prob.values())
                    or market_p != prob["market"]
                    or any(abs(p - inv / total) > 1e-12 for p, inv in zip(market_p, reciprocal))
                    or prob["half_market_form"] != [(p + form) / 2 for p, form in zip(market_p, prob["outcome_form"])]
                    or fallback and any(prob[name] != market_p for name in FITTED_MODELS)):
                counts["invalid_probability"] += 1
                continue
            counts["included"] += 1
            counts["market_fallback"] += int(fallback)
            yield {
                "id": ident, "day": day, "sport": sport, "league": league,
                "market": market, "period": "full_game", "line": None,
                "odds": odds, "y": y, "prob": prob,
                "provenance": f"{provenance}:market_fallback" if fallback else provenance,
            }


def rows(repo: Path) -> Iterator[dict]:
    """Yield saved vectors in home/draw/away (sc) or home/away order."""
    return heapq.merge(*(_sport_rows(repo, sport, Counter()) for sport in MARKETS), key=itemgetter("day"))


def audit_counts(repo: Path) -> dict[str, dict[str, int]]:
    """Count each exclusion at this adapter, after upstream archive filtering."""
    audit = {}
    for sport in MARKETS:
        counts = Counter(dict.fromkeys(COUNTS, 0))
        for _ in _sport_rows(repo, sport, counts):
            pass
        audit[sport] = dict(counts)
    return audit


def audit_metadata(repo: Path) -> dict[str, dict[str, dict[str, str]]]:
    """Return paths and SHA-256 digests of the exact ledger and state file bytes read."""
    metadata = {}
    for sport in MARKETS:
        folder = SOURCE / sport / "domestic_archive"
        metadata[sport] = {}
        for name in ("ledger.jsonl.gz", "states.json"):
            path = folder / name
            with (repo / path).open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            metadata[sport][name] = {"path": path.as_posix(), "sha256": digest}
    return metadata
