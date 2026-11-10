"""KBL schedule and official Betman half-point handicap snapshots."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math

from research.kbo_forward.prices import DETAIL_URL, LIST_URL, REFERER, _millis, _moment

KST = timezone(timedelta(hours=9))
KBL_URL = "https://api.kbl.or.kr/match/list"
ROUND = {"_sbmInfo": {"debugMode": "false"}}
KBL_HEADERS = {"User-Agent": "Mozilla/5.0", "Channel": "WEB", "TeamCode": "XX", "lang": "ko",
               "X-Requested-With": "XMLHttpRequest"}
BETMAN_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": REFERER, "Accept": "application/json",
                  "X-Requested-With": "XMLHttpRequest"}
PHASES = {"01": ("R", "정규시즌", "regular"), "03": ("PO", "플레이오프", "playoff"),
          "04": ("CP", "챔피언결정전", "playoff")}
PRODUCT = ("BK001", 23, "6", "5", "농구 핸디캡", "일반 소수핸디캡")
PRODUCT_KEYS = ("leagueCode", "handi", "betId", "betTypId", "betNm", "betTypNm")


def schedule(raw):
    doc = json.loads(raw)
    if not isinstance(doc, list):
        raise ValueError("Invalid official KBL schedule")
    by_id = {}
    for row in doc:
        if row.get("seasonGrade") != 1 or row.get("gameCode") not in PHASES:
            continue
        code = row["gameCode"]
        category, category_name, phase = PHASES[code]
        season, ident = row["seasonCode"], row["gmkey"]
        if (row["seasonCategory"] != category or row["seasonCategoryName"] != category_name
                or type(season) is not int or type(row["gameNo"]) is not int
                or ident != f"S{season}G{code}N{row['gameNo']}"
                or row["glkey"] != f"S{season}G{code}" or ident in by_id):
            raise ValueError("KBL phase or fixture identity mismatch")
        kickoff = datetime.strptime(str(row["gameDate"]) + str(row["gameStart"]).zfill(4), "%Y%m%d%H%M").replace(tzinfo=KST)
        home, away = row["tcodeH"], row["tcodeA"]
        if not isinstance(home, str) or not isinstance(away, str) or not home or not away or home == away:
            raise ValueError("Missing official KBL team identity")
        score = [row.get("scoreH"), row.get("scoreA")]
        final = row["isEnded"] == 1 and all(type(x) is int and x >= 0 for x in score) and score[0] != score[1]
        scheduled = row["isEnded"] == 0 and score == [None, None]
        by_id[ident] = {
            "id": ident, "kickoff": kickoff.isoformat(), "season": season, "phase": phase,
            "home_id": home, "away_id": away, "home_name": row["tnameH"], "away_name": row["tnameA"],
            "score": score if final else None, "state": "final" if final else "scheduled" if scheduled else "other",
        }
    prior = {}
    seen_days = set()
    for game in sorted(by_id.values(), key=lambda r: r["kickoff"]):
        day = _moment(game["kickoff"]).astimezone(KST).date()
        for side in ("home", "away"):
            key = (game["season"], game[side + "_id"])
            if (key, day) in seen_days:
                raise ValueError("Ambiguous KBL team schedule on the same date")
            seen_days.add((key, day))
            last = prior.get(key)
            game[side + "_rest"] = (day - last[0]).days - 1 if last else None
            game[side + "_prior_id"] = last[1] if last else None
            if game["state"] == "final":
                prior[key] = day, game["id"]
    return by_id


def rounds(raw, at):
    all_rounds = json.loads(raw)["protoGames"]
    live = {}
    for row in all_rounds:
        if row.get("gmId") != "G101":
            continue
        number = str(row["gmTs"])
        if (len(number) != 6 or not number.isdigit()
                or row["gmOsidTsYear"] != 2000 + int(number[:2])):
            raise ValueError("Invalid G101 round identity")
        if at < _millis(row["saleEndDate"]):
            if number in live:
                raise ValueError("Duplicate G101 round")
            live[number] = row
    return live


def detail(raw, number):
    doc = json.loads(raw)
    lottery = doc["currentLottery"]
    if str(doc["gmTs"]) != number or str(lottery["gmTs"]) != number or lottery["gmId"] != "G101":
        raise ValueError("Betman detail round identity mismatch")
    data = doc["compSchedules"]
    if len(set(data["keys"])) != len(data["keys"]):
        raise ValueError("Duplicate Betman detail fields")
    return lottery, [dict(zip(data["keys"], row, strict=True)) for row in data["datas"]]


def terms(row):
    if tuple(row.get(k) for k in PRODUCT_KEYS) != PRODUCT:
        return None
    line = row["winHandi"]
    if (type(line) not in (int, float) or not math.isfinite(line) or line % 1 != 0.5
            or row["loseHandi"] != -line or row["drawHandi"] != 0 or row["drawAllot"] != 0
            or row["gameSubject"] not in (None, "") or (row["winTxt"], row["drawTxt"], row["loseTxt"]) != ("승", "-", "패")):
        return None
    return line


def quotes(raw, number, listing_round, games, receipt):
    lottery, offers = detail(raw, number)
    at = _moment(receipt["received_at"])
    round_end = _millis(lottery["saleEndDate"])
    if (lottery["saleProgress"] is not True or lottery["saleStatus"] != "SaleProgress"
            or round_end != _millis(listing_round["saleEndDate"])
            or not _millis(lottery["saleStartDate"]) <= at < round_end):
        return []
    identities = Counter(row["matchSeq"] for row in offers)
    upcoming = {(g["home_id"], g["away_id"], _moment(g["kickoff"])): g
                for g in games if g["state"] == "scheduled"}
    output = []
    for row in offers:
        if tuple(row.get(k) for k in PRODUCT_KEYS) != PRODUCT:
            continue
        # Placeholder rows may have no line or price until sales open.
        if (row["protoStatus"] != "2" or row["gameReject"] != "0" or row["buyReject"] != "0"
                or any(row[k] != "N" for k in ("live", "unsetSchedule", "unsetEndDate"))):
            continue
        line = terms(row)
        start, close = _millis(row["gameDate"]), _millis(row["endDate"])
        g = upcoming.get((row["homeId"], row["awayId"], start))
        odds = [row["winAllot"], row["loseAllot"]]
        seq = row["matchSeq"]
        if (g is None or line is None or type(seq) is not int or seq <= 0 or identities[seq] != 1
                or not start - timedelta(hours=24) <= at < min(close, start - timedelta(minutes=30))
                or close > start or any(type(v) not in (int, float) or not math.isfinite(v) or not 1 < v < 1000 for v in odds)):
            continue
        output.append((g, {"offer_id": f"G101:{number}:{seq}", "line": line, "odds": odds,
                           "close_at": close.isoformat(), "round_close_at": round_end.isoformat(),
                           "period": "full_including_extra", "settlement_rule": "handicap_half",
                           "received_at": receipt["received_at"], "receipt": receipt,
                           "sale_status": "confirmed_open", "execution_price_verified": False}))
    return output
