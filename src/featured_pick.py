"""The one published pregame pick across existing operating selections."""
from collections import Counter, defaultdict
from decimal import Decimal
import math

from ai_decision import event_id
from bets import SEL_NAMES

VERSION = "featured-pregame-v1"
WINNER_MARKETS = {"승패", "승무패"}
LINE_MARKETS = {"핸디캡", "언더오버"}
FULL_GAME = {"full", "full_game"}
PREFERRED_ODDS = 1.50


def choose(game, snapshot):
    """Use the same price preference and normalized market basis as the card."""
    options = game.get("options") or []
    ids = Counter(row.get("selection_id") for row in options)
    groups = defaultdict(list)
    for row in options:
        number = str(row.get("게임번호") or "")
        if number.isascii() and number.isdigit():
            groups[number].append(row)
    markets = game.get("market_recommendations") or {}
    items = {(row.get("selection_id"), row.get("offer_id")): row
             for row in markets.get("items", [])
             if row.get("automatic_recommendation") is True and row.get("candidate_only") is not True
             } if markets.get("version") == "multi-market-recommendations-v1" else {}
    ranked = []
    for number, rows in groups.items():
        first = rows[0]
        market, label = first.get("market"), first.get("label") or ""
        period, scope = first.get("period") or "", first.get("settlement_scope") or ""
        line, ways = first.get("line"), first.get("n_way")
        if market not in WINNER_MARKETS | LINE_MARKETS:
            continue
        if period not in FULL_GAME | {""} or (market in LINE_MARKETS and (not period or not scope)):
            continue
        names = SEL_NAMES.get((market, ways))
        if not names or len(rows) != len(names) or {row.get("선택") for row in rows} != set(names):
            continue
        if any((row.get("market"), row.get("label") or "", row.get("period") or "",
                row.get("settlement_scope") or "", row.get("line"), row.get("n_way"))
               != (market, label, period, scope, line, ways) for row in rows):
            continue
        prices = {row["선택"]: row.get("배당") for row in rows}
        if any(type(price) not in (int, float) or not math.isfinite(price) or price <= 1
               for price in prices.values()):
            continue
        inverse_sum = sum(1 / prices[name] for name in names)
        for row in rows:
            selection_id, offer_id = row.get("selection_id"), row.get("offer_id")
            if not selection_id or not offer_id or ids[selection_id] != 1:
                continue
            selection, price = row["선택"], row["배당"]
            if market in WINNER_MARKETS:
                if label or snapshot.get("action") != "market_reference" or (
                    selection_id, offer_id
                ) != (snapshot.get("selection_id"), snapshot.get("offer_id")):
                    continue
                rule = "official_winner"
            else:
                item = items.get((selection_id, offer_id)) or {}
                if (item.get("market"), item.get("label") or "", item.get("line"),
                    item.get("selection"), str(item.get("game_no")), item.get("odds")) != (
                    market, label, line, selection, number, price
                ) or type(line) not in (int, float) or not math.isfinite(line) or not item.get("settlement_rule"):
                    continue
                rule = item["settlement_rule"]
            probability = 1 / (price * inverse_sum)
            ranked.append(dict(version=VERSION, selection_id=selection_id, offer_id=offer_id,
                               market=market, label=label, line=line, selection=selection,
                               game_no=number, odds=price, settlement_rule=rule,
                               probability=probability, estimated_roi=probability * price - 1))
    return min(ranked, key=lambda pick: (pick["odds"] < PREFERRED_ODDS, -pick["probability"],
                                       -pick["estimated_roi"], pick["selection_id"]), default=None)


def attach(document, records):
    summary = performance(records)
    changed = document.get("featured_pick_performance") != summary
    document["featured_pick_performance"] = summary
    for section in ("live", "past"):
        for game in document.get(section) or []:
            record = records.get(game.get("event_id") or event_id(game))
            if game.get("featured_pick_record") == record:
                continue
            changed = True
            if record:
                game["featured_pick_record"] = record
            else:
                game.pop("featured_pick_record", None)
    return changed


def _performance(records):
    """One unit per settled fixture, refunds excluded from hit rate only."""
    grades = [record["result"] for record in records.values() if record["result"]]
    hits = sum(grade["result"] == "hit" for grade in grades)
    refunds = sum(grade["result"] == "void" for grade in grades)
    decided = len(grades) - refunds
    net = sum((Decimal(grade["net_units"]) for grade in grades), Decimal(0))
    return dict(captured=len(records), settled=len(grades), pending=len(records) - len(grades),
                hits=hits, decided=decided, refunds=refunds, net_units=str(net),
                hit_rate=hits / decided if decided else None,
                roi=float(net / len(grades)) if grades else None)


def performance(records):
    """Retain the same ledger cohort while exposing its sport and market mix."""
    result = _performance(records)
    for field, name in (("sport", "by_sport"), ("league", "by_league"),
                        ("market", "by_market"), ("selection_source", "by_selection_source"),
                        ("selection_policy", "by_policy")):
        groups = defaultdict(dict)
        for event, record in records.items():
            if field in {"sport", "league"}:
                key = (record.get("fixture") or {}).get(field)
            elif field == "market":
                key = (record.get("pick") or {}).get(field)
            else:
                key = record.get(field)
            groups[str(key or "unknown")][event] = record
        result[name] = {key: _performance(group) for key, group in sorted(groups.items())}
    result["selection_rule"] = {
        "version": VERSION, "preferred_odds": PREFERRED_ODDS,
        "probability_basis": "normalized_inverse_market",
        "validated_edge": False,
    }
    return result
