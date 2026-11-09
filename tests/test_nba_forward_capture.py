from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace

from scripts import nba_forward_capture as capture
from scripts import nba_forward_run as run


DAY = date(2026, 11, 8)
START = datetime(2026, 11, 9, 1, tzinfo=timezone.utc)
EARLY = START - timedelta(minutes=3)
GAME = "0022600200"
ROUND = "260133"


def sources():
    teams = {"home": ("MIL", 1610612749), "away": ("BOS", 1610612738)}
    fixture = {"gameId": GAME, "gameTimeUtc": START.isoformat(),
               "gameTimeEastern": "2026-11-08T20:00:00", "gameStatus": 1}
    game = {"gameId": GAME, "gameStatus": 1}
    for index, (side, (code, team_id)) in enumerate(teams.items()):
        fixture[side + "Team"] = dict(teamTricode=code, teamId=team_id)
        game[side + "Team"] = dict(teamAbbreviation=code, teamId=team_id, players=[
            dict(personId=1 + index * 10 + i, teamId=team_id, playerName=f"Player {side} {i}",
                 position=position, lineupStatus="Confirmed", rosterStatus="Active")
            for i, position in enumerate(sorted(capture.POSITIONS))])
    props = {"selectedDate": DAY.isoformat(), "gameCardFeed": {"modules": [{"cards": [{"cardData": fixture}]}]}}
    raw = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": props}}) + '</script>'
    schedule = capture.receipt(capture.NBA_SCHEDULE + DAY.isoformat(), EARLY, raw.encode())
    millis = int(START.timestamp() * 1000)
    lottery = dict(gmId="G101", gmTs=ROUND, saleStatus="SaleProgress", saleProgress=True,
                   saleStartDate=millis-86400000, saleEndDate=millis+86400000)
    row = dict(itemCode="BK", leagueCode="BK002", leagueName="NBA", betId="3", betTypId="2",
               betNm="농구 승패", betTypNm="일반 승패", live="N", unsetSchedule="N", unsetEndDate="N",
               winHandi=0, drawHandi=0, loseHandi=0, protoStatus="2", gameReject="0", buyReject="0",
               gameResult=None, winTxt="승", drawTxt="-", loseTxt="패", drawAllot=0,
               gameDate=millis, endDate=millis, matchSeq=3601, homeId="MIL", awayId="BOS",
               winAllot=5.32, loseAllot=1.04)
    return schedule, game, lottery, row


def quotes(lottery, row, at=EARLY):
    doc = dict(gmTs=lottery["gmTs"], currentLottery=lottery,
               compSchedules=dict(keys=list(row), datas=[list(row.values())]))
    return capture.receipt(capture.DETAIL, at, json.dumps(doc).encode())


def matches(monkeypatch, schedule, game, prices, at=EARLY):
    monkeypatch.setattr(capture, "utc_now", lambda: START + timedelta(hours=4))
    lineup = capture.receipt(capture.NBA_LINEUPS.format("20261108"), at,
                             json.dumps({"games": [game]}).encode())
    return list(capture.match_records(DAY, schedule, lineup, prices))


def test_only_one_immutable_game_with_original_price_and_raw_bodies(tmp_path, monkeypatch):
    schedule, game, lottery, row = sources()
    [(saved, evidence)] = matches(monkeypatch, schedule, game, {ROUND: quotes(lottery, row)})
    with capture.database(tmp_path / "archive.sqlite3") as db:
        assert capture.save(db, saved, evidence)
        assert not capture.save(db, dict(saved, odds=[4.1, 1.2]), evidence)
        assert run.read_rows(db, "captures")[GAME]["odds"] == [5.32, 1.04]
        assert db.execute("SELECT count(*) FROM bodies").fetchone()[0] == 3


def test_expected_lineup_and_closed_price_are_held(monkeypatch):
    schedule, game, lottery, row = sources()
    assert not matches(monkeypatch, schedule, game, {ROUND: quotes(lottery, row)}, at=START)
    assert not matches(monkeypatch, schedule, game, {ROUND: quotes(lottery, row, at=START)})
    game["awayTeam"]["players"][0]["lineupStatus"] = "Expected"
    assert not matches(monkeypatch, schedule, game, {ROUND: quotes(lottery, row)})


def test_ambiguous_product_or_team_clock_is_held(monkeypatch):
    schedule, game, lottery, row = sources()
    later = quotes(dict(lottery, gmTs="260134"), dict(row, matchSeq=3701))
    assert not matches(monkeypatch, schedule, game, {ROUND: quotes(lottery, row), "260134": later})
    assert not matches(monkeypatch, schedule, game, {ROUND: quotes(lottery, dict(row, homeId="BOS", awayId="MIL"))})
    assert not matches(monkeypatch, schedule, game, {ROUND: quotes(lottery, dict(row, gameDate=row["gameDate"] + 60000))})


def test_freeze_uses_archived_lineup_and_shared_price(tmp_path, monkeypatch):
    schedule, game, lottery, row = sources()
    [(saved, evidence)] = matches(monkeypatch, schedule, game, {ROUND: quotes(lottery, row)})
    model = SimpleNamespace(
        build_day_state=lambda *args: {},
        calculate_match=lambda *args: (
            {"q_recomputed": 0.4, "player_spatial": 0.6, "player_source_game_ids": []}, None
        ),
    )
    monkeypatch.setattr(run, "pinned_model", lambda _: (model, [], [], {}, {"commit": "pinned", "sha256": {}}))
    monkeypatch.setattr(run, "lineup_rotation", lambda row, side, game, *args: game[side + "Team"]["players"])
    monkeypatch.setattr(capture, "utc_now", lambda: EARLY + timedelta(seconds=5))
    with capture.database(tmp_path / "archive.sqlite3") as db:
        assert capture.save(db, saved, evidence)
        assert run.freeze(db, None) == (1, {})
        forecast = run.read_rows(db, "forecasts")[GAME]
        assert forecast["odds"] == saved["odds"]
        assert forecast["rotations"][0] == game["homeTeam"]["players"]
        assert forecast["probabilities"]["confirmed_player_spatial"] == 0.6
        assert run.freeze(db, None) == (0, {})


def test_official_result_and_paired_payout(tmp_path, monkeypatch):
    schedule, game, lottery, row = sources()
    [(saved, evidence)] = matches(monkeypatch, schedule, game, {ROUND: quotes(lottery, row)})
    final = dict(row, protoStatus="4", gameResult="2", mchScore="101:109")
    assert run.official_result(saved, quotes(lottery, final)) is None
    at = START + timedelta(hours=4)
    assert run.official_result(saved, quotes(lottery, dict(final, gameResult="0"), at)) is None
    outcome = run.official_result(saved, quotes(lottery, final, at))
    assert outcome["winner"] == 1
    forecast = dict(frozen_at=(EARLY+timedelta(seconds=2)).isoformat(), odds=saved["odds"],
                    probabilities={"q": 0.6, "market": 0.2, "confirmed_player_spatial": 0.3})
    monkeypatch.setattr(capture, "MIN_SETTLED", 1)
    with capture.database(tmp_path / "archive.sqlite3") as db:
        capture.save(db, saved, evidence)
        db.execute("INSERT INTO settlements VALUES (?, ?)", (GAME, json.dumps(outcome)))
        db.execute("INSERT INTO forecasts VALUES (?, ?)", (GAME, json.dumps(forecast)))
        result = run.comparison(db)
        assert result["common_settled"] == 1
        assert result["models"]["q"]["roi"] == -1
        assert result["models"]["confirmed_player_spatial"]["roi"] == 0.04
        db.execute("UPDATE forecasts SET record = ?", (json.dumps(dict(forecast, frozen_at=START.isoformat())),))
        assert run.comparison(db)["common_settled"] == 0
