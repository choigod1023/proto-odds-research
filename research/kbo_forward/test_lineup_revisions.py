"""Synthetic archive fixtures exercise the same read-only replay as local receipts."""
import json
from datetime import timedelta

import pytest

from research.kbo_forward import lineup_revisions as revisions
from research.kbo_forward.official import LINEUP, SCHEDULE, lineup_params
from research.kbo_forward.prices import DETAIL_URL, LIST_URL, TOTAL, HANDICAP
from research.kbo_forward.test_official import lineup
from research.kbo_forward.test_prices import NOW, START, ROW, ROUND, detail

GAME = {
    "G_ID": "g1", "G_DT": "20261026", "G_TM": "19:00", "LE_ID": "1", "SR_ID": "1",
    "SEASON_ID": "2026", "HOME_ID": "SS", "AWAY_ID": "HT", "GAME_STATE_SC": "1",
    "CANCEL_SC_ID": "0", "START_PIT_CK": 1, "B_PIT_P_ID": 123, "T_PIT_P_ID": 456,
    "B_PIT_P_NM": "home pitcher", "T_PIT_P_NM": "away pitcher",
}
FINAL = GAME | {"GAME_STATE_SC": "3", "GAME_RESULT_CK": "1", "B_SCORE_CN": "6", "T_SCORE_CN": "5"}
PRODUCT_FIELDS = ("betId", "betTypId", "betNm", "betTypNm")
TOTAL_ROW = ROW | dict(zip(PRODUCT_FIELDS, TOTAL)) | {
    "matchSeq": 797, "winTxt": "언더", "loseTxt": "오버", "winHandi": 9.5, "loseHandi": 9.5,
}
HANDICAP_ROW = ROW | dict(zip(PRODUCT_FIELDS, HANDICAP)) | {
    "matchSeq": 798, "winHandi": -1.5, "loseHandi": 1.5,
}


def save(state, url, at, params, raw):
    official = url in (SCHEDULE, LINEUP)
    folder = state / ("official/bodies" if official else "prices/price-evidence")
    folder.mkdir(parents=True, exist_ok=True)
    digest = revisions.sha(raw)
    body = folder / (digest + (".bin" if official else ".body"))
    body.write_bytes(raw)
    record = {
        "url": url, "params": params, "method": "POST", "status": 200, "sha256": digest,
        "requested_at": (at - timedelta(seconds=1)).isoformat(),
        "finished_at" if official else "received_at": at.isoformat(),
        "content_type": "application/json",
    }
    log = state / ("official/receipts.jsonl" if official else "price-request-receipts.jsonl")
    with log.open("a", encoding="utf8") as stream:
        stream.write(json.dumps(record) + "\n")
    return body


def schedule(state, at, *games):
    return save(state, SCHEDULE, at, {"date": GAME["G_DT"], "leId": "1"},
                json.dumps({"code": "100", "game": list(games)}).encode())


def ordered(state, at, *, changed=False, confirmed=True, game=GAME):
    body = json.loads(lineup(game, confirmed))
    if changed and confirmed:
        table = json.loads(body[3][0])
        first, second = table["rows"][:2]
        first["row"][2], second["row"][2] = second["row"][2], first["row"][2]
        body[3][0] = json.dumps(table)
    return save(state, LINEUP, at, lineup_params(game), json.dumps(body).encode())


def price(state, at, rows, *, round_id=260127, include_detail=True):
    listing = json.dumps({"protoGames": [ROUND | {"gmTs": round_id}]}).encode()
    save(state, LIST_URL, at - timedelta(seconds=2), {}, listing)
    if include_detail:
        raw = detail(rows).replace(b'"gmTs": 260127', f'"gmTs": {round_id}'.encode())
        return save(state, DETAIL_URL, at, {"gmTs": str(round_id), "gmId": "G101", "gameYear": "2026"}, raw)


def archive(state, *, before=(ROW,), after=(ROW,), middle=None, final=FINAL):
    schedule(state, NOW, GAME)
    first = ordered(state, NOW + timedelta(minutes=2))
    schedule(state, NOW + timedelta(minutes=4), GAME)
    ordered(state, NOW + timedelta(minutes=7), changed=True)
    price(state, NOW + timedelta(minutes=1), before)
    if middle is not None:
        price(state, NOW + timedelta(minutes=5), middle)
    last_price = price(state, NOW + timedelta(minutes=8), after)
    if final is not None:
        schedule(state, START + timedelta(hours=3), final)
    return first, last_price


def replay(state):
    return revisions.replay(state, as_of=START + timedelta(days=1))


def test_same_exact_products_and_complete_prices_link_to_latest_official_score(tmp_path):
    rows = [ROW, TOTAL_ROW, HANDICAP_ROW]
    archive(tmp_path, before=rows, after=rows, middle=rows)
    correction = schedule(tmp_path, START + timedelta(hours=4), FINAL | {"T_SCORE_CN": 6})
    output = replay(tmp_path)
    assert output["counts"]["eligible_games"] == 1
    assert output["counts"]["eligible_rows"] == 3
    assert output["counts"]["changed_lineups"] == 1
    assert [r["market"]["line"] for r in output["rows"]] == [None, 9.5, -1.5]
    winner, total, handicap = output["rows"]
    assert total["market"]["odds"] == {"under": 2.02, "over": 1.53}
    assert handicap["market"]["settlement_rule"] == "handicap_half"
    assert [p["name"] for p in winner["before"]["batting_order"]["home"][:2]] == ["name1", "name2"]
    assert [p["name"] for p in winner["after"]["batting_order"]["home"][:2]] == ["name2", "name1"]
    assert winner["before"]["starters"]["home"]["player_id"] == "123"
    assert len(winner["market"]["observations"]) == 3
    assert winner["official_result"]["away_score"] == 6
    assert winner["official_result"]["schedule"]["raw_sha256"] == correction.stem


@pytest.mark.parametrize("after", [
    (ROW | {"loseAllot": 1.54},),
    (ROW | {"loseAllot": 0},),
    (ROW | {"matchSeq": 800},),
    (ROW | {"protoStatus": "4"},),
    (ROW | {"awayId": "LG"},),
    (ROW, ROW),
])
def test_different_incomplete_or_unavailable_price_is_not_stable(tmp_path, after):
    archive(tmp_path, after=after)
    assert replay(tmp_path)["counts"]["without_stable_prices"] == 1


@pytest.mark.parametrize("before,after", [
    (TOTAL_ROW, TOTAL_ROW | {"winHandi": 10.5, "loseHandi": 10.5}),
    (HANDICAP_ROW, HANDICAP_ROW | {"winHandi": 1.5, "loseHandi": -1.5}),
    (TOTAL_ROW, HANDICAP_ROW | {"matchSeq": TOTAL_ROW["matchSeq"]}),
])
def test_exact_product_and_signed_line_cannot_be_swapped(tmp_path, before, after):
    archive(tmp_path, before=[before], after=[after])
    assert replay(tmp_path)["counts"]["without_stable_prices"] == 1


@pytest.mark.parametrize("middle", [[], [ROW | {"winAllot": 2.12}]])
def test_intervening_missing_or_changed_odds_blocks_same_endpoints(tmp_path, middle):
    archive(tmp_path, middle=middle)
    assert replay(tmp_path)["counts"]["without_stable_prices"] == 1


@pytest.mark.parametrize("correction", [
    FINAL | {"G_TM": "19:10"},
    FINAL | {"SR_ID": "3"},
    FINAL | {"GAME_RESULT_CK": "0"},
    FINAL | {"B_SCORE_CN": 6.1},
    FINAL | {"CANCEL_SC_ID": "1", "CANCEL_SC_NM": "취소"},
])
def test_latest_nonmatching_result_cannot_use_older_final(tmp_path, correction):
    archive(tmp_path)
    schedule(tmp_path, START + timedelta(hours=5), correction)
    assert replay(tmp_path)["counts"]["without_official_final"] == 1


@pytest.mark.parametrize("which,reason", [(0, "invalid_or_unconfirmed_lineups"), (1, "without_stable_prices")])
def test_changed_raw_bytes_are_rejected(tmp_path, which, reason):
    targets = archive(tmp_path)
    targets[which].write_bytes(b"changed body")
    output = replay(tmp_path)
    assert output["counts"][reason] == 1
    assert output["rows"] == []


def test_unconfirmed_intermediate_observation_breaks_the_lineup_pair(tmp_path):
    archive(tmp_path)
    ordered(tmp_path, NOW + timedelta(minutes=5), confirmed=False)
    assert replay(tmp_path)["counts"]["changed_lineups"] == 0


def test_confirmed_starter_replacement_with_same_batting_order(tmp_path):
    schedule(tmp_path, NOW, GAME)
    ordered(tmp_path, NOW + timedelta(minutes=2))
    replacement = GAME | {"B_PIT_P_ID": 321, "B_PIT_P_NM": "replacement"}
    schedule(tmp_path, NOW + timedelta(minutes=4), replacement)
    ordered(tmp_path, NOW + timedelta(minutes=7), game=replacement)
    price(tmp_path, NOW + timedelta(minutes=1), [ROW])
    price(tmp_path, NOW + timedelta(minutes=8), [ROW])
    schedule(tmp_path, START + timedelta(hours=3), FINAL)
    row, = replay(tmp_path)["rows"]
    assert row["before"]["batting_order"] == row["after"]["batting_order"]
    assert row["after"]["starters"]["home"]["player_id"] == "321"


def test_unchanged_quotes_with_more_than_ten_minutes_between_do_not_qualify(tmp_path):
    schedule(tmp_path, NOW, GAME)
    ordered(tmp_path, NOW + timedelta(minutes=2))
    schedule(tmp_path, NOW + timedelta(minutes=12), GAME)
    ordered(tmp_path, NOW + timedelta(minutes=13), changed=True)
    price(tmp_path, NOW + timedelta(minutes=1), [ROW])
    price(tmp_path, NOW + timedelta(minutes=14), [ROW])
    schedule(tmp_path, START + timedelta(hours=3), FINAL)
    assert replay(tmp_path)["counts"]["without_stable_prices"] == 1


@pytest.mark.parametrize("delay,expected", [(timedelta(0), 1), (timedelta(seconds=1), 0)])
def test_t30_boundary_uses_body_completion(tmp_path, delay, expected):
    cutoff = START - timedelta(minutes=30)
    schedule(tmp_path, cutoff - timedelta(minutes=9), GAME)
    price(tmp_path, cutoff - timedelta(minutes=8), [ROW])
    ordered(tmp_path, cutoff - timedelta(minutes=6))
    ordered(tmp_path, cutoff + delay, changed=True)
    price(tmp_path, cutoff, [ROW])
    schedule(tmp_path, START + timedelta(hours=3), FINAL)
    assert replay(tmp_path)["counts"]["eligible_rows"] == expected


def test_round_change_cannot_reuse_match_sequence(tmp_path):
    schedule(tmp_path, NOW, GAME)
    ordered(tmp_path, NOW + timedelta(minutes=2))
    ordered(tmp_path, NOW + timedelta(minutes=7), changed=True)
    price(tmp_path, NOW + timedelta(minutes=1), [ROW])
    price(tmp_path, NOW + timedelta(minutes=8), [ROW], round_id=260128)
    schedule(tmp_path, START + timedelta(hours=3), FINAL)
    assert replay(tmp_path)["counts"]["without_stable_prices"] == 1


def test_missing_detail_breaks_observed_price_continuity(tmp_path):
    schedule(tmp_path, NOW, GAME)
    ordered(tmp_path, NOW + timedelta(minutes=2))
    ordered(tmp_path, NOW + timedelta(minutes=7), changed=True)
    price(tmp_path, NOW + timedelta(minutes=1), [ROW])
    price(tmp_path, NOW + timedelta(minutes=5), [], include_detail=False)
    price(tmp_path, NOW + timedelta(minutes=8), [ROW])
    schedule(tmp_path, START + timedelta(hours=3), FINAL)
    assert replay(tmp_path)["counts"]["without_stable_prices"] == 1


def test_doubleheader_requires_unique_matching_kickoff(tmp_path):
    archive(tmp_path)
    schedule(tmp_path, NOW + timedelta(minutes=5), GAME, GAME | {"G_ID": "second"})
    assert replay(tmp_path)["counts"]["without_stable_prices"] == 1


def test_empty_archive_reports_zero_without_writing(tmp_path):
    output = replay(tmp_path)
    assert output["counts"]["eligible_games"] == output["counts"]["eligible_rows"] == 0
    assert list(tmp_path.iterdir()) == []
