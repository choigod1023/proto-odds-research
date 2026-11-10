import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from research.kbl_handicap_forward import cycle, sources


TIP = datetime.fromisoformat("2026-11-10T19:00:00+09:00")
AT = TIP - timedelta(hours=2)
ID = "S49G01N63"


def fixture(number, date, home, away, *, final=False):
    return {"seasonGrade": 1, "gameCode": "01", "seasonCode": 49, "gameNo": number,
            "gmkey": f"S49G01N{number}", "glkey": "S49G01", "seasonCategory": "R",
            "seasonCategoryName": "정규시즌", "gameDate": date, "gameStart": "1900",
            "tcodeH": home, "tcodeA": away, "tnameH": home, "tnameA": away,
            "isEnded": int(final), "scoreH": 90 if final else None, "scoreA": 80 if final else None}


def official_game():
    fixtures = [fixture(59, "20261107", "66", "50", final=True),
                fixture(60, "20261108", "10", "16", final=True),
                fixture(63, "20261110", "10", "66")]
    return sources.schedule(json.dumps(fixtures))[ID]


def price(at=AT, **changes):
    g = official_game()
    ms = lambda x: int(x.timestamp() * 1000)
    row = dict(zip(sources.PRODUCT_KEYS, sources.PRODUCT, strict=True))
    row.update(matchSeq=3673, protoStatus="2", gameReject="0", buyReject="0", live="N",
               unsetSchedule="N", unsetEndDate="N", gameDate=ms(TIP), endDate=ms(TIP),
               homeId="10", awayId="66", winHandi=1.5, loseHandi=-1.5, drawHandi=0,
               drawAllot=0, winTxt="승", drawTxt="-", loseTxt="패", gameSubject=None,
               winAllot=1.74, loseAllot=1.74)
    row.update(changes)
    round_end = ms(TIP + timedelta(hours=4))
    doc = {"gmTs": 260133, "currentLottery": {"gmId": "G101", "gmTs": 260133,
           "saleProgress": True, "saleStatus": "SaleProgress", "saleEndDate": round_end,
           "saleStartDate": ms(TIP - timedelta(days=1))},
           "compSchedules": {"keys": list(row), "datas": [list(row.values())]}}
    return sources.quotes(json.dumps(doc), "260133", {"saleEndDate": round_end}, [g],
                          {"received_at": at.isoformat()})


def test_official_rest_uses_unpriced_prior_games():
    g = official_game()
    assert (g["home_rest"], g["away_rest"]) == (1, 2)
    assert (g["home_prior_id"], g["away_prior_id"]) == ("S49G01N60", "S49G01N59")


def test_first_price_is_full_game_and_requires_open_and_early_receipt():
    _, quote = price()[0]
    assert (quote["offer_id"], quote["line"], quote["odds"]) == ("G101:260133:3673", 1.5, [1.74, 1.74])
    assert price(at=TIP - timedelta(minutes=30)) == []
    assert price(at=TIP - timedelta(hours=25)) == []
    assert price(buyReject="1") == []
    assert price(protoStatus="4") == []
    assert price(loseHandi=-2.5) == []
    assert price(homeId="50") == []
    assert price(betId="7") == []


def test_immutable_paired_freeze_and_pending_without_roi(tmp_path):
    g, quote = price()[0]
    p = {name: {"selection": "home", "odds": 1.74} for name in cycle.MODELS}
    predict = Mock(return_value=p)
    first = cycle.freeze(tmp_path, g, quote, {}, lambda: AT, predict)
    assert first["quote"] == quote
    later = dict(quote, odds=[1.8, 1.7])
    assert cycle.freeze(tmp_path, g, later, {}, lambda: AT + timedelta(minutes=1), predict) is None
    stored = cycle.records(tmp_path, "predictions")
    assert stored[ID]["quote"]["odds"] == [1.74, 1.74]
    report = cycle.counts(stored, {})
    assert report["pending"] == 1 and report["models"]["frozen"]["roi"] is None
    predict.assert_called_once()



def test_counts_separate_different_picks_and_preserve_refund_denominators():
    def prediction(*sides):
        return {"models": {name: {"selection": side}
                           for name, side in zip(cycle.MODELS, sides, strict=True)}}

    def grade(status, *results):
        return {"status": status, "models": {name: {"hit": hit, "net": net}
                 for name, (hit, net) in zip(cycle.MODELS, results, strict=True)}}

    agree = prediction("away", "away", "away")
    disagree = prediction("home", "away", "home")
    predictions = {"same": agree, "different": disagree, "waiting": disagree, "refund": disagree}
    settlements = {
        "same": grade("settled", *((True, 0.74),) * 3),
        "different": grade("settled", (False, -1.0), (True, 0.99), (False, -1.0)),
        "refund": grade("void", *((None, 0.0),) * 3),
    }
    report = cycle.counts(predictions, settlements)
    assert (report["predictions"], report["settled"], report["pending"], report["void"]) == (4, 3, 1, 1)
    assert report["models"]["frozen"]["accuracy"] == 0.5
    assert report["models"]["static"]["roi"] == 1.73 / 3
    different = report["disagreements"]
    assert (different["predictions"], different["settled"], different["pending"], different["void"]) == (3, 2, 1, 1)
    assert different["models"]["frozen"] == {
        "hits": 0, "decided": 1, "net_units": -1.0, "accuracy": 0.0, "roi": -0.5,
    }
    assert different["models"]["static"] == {
        "hits": 1, "decided": 1, "net_units": 0.99, "accuracy": 1.0, "roi": 0.495,
    }


def test_counts_without_different_picks_has_no_comparison_metrics():
    report = cycle.counts({}, {})
    different = report["disagreements"]
    assert (different["predictions"], different["settled"], different["pending"], different["void"]) == (0, 0, 0, 0)
    assert all(row["accuracy"] is None and row["roi"] is None for row in different["models"].values())

def test_freeze_does_not_use_stale_or_late_capture(tmp_path):
    g, quote = price()[0]
    predict = Mock()
    assert cycle.freeze(tmp_path, g, quote, {}, lambda: AT + timedelta(minutes=11), predict) is None
    predict.assert_not_called()
