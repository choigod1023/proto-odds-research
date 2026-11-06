import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1] / "experiments/kleague-cross-market-nba-20261106"
SPEC = importlib.util.spec_from_file_location("kleague_cross_market", ROOT / "kleague_replay.py")
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def test_cross_market_requires_same_team_pair_and_full_game():
    bridge = {"betman_league_code": "SC001", "home_team_id": "K01", "away_team_id": "K02",
              "kickoff_kst": "2026-07-01T19:00:00+09:00", "official_score": [2, 1],
              "matchSeq": 10, "prices": {"home": 2.0, "draw": 3.0, "away": 4.0}}
    identity = {"itemCode": "SC", "leagueCode": "SC001", "homeId": "K01", "awayId": "K02",
                "gameDate": 1782900000000, "gameReject": "0", "buyReject": "0", "live": "N",
                "winAllot": 2.0, "drawAllot": 3.0, "loseAllot": 4.0}
    winner = identity | dict(matchSeq=10, handi=0, winHandi=0, drawHandi=0, loseHandi=0, gameResult="0")
    total = identity | dict(matchSeq=11, handi=9, winHandi=2.5, drawHandi=0, loseHandi=2.5,
                            drawAllot=0, gameResult="2")
    handicap = identity | dict(matchSeq=12, handi=2, winHandi=-1, drawHandi=1, loseHandi=1, gameResult="1")
    assert replay.match_snapshot([winner, total | {"awayId": "K03"}, handicap], bridge) == (None, "missing_total")
    half = handicap | dict(betId="119", betTypId="4", betNm="축구 전반 핸디캡", betTypNm="일반 정수핸디캡")
    assert replay.match_snapshot([winner, total, half], bridge) == (None, "missing_handicap")
    assert replay.match_snapshot([winner, total, handicap | {"gameResult": "0"}], bridge) == (None, "missing_handicap")
    quotes, reason = replay.match_snapshot([winner, total, handicap], bridge)
    assert reason is None
    assert [quotes[k]["id"] for k in ("winner", "total", "handicap")] == [10, 11, 12]


def test_open_round_requires_receipt_during_sale(tmp_path):
    source = ROOT / "round-260132.json"
    opened = replay.pregame_snapshot(source)
    assert opened["coverage"] == {"K1": 6, "K2": 8}
    meta = json.loads(source.read_bytes())
    (tmp_path / meta["body_file"]).write_bytes((ROOT / meta["body_file"]).read_bytes())
    meta["received_at"] = "2026-11-10T15:00:00+09:00"
    path = tmp_path / source.name
    path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="not open at capture time"):
        replay.pregame_snapshot(path)
