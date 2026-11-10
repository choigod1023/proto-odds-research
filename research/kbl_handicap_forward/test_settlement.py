from hashlib import sha256
import json
import unittest

from research.kbl_handicap_forward import sources
from research.kbl_handicap_forward.settlement import settle


class SettlementTests(unittest.TestCase):
    def setUp(self):
        frozen = {"id": "S49G01N7", "kickoff": "2026-04-27T19:00:00+09:00",
                  "home_id": "66", "away_id": "50"}
        self.game = dict(frozen, kickoff="2026-04-27T10:00:00Z", score=[90, 80], state="final")
        self.prediction = {
            "game": frozen,
            "quote": {"offer_id": "G101:260049:1729", "line": -1.5, "odds": [1.87, 1.99],
                      "period": "full_including_extra", "settlement_rule": "handicap_half"},
            "models": {"frozen": {"selection": "home", "odds": 1.87},
                       "static": {"selection": "away", "odds": 1.99},
                       "phase_rest": {"selection": "home", "odds": 1.87}},
        }
        self.row = dict(zip(sources.PRODUCT_KEYS, sources.PRODUCT)) | {
            "matchSeq": 1729, "gameDate": 1777284000000, "homeId": "66", "awayId": "50",
            "protoStatus": "4", "gameReject": "0", "buyReject": "4", "gameResult": "0", "live": "N",
            "gameSubject": None,
            "winHandi": -1.5, "loseHandi": 1.5, "drawHandi": 0,
            "winAllot": 1.85, "loseAllot": 1.64, "drawAllot": 0,
            "winTxt": "승", "drawTxt": "-", "loseTxt": "패", "mchScore": "88.5:80",
        }

    def evidence(self, rows=None):
        keys = list(self.row)
        body = json.dumps({
            "gmTs": 260049, "currentLottery": {"gmId": "G101", "gmTs": 260049},
            "compSchedules": {"keys": keys, "datas": [[row[k] for k in keys] for row in (rows or [self.row])]},
        }).encode()
        receipt = {"url": sources.DETAIL_URL, "status": 200, "sha256": sha256(body).hexdigest(),
                   "received_at": "2026-04-27T12:00:00Z", "payload": {"gmId": "G101", "gmTs": "260049"}}
        return body, receipt

    def grade(self):
        return settle(self.prediction, self.game, *self.evidence())

    def test_settles_at_captured_price_and_keeps_receipt(self):
        body, receipt = self.evidence()
        result = settle(self.prediction, self.game, body, receipt)
        self.assertEqual((result["status"], result["winner"], result["score"]), ("settled", "home", [90, 80]))
        self.assertEqual(result["official_betman_receipt"], receipt)
        self.assertEqual(result["models"]["frozen"], {"selection": "home", "odds": 1.87, "hit": True, "net": 0.87})
        self.assertEqual(result["models"]["static"], {"selection": "away", "odds": 1.99, "hit": False, "net": -1.0})
        self.assertEqual(result["models"]["phase_rest"], result["models"]["frozen"])
        self.assertEqual(result, self.grade())
        self.assertNotIn("hit", self.prediction["models"]["frozen"])

    def test_regrades_moved_line_only_after_current_score_and_result_agree(self):
        self.game["score"] = [78, 80]
        self.row.update(winHandi=2.5, loseHandi=-2.5, mchScore="80.5:80")
        result = self.grade()
        self.assertEqual((result["line"], result["official_line"], result["winner"]), (-1.5, 2.5, "away"))
        self.assertEqual(result["models"]["static"]["net"], 0.99)
        for change in ({"mchScore": "80.5:79"}, {"gameResult": "2"}, {"mchScore": "bad"}):
            with self.subTest(change=change):
                self.assertIsNone(settle(self.prediction, self.game, *self.evidence([self.row | change])))

    def test_explicit_void_can_settle_without_kbl_score(self):
        self.game.update(state="other", score=None)
        self.row.update(gameResult="4", mchScore=None, winTxt=None, drawTxt=None, loseTxt=None,
                        winAllot=0, loseAllot=0, winHandi=0, loseHandi=0)
        result = self.grade()
        self.assertEqual((result["status"], result["winner"], result["score"]), ("void", None, None))
        self.assertTrue(all(p["hit"] is None and p["net"] == 0 for p in result["models"].values()))
        for change in ({"protoStatus": "2"}, {"homeId": "999"}, {"winAllot": 1.85}, {"gameResult": "0"}):
            with self.subTest(change=change):
                self.assertIsNone(settle(self.prediction, self.game, *self.evidence([self.row | change])))

    def test_ambiguous_incomplete_or_different_offers_stay_pending(self):
        changes = ({"matchSeq": 1730}, {"gameDate": self.row["gameDate"] + 60000}, {"awayId": "60"},
                   {"protoStatus": "3"}, {"betId": "3"}, {"betTypId": "3"}, {"gameResult": "4"},
                   {"gameReject": "1"}, {"winHandi": -1}, {"live": "Y"})
        for change in changes:
            with self.subTest(change=change):
                self.assertIsNone(settle(self.prediction, self.game, *self.evidence([self.row | change])))
        self.assertIsNone(settle(self.prediction, self.game, *self.evidence([self.row, self.row])))
        for game in (dict(self.game, id="wrong"), dict(self.game, state="scheduled", score=None),
                     dict(self.game, score=[80, 80]), dict(self.game, score=[90.0, 80])):
            self.assertIsNone(settle(self.prediction, game, *self.evidence()))

    def test_requires_exact_receipt_identity_integrity_and_postgame_time(self):
        body, receipt = self.evidence()
        for field, value in (("status", 403), ("url", "https://example.com"), ("sha256", "0" * 64),
                             ("received_at", "2026-04-27T10:00:00Z"),
                             ("received_at", "2026-04-27T12:00:00"),
                             ("payload", {"gmId": "G101", "gmTs": "260050"})):
            with self.subTest(field=field, value=value):
                self.assertIsNone(settle(self.prediction, self.game, body, receipt | {field: value}))
        for required in ("url", "status", "sha256", "received_at"):
            with self.subTest(missing=required):
                self.assertIsNone(settle(self.prediction, self.game, body, {k: v for k, v in receipt.items() if k != required}))
        self.assertIsNone(settle(self.prediction, self.game, body + b" ", receipt))

    def test_requires_captured_period_rule_half_line_and_model_prices(self):
        quote = self.prediction["quote"]
        body, receipt = self.evidence()
        for change in ({"period": "first_half"}, {"settlement_rule": "handicap_integer"}, {"line": -1},
                       {"offer_id": "G101:260050:1729"}):
            with self.subTest(change=change):
                self.assertIsNone(settle(self.prediction | {"quote": quote | change}, self.game, body, receipt))
        for required in ("period", "settlement_rule"):
            with self.subTest(missing=required):
                incomplete = {k: v for k, v in quote.items() if k != required}
                self.assertIsNone(settle(self.prediction | {"quote": incomplete}, self.game, body, receipt))
        self.prediction["models"]["frozen"]["odds"] = 1.85
        self.assertIsNone(self.grade())
