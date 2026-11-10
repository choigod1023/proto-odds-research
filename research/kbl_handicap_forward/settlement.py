"""Grade frozen KBL half-point handicap predictions from official results."""

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import math
import re

from research.kbo_forward.prices import _millis, _moment

from . import sources


MODELS = ("frozen", "static", "phase_rest")
RESULT_CODES = {"home": "0", "away": "2"}


def _price(value):
    return type(value) in (int, float) and 1 < value < 1000


def _verified_offer(body, receipt, offer_id, kickoff):
    match = re.fullmatch(r"G101:([0-9]{6}):([1-9][0-9]*)", offer_id)
    if match is None or not isinstance(body, bytes):
        return None
    round_id, seq = match.groups()
    if (receipt["sha256"] != sha256(body).hexdigest()
            or _moment(receipt["received_at"]) <= kickoff
            or receipt["url"] != sources.DETAIL_URL
            or type(receipt["status"]) is not int or receipt["status"] != 200):
        return None
    request = receipt.get("payload")
    if request is not None and (request["gmId"], str(request["gmTs"])) != ("G101", round_id):
        return None
    _, rows = sources.detail(body, round_id)
    number = int(seq)
    matches = [row for row in rows if row.get("matchSeq") in (seq, number)]
    if len(matches) != 1 or type(matches[0]["matchSeq"]) is not int:
        return None
    return matches[0]


def _explicit_void(row):
    # Archived G101 KBL refunds clear the selection labels, odds and score.
    return (
        row["gameResult"] == "4"
        and all(row[k] is None for k in ("winTxt", "drawTxt", "loseTxt", "mchScore"))
        and all(type(row[k]) in (int, float) and row[k] == 0 for k in ("winAllot", "drawAllot", "loseAllot"))
    )


def _winning_side(adjusted_home, away):
    return "home" if adjusted_home > away else "away"


def settle(prediction, game, detail_body, receipt):
    """Return a JSON-ready grade, or None if official support is incomplete.

    detail_body is the original Betman response bytes. The capture receipt
    supplies their sha256, an offset-aware received_at timestamp, and the
    official detail URL and HTTP 200 status.
    """
    try:
        frozen_game = prediction["game"]
        kickoff = _moment(frozen_game["kickoff"])
        for field in ("id", "home_id", "away_id"):
            value = frozen_game[field]
            if not isinstance(value, str) or not value.strip() or value != game[field]:
                return None
        if kickoff != _moment(game["kickoff"]) or game["home_id"] == game["away_id"]:
            return None

        quote = prediction["quote"]
        line, odds = quote["line"], quote["odds"]
        if (quote["period"] != "full_including_extra" or quote["settlement_rule"] != "handicap_half"
                or type(line) not in (int, float) or not math.isfinite(line) or line % 1 != 0.5
                or not isinstance(odds, (list, tuple)) or len(odds) != 2
                or not all(_price(price) for price in odds)):
            return None
        models = prediction["models"]
        if set(models) != set(MODELS):
            return None
        picks = {}
        for name in MODELS:
            side, price = models[name]["selection"], models[name]["odds"]
            if side not in RESULT_CODES or not _price(price) or price != odds[side == "away"]:
                return None
            picks[name] = {"selection": side, "odds": price}

        row = _verified_offer(detail_body, receipt, quote["offer_id"], kickoff)
        if (row is None or tuple(row.get(k) for k in sources.PRODUCT_KEYS) != sources.PRODUCT
                or row["protoStatus"] != "4" or row.get("live", "N") != "N"
                or row.get("gameSubject") not in (None, "")
                or (row["homeId"], row["awayId"]) != (game["home_id"], game["away_id"])
                or _millis(row["gameDate"]) != kickoff):
            return None

        state, score = game["state"], game["score"]
        if state == "final":
            if (not isinstance(score, (list, tuple)) or len(score) != 2
                    or not all(type(points) is int and points >= 0 for points in score)
                    or score[0] == score[1]):
                return None
            score = list(score)
        elif state not in ("scheduled", "other") or score is not None:
            return None

        official_line = winner = None
        if _explicit_void(row):
            status = "void"
        else:
            official_line = sources.terms(row)
            if state != "final" or row["gameReject"] != "0" or official_line is None:
                return None
            home, away = score
            adjusted = Decimal(home) + Decimal(str(official_line))
            official_score = tuple(Decimal(part) for part in row["mchScore"].split(":"))
            if (official_score != (adjusted, Decimal(away))
                    or row["gameResult"] != RESULT_CODES[_winning_side(adjusted, away)]):
                return None
            # Betman applies the line in effect when the quote was captured.
            winner = _winning_side(Decimal(home) + Decimal(str(line)), away)
            status = "settled"

        for pick in picks.values():
            hit = pick["selection"] == winner if winner is not None else None
            pick["hit"] = hit
            if hit is None:
                pick["net"] = 0.0
            elif hit:
                pick["net"] = float(Decimal(str(pick["odds"])) - 1)
            else:
                pick["net"] = -1.0
        return {
            "status": status, "game_id": game["id"], "offer_id": quote["offer_id"],
            "score": score, "line": line, "official_line": official_line,
            "official_result_code": row["gameResult"], "official_adjusted_score": row["mchScore"],
            "winner": winner, "models": picks, "official_betman_receipt": dict(receipt),
        }
    except (AttributeError, InvalidOperation, KeyError, OSError, OverflowError, TypeError, ValueError):
        return None
