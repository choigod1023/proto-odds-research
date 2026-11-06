from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from src import odds_live  # Adds src to the module path, as the runtime entry point does.
import kleague_price_windows as windows
from runtime_db import RuntimeDatabase
from wisetoto import parse_rows
from scripts import compare_kleague_price_windows as compare

UTC = timezone.utc
OPEN = datetime(2026, 11, 10, 0, tzinfo=UTC)
KICK = datetime(2026, 11, 11, 10, 30, tzinfo=UTC)


def body(odds):
    values = "".join(f'<li class="a9">{value:.2f}</li>' for value in odds)
    return (f'<div class="gameinfo"><ul><li class="a1">123</li>'
            f'<li class="a2">11.11(수) 19:30</li><li class="a3 sc">축구</li>'
            f'<li class="a4">K리그1</li><li class="hm"></li>'
            f'<li class="a6">강원FC</li><li class="a8">부천FC</li>{values}'
            f'<li>경기전</li></ul></div>').encode()


def official_catalog():
    def ms(stamp):
        return int(stamp.timestamp() * 1000)

    row = dict(matchSeq=123, leagueCode="SC001", itemCode="SC", betId="1", betTypId="1",
               betNm="축구 승무패", betTypNm="승무패", live="N", handi=0, winHandi=0, drawHandi=0,
               loseHandi=0, unsetSchedule="N", unsetEndDate="N", homeId="K21", awayId="K26",
               gameDate=ms(KICK), endDate=ms(KICK), gameReject="0", buyReject="0")
    return json.dumps({"gmTs": 260133, "currentLottery": {
        "gmTs": 260133, "gmId": "G101", "saleProgress": True, "saleStatus": "SaleProgress",
        "saleStartDate": ms(OPEN), "saleEndDate": ms(KICK + timedelta(hours=3)),
    }, "compSchedules": {"keys": list(row), "datas": [list(row.values())]}}).encode()


def test_two_windows_reparse_exact_db_prices_and_settle_official_game(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(windows, "fetch_catalog", lambda *key: (
        calls.append(key) or official_catalog(), OPEN + timedelta(minutes=10, seconds=1)))
    db = RuntimeDatabase(tmp_path / "runtime.sqlite3")
    recorder = windows.PriceWindowRecorder(db, tmp_path / "capture")
    first = body([2.10, 3.20, 3.30])
    last = body([1.90, 3.40, 3.70])

    def observe(raw, instant, rnd=133):
        return recorder.observe(2026, rnd, parse_rows(raw.decode(), 2026, rnd), instant, raw)

    assert observe(first, OPEN + timedelta(minutes=10), rnd=132) == 0
    assert observe(first, OPEN + timedelta(minutes=10, microseconds=1)) == 1
    assert observe(last, OPEN + timedelta(minutes=20)) == 0
    assert observe(last, KICK - timedelta(minutes=30) + timedelta(microseconds=1)) == 0
    assert observe(last, KICK - timedelta(minutes=32)) == 1
    assert observe(first, KICK - timedelta(minutes=31)) == 0
    assert calls == [(2026, 133)]
    quotes = compare.db_quotes(recorder.root, db.path)
    assert len(quotes) == 2
    assert len(list((recorder.root / "2026-133" / "bodies").glob("*.gz"))) == 3

    def schedule(*key):
        assert key == (1, 2026, 11)
        return "official", [dict(leagueId=1, gameId=456, gameDate="2026.11.11", gameTime="19:30",
                                homeTeam="K21", awayTeam="K26", gameStatus="FE", endYn="Y",
                                homeGoal=2, awayGoal=1)]

    monkeypatch.setattr(compare, "MIN_MATCHES", 1)
    monkeypatch.setattr(compare, "MIN_ROUNDS", 1)
    result = compare.evaluate(recorder.root, quotes, schedule)
    assert result["verified_db_pairs"] == result["settled"] == 1
    assert result["fixtures"][0]["fixture"] == (2026, 1, 456)
    assert result["metrics"]["baseline"]["roi"] == result["metrics"]["movement"]["roi"] == 1.90 - 1
    assert result["metrics"]["baseline"]["log_loss"] != result["metrics"]["movement"]["log_loss"]
    quotes[0]["odds_json"] = "[2.20,3.20,3.30]"
    tampered = compare.evaluate(recorder.root, quotes, schedule)
    assert tampered["evidence_mismatch"] == 1 and tampered["metrics"] is None


def test_capture_failure_does_not_block_live_prices(monkeypatch):
    raw = body([2.10, 3.20, 3.30])
    rows = parse_rows(raw.decode(), 2026, 133)
    monkeypatch.setattr(odds_live, "_session", lambda: object())
    monkeypatch.setattr(odds_live, "_start_hint", lambda _: 133)
    monkeypatch.setattr(odds_live, "find_live_rounds", lambda *args: [133])
    monkeypatch.setattr(odds_live, "_fetch_observation", lambda _s, _y, rnd: (rows, OPEN, raw) if rnd == 133 else None)

    def fail(*_):
        raise OSError("archive unavailable")

    result = odds_live.collect(price_windows=SimpleNamespace(observe=fail))
    assert result["odds"] == {"133": {"123": [2.10, 3.20, 3.30]}}
