"""Inference with the pinned KBL handicap candidate and #693 full-ledger fits."""

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from math import erf, exp, isfinite, log, sqrt
from pathlib import Path


SEED_PATH = Path(__file__).with_name("seed.json")
SEED_SHA256 = "439e3c465f394332fa847397e8fc90ed7820f0ec3622c000b78d759f94485a48"
FROZEN_PATH = Path(__file__).resolve().parents[2] / "data/model_candidates/handicap-v1.json"
KST = timezone(timedelta(hours=9))


def _verified_json(path, expected_sha):
    try:
        # Match the league artifact convention across Git line endings.
        raw = path.read_bytes().replace(b"\r\n", b"\n")
    except OSError as exc:
        raise ValueError(f"unavailable model artifact: {path.name}") from exc
    if sha256(raw).hexdigest() != expected_sha:
        raise ValueError(f"model artifact SHA-256 mismatch: {path.name}")
    return json.loads(raw)


def _kickoff_day(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("kickoff must be a timezone-aware timestamp")
    try:
        return value.astimezone(KST).date()
    except OverflowError as exc:
        raise ValueError("kickoff is outside the supported KST date range") from exc


def _number(value, name):
    if type(value) in (int, float):
        try:
            if isfinite(value):
                return float(value)
        except OverflowError:
            pass
    raise ValueError(f"{name} must be a finite number")


def _rest_difference(home, away):
    for value in (home, away):
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("rest days must be nonnegative integers or null")
    if home is None or away is None:
        return 0.0
    return (min(home, 5) - min(away, 5)) / 5


def _pick(p_home, odds, artifact_sha):
    home = p_home >= 0.5
    return {
        "home_probability": p_home,
        "selection": "home" if home else "away",
        "odds": odds[0] if home else odds[1],
        "artifact_sha256": artifact_sha,
    }


def forecasts(game: Mapping, quote: Mapping) -> dict:
    """Forecast one caller-supplied pregame, full-game half-point KBL offer.

    The line is applied to the home score and the odds are ordered home, away.
    Official team IDs key the frozen coefficients. Recognized names must agree
    with those IDs. Invalid inputs or unavailable models raise ValueError.
    """
    if not isinstance(game, Mapping) or not isinstance(quote, Mapping):
        raise ValueError("game and quote must be mappings")
    for record, fields in (
        (game, ("id", "home_id", "away_id", "home_name", "away_name")),
        (quote, ("offer_id",)),
    ):
        for key in fields:
            value = record.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a nonempty string")
    phase = game.get("phase")
    if phase not in ("regular", "playoff"):
        raise ValueError("phase must be regular or playoff")
    if "home_rest" not in game or "away_rest" not in game:
        raise ValueError("both rest fields are required")
    rest = _rest_difference(game["home_rest"], game["away_rest"])
    day = _kickoff_day(game.get("kickoff"))
    line = _number(quote.get("line"), "line")
    if line % 1 != 0.5:
        raise ValueError("line must be a half-point handicap")
    prices = quote.get("odds")
    if not isinstance(prices, (list, tuple)) or len(prices) != 2:
        raise ValueError("odds must contain two prices in home, away order")
    odds = tuple(_number(price, "odds") for price in prices)
    if min(odds) <= 1:
        raise ValueError("decimal odds must exceed 1")

    seed = _verified_json(SEED_PATH, SEED_SHA256)
    frozen_sha = seed["frozen"]["artifact_sha256"]
    frozen = _verified_json(FROZEN_PATH, frozen_sha)["KBL"]
    if frozen["train_through"] != seed["frozen"]["train_through"]:
        raise ValueError("frozen KBL training cutoff mismatch")
    cutoff = max(date.fromisoformat(seed["train_through"]), date.fromisoformat(frozen["train_through"]))
    if day <= cutoff:
        raise ValueError("kickoff KST date must be after the model training cutoffs")

    home, away = game["home_id"], game["away_id"]
    residual = frozen["team_residual"]
    if home == away or home not in residual or away not in residual:
        raise ValueError("distinct official KBL team IDs in the frozen model are required")
    for side, team_id in (("home", home), ("away", away)):
        alias = "".join(game[f"{side}_name"].split())
        if frozen["aliases"].get(alias, team_id) != team_id:
            raise ValueError(f"{side} KBL name conflicts with the official team ID")

    # The KBL branch of league_handicap.recommend, keyed by official team ID.
    margin = frozen["mean_margin"] + (residual[home] - residual[away]) / 2
    z = (-line - margin) / (frozen["sd_margin"] * sqrt(2))
    result = {"frozen": _pick(1 - 0.5 * (1 + erf(z)), odds, frozen_sha)}

    # logit(away / (home + away)), using the equivalent odds ratio.
    offset = log(odds[1] / odds[0])
    features = {"intercept": 1.0, "phase": float(phase == "playoff"), "rest": rest}
    for name in ("static", "phase_rest"):
        coefficients = seed["coefficients"][name]
        eta = offset + sum(features[key] * coefficient for key, coefficient in coefficients.items())
        tail = exp(-abs(eta))
        p_home = 1 / (1 + tail) if eta >= 0 else tail / (1 + tail)
        result[name] = _pick(p_home, odds, SEED_SHA256)
    return result
