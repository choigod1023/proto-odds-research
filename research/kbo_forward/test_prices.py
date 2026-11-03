"""Regression coverage for the Betman public response schema."""
import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import prices

# Product fields from the archived official 2026-127 response (SS/HT, 796).
# Timestamps are synthetic and are never claimed as historical receipts.
NOW = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc)
START = NOW + timedelta(hours=2)
def ms(dt): return int(dt.timestamp() * 1000)
ROW = {"itemCode":"BS", "leagueCode":"BS001", "leagueName":"KBO", "matchSeq":796,
       "homeId":"SS", "awayId":"HT", "homeName":"삼성 라이온즈", "awayName":"KIA 타이거즈",
       "winTxt":"승", "drawTxt":"-", "loseTxt":"패", "winAllot":2.02, "drawAllot":0.0, "loseAllot":1.53,
       "handi":21, "winHandi":0.0, "drawHandi":0.0, "loseHandi":0.0, "gameReject":"0", "buyReject":"0",
       "protoStatus":"2", "live":"N", "unsetSchedule":"N", "unsetEndDate":"N", "gameSubject":None,
       "betId":"2", "betTypId":"2", "betNm":"야구 승패", "betTypNm":"일반 승패",
       "gameDate":ms(START), "endDate":ms(START)}
ROUND = {"gmId":"G101", "gmTs":260127, "gmOsidTsYear":2026, "saleEndDate":ms(START + timedelta(hours=4))}
GAME = {"official_game_id":"g1", "season":2026, "home_id":"SS", "away_id":"HT", "kickoff_at":START.isoformat()}

def detail(rows):
    keys = list(ROW)
    return json.dumps({"gmTs":260127, "compSchedules":{"keys":keys, "datas":[[r.get(k) for k in keys] for r in rows]}}, ensure_ascii=False).encode()

class Response:
    def __init__(self, body, status=200, content_type="application/json"):
        self.status_code = status; self.content = body; self.headers = {"Content-Type":content_type}

class Client:
    def __init__(self, responses): self.responses=iter(responses); self.calls=[]
    def post(self, url, **kwargs): self.calls.append((url, kwargs)); return next(self.responses)

class ProbeTests(unittest.TestCase):
    def parse(self, rows, games=None, receipt=NOW):
        return prices.parse_detail(detail(rows), ROUND, games if games is not None else [GAME], receipt.isoformat(), {"source":"test"})

    def test_exact_product_and_identity(self):
        q, x = self.parse([ROW]); self.assertEqual(x, []); self.assertEqual(q[0]["official_game_id"], "g1")
        self.assertEqual(q[0]["odds"], {"home":2.02, "away":1.53}); self.assertEqual(q[0]["settlement_rule"], "draw_void_refund")

    def test_win_by_one_and_first_half_are_not_full_game_draw(self):
        for product in [dict(betId="108",betTypId="3",betNm="야구 승1패",betTypNm="승N패",drawTxt="1",drawAllot=3.55),
                        dict(betId="111",betTypId="1",betNm="야구 전반 승무패",betTypNm="승무패",drawTxt="무",drawAllot=3.55)]:
            q, x = self.parse([ROW | product]); self.assertEqual(q, []); self.assertEqual(x[0]["reason"], "unsupported_product")

    def test_timing_status_identity_and_price_fail_closed(self):
        cases=[([ROW], [GAME], START-timedelta(minutes=29), "late_or_closed"),
               ([ROW | {"protoStatus":"4"}], [GAME], NOW, "not_confirmed_on_sale"),
               ([ROW | {"winAllot":float('nan')}], [GAME], NOW, "invalid_field"),
               ([ROW], [GAME,GAME], NOW, "fixture_unmatched_or_ambiguous"),
               ([ROW], [GAME | {"away_id":"LG"}], NOW, "fixture_unmatched_or_ambiguous")]
        for rows, games, receipt, reason in cases:
            with self.subTest(reason=reason):
                q,x=self.parse(rows,games,receipt);self.assertEqual(q,[]);self.assertEqual(x[0]["reason"],reason)

    @patch.object(prices.time, "sleep")
    @patch.object(prices, "_utc", return_value=NOW)
    def test_actual_collection_path_with_valid_json(self, clock, sleep):
        listing=json.dumps({"protoGames":[ROUND]}).encode(); body=detail([ROW]); client=Client([Response(listing),Response(body)])
        with tempfile.TemporaryDirectory() as directory:
            result=prices.collect_prices(directory,[GAME],client)
            self.assertEqual(len(result["quotes"]),1);self.assertEqual(len(result["requests"]),2)
            self.assertEqual(Path(result["requests"][1]["body_file"]).read_bytes(),body)
            self.assertEqual(client.calls[1][1]["json"]["gmTs"],"260127")

    def test_error_and_throttle_are_not_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            result=prices.collect_prices(directory,[GAME],Client([Response(b"<html>error</html>",content_type="text/html")]))
            self.assertEqual(result["quotes"],[]);self.assertEqual(result["excluded"][0]["reason"],"official_non_json_response")
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,"price-last-request.json").write_text('{}');client=Client([])
            result=prices.collect_prices(directory,[GAME],client);self.assertEqual(result["quotes"],[]);self.assertEqual(client.calls,[])

if __name__ == "__main__": unittest.main()
