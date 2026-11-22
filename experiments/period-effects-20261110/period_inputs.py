"""Read the fixed #691 evaluation ledger and its matching primary history."""

from collections.abc import Iterator
from datetime import date
import gzip
import hashlib
import json
import math
from pathlib import Path
import subprocess


ROUTING_COMMIT = "12f3427f76bad843b249e8c3393c450897bf47d5"
ROUTING_PATH = Path("experiments/market-model-routing-20261110/ledger.jsonl.gz")
ROUTING_SHA256 = "cc7b81394b43efef5f78bd5b0931f6eb99cc3609f10afda0aae8287679d27e0c"
# The uncompressed hash and primary hashes are recorded in the pinned summary.json.
ROUTING_BODY_SHA256 = "4f38534246be58428a5c65b451a210e3e2f3ec5dde9faf01109ce1b3ff9fdebe"
ROUTING_COUNT = 33_086
PRIMARY_ROOT = Path("experiments/all-sports-underdog-20261031/results")
PRIMARY_SHA256 = {
    "sc": "4eb3d4ab6b61bafd7319bb456d69a7dd3135d4deae9e813c24b64a1ca68c72cb",
    "bs": "311e298b4b7edf9cc8507c7c493966075c694cc9eed15896eb4a390e3c49649f",
    "bk": "b8eb5c8ffa2906d2f1b2daa003d1656c9dd7009c58fd9c0eca655d40631cd37b",
    "vl": "07fd92b2d937806db16d9f185ecece71efeed53cc9f63bb122d315dc6fbb6976",
}
HISTORY_START, EVALUATION_START, EVALUATION_END = "2023-01-01", "2025-01-01", "2026-12-31"


def _primary_path(sport: str) -> Path:
    return PRIMARY_ROOT / sport / "domestic_archive/ledger.jsonl.gz"


def _verify_sha(body: bytes, expected: str, source: str) -> None:
    actual = hashlib.sha256(body).hexdigest()
    if actual != expected:
        raise ValueError(f"SHA-256 mismatch for {source}: expected {expected}, got {actual}")


def _routing_body(repo: Path) -> bytes:
    spec = f"{ROUTING_COMMIT}:{ROUTING_PATH.as_posix()}"
    try:
        blob = subprocess.check_output(
            ["git", "cat-file", "blob", spec], cwd=repo, stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"Cannot read pinned Git artifact {spec} from {repo}") from exc
    _verify_sha(blob, ROUTING_SHA256, spec)
    body = gzip.decompress(blob)
    _verify_sha(body, ROUTING_BODY_SHA256, f"{spec} (uncompressed)")
    return body


def _primary_blob(repo: Path, sport: str) -> bytes:
    path = repo / _primary_path(sport)
    blob = path.read_bytes()
    _verify_sha(blob, PRIMARY_SHA256[sport], str(path))
    return blob


def _date(raw: dict) -> str:
    day = raw["day"]
    if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError(f"Invalid archive date for {raw['id']}: {day!r}")
    return day


def _finite(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _group(row: dict) -> tuple:
    return row["sport"], row["league"], row["market"], row["period"], len(row["odds"])


def _offer(raw: dict, market: str, period: str, line, base: list, source: str) -> dict:
    ident, sport, league = raw["id"], raw["sport"], raw["league"]
    if sport not in PRIMARY_SHA256 or not all(
        isinstance(s, str) and s.strip() for s in (ident, league, period, source)
    ):
        raise ValueError(f"Invalid archive identity or group: {ident}")

    sizes = {"1x2": 3, "win_loss": 2, "handicap": 3 if sport == "sc" else 2, "total": 2}
    size = sizes.get(market)
    if (size is None or (market == "1x2" and sport != "sc")
            or (market == "win_loss" and sport == "sc")):
        raise ValueError(f"Invalid archive market: {ident}")
    if (market in ("handicap", "total") and not _finite(line)
            or market in ("1x2", "win_loss") and line is not None):
        raise ValueError(f"Invalid archive line: {ident}")

    odds, y = raw["odds"], raw["y"]
    if (not isinstance(odds, list) or len(odds) != size
            or not all(_finite(o) and o > 1 for o in odds)):
        raise ValueError(f"Invalid archive odds: {ident}")
    if type(y) is not int or not 0 <= y < size or raw.get("is_void") or raw.get("is_push"):
        raise ValueError(f"Invalid archive settlement: {ident}")
    if (not isinstance(base, list) or len(base) != size
            or not all(_finite(p) and 0 <= p <= 1 for p in base)
            or not math.isclose(math.fsum(base), 1, rel_tol=0, abs_tol=1e-6)):
        raise ValueError(f"Invalid reference probabilities: {ident}")

    return {
        "id": ident, "day": raw["day"], "sport": sport, "league": league,
        "market": market, "period": period, "line": line, "odds": odds,
        "y": y, "base": base, "source": source,
    }


def _check_market(odds: list, probabilities: list, ident: str) -> None:
    weights = [1 / odd for odd in odds]
    total = math.fsum(weights)
    if (not isinstance(probabilities, list) or len(probabilities) != len(odds)
            or not all(_finite(p) and math.isclose(p, w / total, rel_tol=0, abs_tol=1e-12)
                       for p, w in zip(probabilities, weights))):
        raise ValueError(f"Market probabilities do not match odds: {ident}")


def _check_pick(row: dict, probabilities: list, prediction: dict) -> None:
    # Both archives break argmax ties by the first position in the saved vector.
    pick = max(range(len(probabilities)), key=probabilities.__getitem__)
    if type(prediction["pick"]) is not int or prediction["pick"] != pick:
        raise ValueError(f"Invalid archive pick: {row['id']}")
    hit = pick == row["y"]
    net = row["odds"][pick] - 1 if hit else -1
    # The older primary archive stores unit net as a decimal string.
    if (type(prediction["hit"]) not in (bool, int) or prediction["hit"] != hit
            or not math.isclose(float(prediction["net"]), net, rel_tol=0, abs_tol=1e-12)):
        raise ValueError(f"Archive pick and settlement disagree: {row['id']}")


def _routing_rows(repo: Path) -> Iterator[dict]:
    lines = _routing_body(repo).splitlines()
    if len(lines) != ROUTING_COUNT:
        raise ValueError(f"Expected {ROUTING_COUNT} routing rows, got {len(lines)}")
    for raw in map(json.loads, lines):
        if not EVALUATION_START <= _date(raw) <= EVALUATION_END:
            raise ValueError(f"Routing date outside 2025–26: {raw['id']}")
        reference = raw["policies"]["reference"]
        market_policy = raw["policies"]["market"]
        if reference["model"] != raw["reference"] or market_policy["model"] != "market":
            raise ValueError(f"Unexpected routing reference or market model: {raw['id']}")
        row = _offer(raw, raw["market"], raw["period"], raw["line"],
                     reference["prob"], raw["provenance"])
        if raw["group"] != list(_group(row)):
            raise ValueError(f"Routing group disagrees with row: {row['id']}")
        market_p = market_policy["prob"]
        _check_market(row["odds"], market_p, row["id"])
        if raw["reference"] == "market" and row["base"] != market_p:
            raise ValueError(f"Routing market and reference probabilities disagree: {row['id']}")
        _check_pick(row, market_p, market_policy)
        _check_pick(row, row["base"], reference)
        yield row


def _primary_rows(repo: Path, sport: str, groups: set[tuple]) -> Iterator[dict]:
    body = gzip.decompress(_primary_blob(repo, sport))
    source = f"{_primary_path(sport).as_posix()}:market_p"
    market = "1x2" if sport == "sc" else "win_loss"
    for raw in map(json.loads, body.splitlines()):
        if not HISTORY_START <= _date(raw) < EVALUATION_START:
            continue
        if raw["sport"] != sport or raw["cohort"] != "domestic_archive":
            raise ValueError(f"Unexpected primary archive cohort: {raw['id']}")
        if sport == "bk" and raw["league"] == "KBL":
            continue
        row = _offer(raw, market, "full_game", None, raw["market_p"], source)
        if _group(row) not in groups:
            continue
        _check_market(row["odds"], row["base"], row["id"])
        prediction = raw["predictions"]["market"]
        if prediction["p"] != row["base"]:
            raise ValueError(f"Primary market predictions disagree with market_p: {row['id']}")
        _check_pick(row, row["base"], prediction)
        yield row


def rows(repo: Path) -> Iterator[dict]:
    """Yield offers by (day, id) in home/[draw]/away or under/over outcome order."""
    offers = list(_routing_rows(repo))
    groups = {_group(row) for row in offers}
    for sport in PRIMARY_SHA256:
        offers.extend(_primary_rows(repo, sport, groups))

    seen = set()
    for row in offers:
        ident = row["id"]
        if ident in seen:
            raise ValueError(f"Duplicate offer identity: {ident}")
        seen.add(ident)
    yield from sorted(offers, key=lambda row: (row["day"], row["id"]))


def manifest(repo: Path) -> dict:
    """Verify the input bytes and describe their provenance and coverage."""
    _routing_body(repo)
    sources = {
        ROUTING_PATH.as_posix(): {
            "git_commit": ROUTING_COMMIT,
            "sha256": ROUTING_SHA256,
            "sha256_uncompressed": ROUTING_BODY_SHA256,
            "rows": ROUTING_COUNT,
            "base": "policies.reference.prob",
        }
    }
    for sport, sha256 in PRIMARY_SHA256.items():
        _primary_blob(repo, sport)
        sources[_primary_path(sport).as_posix()] = {"sha256": sha256, "base": "market_p"}
    return {
        "sources": sources,
        "limitations": [
            "The 2025–26 slice is the settled #691 snapshot through 2026-11-02.",
            "2023–24 history is limited to exact sport/league/market/period/outcome-count "
            "matches for domestic primary 1x2 and win_loss (full_game). It excludes KBL "
            "and has no side-market history.",
            "Primary archive IDs identify normalized fixtures. Cross-market fixture overlap "
            "can exist even when offer IDs are unique.",
            "The adapter checks consistency with archived settlement fields. Independent "
            "settlement, historical receipt times, and executable prices are unverified.",
        ],
    }
