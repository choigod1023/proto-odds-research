"""Replay the official KBL and Betman response shapes through the whole cycle."""

import json
import sys
from datetime import timedelta
from unittest.mock import MagicMock, Mock

import pytest
import requests

from research.kbl_handicap_forward import cycle, sources
from research.kbl_handicap_forward.test_cycle import AT, ID, TIP, fixture, price


PAST_ID = "S49G01N62"
ROUND = "260133"
SALE_END = TIP + timedelta(hours=4)


def millis(at):
    return int(at.timestamp() * 1000)


def detail(round_id, *offers):
    keys = list(offers[0])
    return {
        "gmTs": int(round_id),
        "currentLottery": {"gmId": "G101", "gmTs": int(round_id), "saleProgress": True,
                           "saleStatus": "SaleProgress", "saleEndDate": millis(SALE_END),
                           "saleStartDate": millis(TIP - timedelta(days=1))},
        "compSchedules": {"keys": keys, "datas": [[offer[key] for key in keys] for offer in offers]},
    }


def response(payload, status=200):
    http = requests.Response()
    http.status_code = status
    http._content = json.dumps(payload).encode()
    http._content_consumed = True
    return http


@pytest.fixture
def official(tmp_path, monkeypatch):
    # 133회차 실제 상품 형태: 3623번 정산(85:78, -2.5), 3673번 판매(+1.5).
    settled = fixture(62, "20261109", "35", "06", final=True)
    settled.update(scoreH=85, scoreA=78)
    schedule = [settled, fixture(63, "20261110", "10", "66")]
    past = sources.schedule(json.dumps(schedule))[PAST_ID]
    upcoming = dict(zip(sources.PRODUCT_KEYS, sources.PRODUCT, strict=True))
    upcoming.update(matchSeq=3673, protoStatus="2", gameReject="0", buyReject="0", live="N",
                    unsetSchedule="N", unsetEndDate="N", gameDate=millis(TIP), endDate=millis(TIP),
                    homeId="10", awayId="66", winHandi=1.5, loseHandi=-1.5, drawHandi=0,
                    drawAllot=0, winTxt="승", drawTxt="-", loseTxt="패", gameSubject=None,
                    winAllot=1.74, loseAllot=1.74, gameResult=None, mchScore=None)
    past_tip = TIP - timedelta(days=1)
    result = dict(upcoming, matchSeq=3623, protoStatus="4", winAllot=1.79, loseAllot=1.69,
                  gameDate=millis(past_tip), endDate=millis(past_tip), homeId="35", awayId="06",
                  winHandi=-2.5, loseHandi=2.5, gameResult="0", mchScore="82.5:78")
    _, quote = price()[0]
    models = {name: {"selection": "home", "odds": 1.79} for name in cycle.MODELS}
    record = {"game": past, "quote": dict(quote, offer_id=f"G101:{ROUND}:3623", line=-2.5, odds=[1.79, 1.69]),
              "models": models}
    cycle.write(tmp_path / "predictions" / f"{PAST_ID}.json", cycle.json_body(record))
    monkeypatch.setattr(cycle.time, "sleep", lambda _: None)
    return schedule, upcoming, result


def listing(*round_ids):
    return {"protoGames": [{"gmId": "G101", "gmTs": int(round_id), "gmOsidTsYear": 2026,
                            "saleEndDate": millis(SALE_END)} for round_id in round_ids]}


@pytest.mark.parametrize("failure", [requests.ConnectionError("connection reset"), 500])
def test_sale_listing_transport_failure_allows_official_settlement(tmp_path, official, failure):
    schedule, upcoming, result = official
    session = Mock()
    sale_response = response(None, failure) if isinstance(failure, int) else failure
    session.request.side_effect = [response(schedule), sale_response,
                                   response(detail(ROUND, upcoming, result))]

    report = cycle.cycle(tmp_path, session, lambda: AT)

    assert report["capture_error"]["stage"] == "listing"
    assert report["captured"] == []
    assert report["settled_now"] == [PAST_ID]
    grade = cycle.records(tmp_path, "settlements")[PAST_ID]
    assert (grade["status"], grade["score"], grade["official_adjusted_score"]) == (
        "settled", [85, 78], "82.5:78")
    assert session.request.call_args.args == ("POST", sources.DETAIL_URL)


def test_failed_sale_detail_discards_partial_offers_and_settles_other_round(tmp_path, official):
    schedule, upcoming, result = official
    # One detail is good, another active round is unavailable. The earlier offer
    # cannot be frozen because uniqueness across the active rounds is unknown.
    failed_round = "260134"
    session = Mock()
    session.request.side_effect = [response(schedule), response(listing(ROUND, failed_round)),
                                   response(detail(ROUND, upcoming, result)),
                                   requests.exceptions.ChunkedEncodingError("truncated body")]

    report = cycle.cycle(tmp_path, session, lambda: AT)

    assert report["capture_error"]["stage"] == "detail"
    assert report["capture_error"]["round"] == failed_round
    assert report["settled_now"] == [PAST_ID]
    assert not (tmp_path / "predictions" / f"{ID}.json").exists()
    assert session.request.call_count == 4


@pytest.mark.parametrize("sale_detail", [True, False])
def test_failed_detail_counts_towards_budget_without_blocking_other_round(tmp_path, official, monkeypatch,
                                                                         sale_detail):
    schedule, _, result = official
    records = cycle.records(tmp_path, "predictions")
    prior_id, prior_round = "S49G01N61", "260132"
    older_id, older_round = "S49G01N57", "260131"
    schedule.extend([fixture(61, "20261108", "50", "16", final=True),
                     fixture(57, "20261107", "35", "16", final=True)])
    games = sources.schedule(json.dumps(schedule))
    for gid, round_id, seq in [(prior_id, prior_round, 3573), (older_id, older_round, 3523)]:
        record = dict(records[PAST_ID], game=games[gid],
                      quote=dict(records[PAST_ID]["quote"], offer_id=f"G101:{round_id}:{seq}"))
        cycle.write(tmp_path / "predictions" / f"{gid}.json", cycle.json_body(record))
    prior_tip = TIP - timedelta(days=2)
    prior_result = dict(result, matchSeq=3573, homeId="50", awayId="16",
                        gameDate=millis(prior_tip), endDate=millis(prior_tip), mchScore="87.5:80")
    session = Mock()
    session.request.side_effect = [response(schedule), response(listing(ROUND) if sale_detail else listing()),
                                   requests.Timeout("detail timed out"),
                                   response(detail(prior_round, prior_result))]
    monkeypatch.setattr(cycle, "MAX_REQUESTS", 4)

    report = cycle.cycle(tmp_path, session, lambda: AT)

    if sale_detail:
        assert report["capture_error"]["round"] == ROUND
    else:
        assert report["settlement_errors"] == {ROUND: "Timeout: detail timed out"}
        assert "capture_error" not in report
    assert report["settled_now"] == [prior_id]
    assert [call.kwargs["json"]["gmTs"] for call in session.request.call_args_list[2:]] == [
        ROUND, prior_round]


def test_cli_records_degraded_capture_and_requests_retry(tmp_path, official, monkeypatch, capsys):
    schedule, upcoming, result = official
    session = MagicMock()
    session.__enter__.return_value = session
    session.request.side_effect = [response(schedule), response(None, 500),
                                   response(detail(ROUND, upcoming, result))]
    run_cycle = cycle.cycle
    monkeypatch.setattr(cycle, "cycle", lambda state, session: run_cycle(state, session, lambda: AT))
    monkeypatch.setattr(cycle, "now", lambda: AT)
    monkeypatch.setattr(cycle.requests, "Session", lambda: session)
    monkeypatch.setattr(sys, "argv", ["cycle", "--state", str(tmp_path), "--scheduled"])

    with pytest.raises(SystemExit) as exc:
        cycle.main()

    assert exc.value.code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["capture_error"]["stage"] == "listing"
    assert output["settled_now"] == [PAST_ID]
    assert json.loads((tmp_path / "last-run.json").read_bytes()) == {**output, "ran_at": AT.isoformat()}


@pytest.mark.parametrize("status", [403, 429])
def test_sale_access_stop_blocks_all_further_requests(tmp_path, official, status):
    schedule, _, _ = official
    session = Mock()
    session.request.side_effect = [response(schedule), response(None, status)]

    with pytest.raises(ValueError, match=f"HTTP {status}; paused"):
        cycle.cycle(tmp_path, session, lambda: AT)
    with pytest.raises(ValueError, match="source access is paused"):
        cycle.cycle(tmp_path, session, lambda: AT)

    stop = json.loads((tmp_path / "STOP_403_429.json").read_bytes())
    assert stop["status"] == status
    assert session.request.call_count == 2
    assert not cycle.records(tmp_path, "settlements")


def test_invalid_official_detail_fails_closed(tmp_path, official):
    schedule, upcoming, result = official
    session = Mock()
    session.request.side_effect = [response(schedule), response(listing(ROUND)),
                                   response(detail("260134", upcoming, result))]

    with pytest.raises(ValueError, match="detail round identity mismatch"):
        cycle.cycle(tmp_path, session, lambda: AT)

    assert not (tmp_path / "predictions" / f"{ID}.json").exists()
    assert not cycle.records(tmp_path, "settlements")
