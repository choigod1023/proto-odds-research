from datetime import UTC, datetime

import pytest

from src.live_market_refresh import (record_live_market_revisions,
                                     refresh_document)
from src.prediction_runtime import PredictionRuntime
from prediction_ledger import PredictionLedgerError


def _live_odds():
    return {
        "generated_at": "2026-08-30T01:00:00+00:00",
        "markets": {"102": {
            "7100": {
                "game_no": "7100", "date": "08.30(일) 18:00",
                "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
                "market": "승패", "label": "", "n_way": 2,
                "odds": [1.55, 2.05], "result": "경기전",
            },
            "7101": {
                "game_no": "7101", "date": "08.30(일) 18:00",
                "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
                "market": "언더오버", "label": "U 10.5", "n_way": 2,
                "odds": [1.70, 1.82], "result": "경기전",
            },
        }},
    }


def test_refresh_hydrates_unpriced_game_and_updates_document_clock():
    document = {"generated_at": "2026-08-28T00:00:00+00:00", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "배당대기", "options": [],
    }], "past": []}

    refreshed, changed = refresh_document(document, _live_odds())

    game = refreshed["live"][0]
    assert changed == 1
    assert refreshed["generated_at"] == _live_odds()["generated_at"]
    assert game["status"] == "경기전"
    assert len(game["options"]) == 4
    assert game["decision_snapshot"]["action"] == "market_reference"
    assert game["decision_snapshot"]["probability"]["market"] is not None


def test_changed_live_decision_is_recorded_before_publish(tmp_path):
    document = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "배당대기", "options": [],
    }], "past": []}
    refreshed, changed = refresh_document(document, _live_odds())
    assert changed == 1

    runtime = PredictionRuntime(
        tmp_path / "pregame.jsonl",
        clock=lambda: datetime(2026, 8, 30, 1, 5, tzinfo=UTC),
    )
    counts = record_live_market_revisions(
        refreshed,
        _live_odds()["generated_at"],
        runtime,
    )

    game = refreshed["live"][0]
    records = runtime.records()
    assert counts == {"predictions": 1, "skipped": 0, "withheld": 0}
    assert len(records) == 1
    assert records[0]["record_type"] == "prediction"
    assert records[0]["predictions"]["probability_detail"]["basis"] == "shin_market"
    assert game["prediction_status"] == "recorded_pregame"
    assert game["prediction_record"]["prediction_snapshot_id"] == records[0]["snapshot_id"]


def test_high_price_reference_is_recorded_without_price_only_withhold(tmp_path):
    live_odds = _live_odds()
    live_odds["markets"]["102"] = {
        "7100": {**live_odds["markets"]["102"]["7100"], "odds": [2.20, 2.20]},
    }
    document = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "배당대기", "options": [],
    }], "past": []}
    refreshed, _ = refresh_document(document, live_odds)
    runtime = PredictionRuntime(
        tmp_path / "pregame.jsonl",
        clock=lambda: datetime(2026, 8, 30, 1, 5, tzinfo=UTC),
    )

    counts = record_live_market_revisions(
        refreshed, live_odds["generated_at"], runtime
    )

    game = refreshed["live"][0]
    assert counts == {"predictions": 1, "skipped": 0, "withheld": 0}
    assert runtime.records()[0]["predictions"]["action"] == "market_reference"
    assert game["prediction_status"] == "recorded_pregame"
    assert game["prediction_revision_id"] == runtime.records()[0]["snapshot_id"]
    assert game["prediction_record"]["odds"] == 2.20


def test_older_live_feed_cannot_replace_newer_ledger_revision(tmp_path):
    runtime = PredictionRuntime(
        tmp_path / "pregame.jsonl",
        clock=lambda: datetime(2026, 8, 30, 2, 5, tzinfo=UTC),
    )
    base = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "배당대기", "options": [],
    }], "past": []}
    newer_odds = _live_odds()
    newer_odds["generated_at"] = "2026-08-30T02:00:00+00:00"
    newer, _ = refresh_document(base, newer_odds)
    record_live_market_revisions(newer, newer_odds["generated_at"], runtime)

    older_odds = _live_odds()
    older_odds["generated_at"] = "2026-08-30T01:00:00+00:00"
    older_odds["markets"]["102"]["7100"]["odds"] = [1.60, 2.00]
    older_base = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "배당대기", "options": [],
    }], "past": []}
    older, _ = refresh_document(older_base, older_odds)

    with pytest.raises(PredictionLedgerError, match="timestamp regressed"):
        record_live_market_revisions(older, older_odds["generated_at"], runtime)
    assert len(runtime.records()) == 1


def test_post_kickoff_live_row_is_withheld_without_blocking_publish(tmp_path):
    live_odds = _live_odds()
    live_odds["generated_at"] = "2026-08-30T10:00:00+00:00"
    document = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "배당대기", "options": [],
    }], "past": []}
    refreshed, _ = refresh_document(document, live_odds)
    runtime = PredictionRuntime(
        tmp_path / "pregame.jsonl",
        clock=lambda: datetime(2026, 8, 30, 10, 5, tzinfo=UTC),
    )

    counts = record_live_market_revisions(
        refreshed, live_odds["generated_at"], runtime
    )

    game = refreshed["live"][0]
    assert counts == {"predictions": 0, "skipped": 0, "withheld": 1}
    assert runtime.records() == []
    assert game["prediction_status"] == "withheld_unrecorded_live_revision"
    assert game["_liveOddsChanged"] is True
    assert "decision_snapshot" not in game


def test_new_game_year_uses_kst_at_utc_new_year_boundary():
    live_odds = _live_odds()
    live_odds["generated_at"] = "2025-12-31T15:10:00+00:00"
    for value in live_odds["markets"]["102"].values():
        value["date"] = "01.01(목) 18:00"
    document = {"generated_at": "old", "rounds": [], "live": [], "past": []}

    refreshed, changed = refresh_document(document, live_odds)

    assert changed == 1
    assert refreshed["live"][0]["year"] == 2026


def test_refresh_adds_new_round_game_without_structure_model():
    document = {"generated_at": "old", "rounds": [], "live": [], "past": []}

    refreshed, changed = refresh_document(document, _live_odds())

    assert changed == 1
    assert refreshed["rounds"] == [102]
    assert refreshed["live"][0]["no_model"] is True
    assert refreshed["live"][0]["추천"] is not None


def test_refresh_recovers_mlb_odds_after_result_without_backfilling_prediction():
    document = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 02:05",
        "sport": "bs", "league": "MLB", "home": "뉴욕양키", "away": "보스레드",
        "status": "배당대기", "options": [],
        "decision_snapshot": {"action": "old"},
    }], "past": []}
    live_odds = {
        "generated_at": "2026-08-30T03:00:00+00:00",
        "markets": {"102": {"7583": {
            "game_no": "7583", "date": "08.30(일) 02:05",
            "sport": "bs", "league": "MLB", "home": "뉴욕양키", "away": "보스레드",
            "market": "승패", "label": "", "odds": [1.63, 1.91], "result": "홈패",
        }}},
    }

    refreshed, changed = refresh_document(document, live_odds)

    game = refreshed["live"][0]
    assert changed == 1
    assert game["status"] == "결과확인"
    assert [option["배당"] for option in game["options"]] == [1.63, 1.91]
    assert game["odds_recovered_after_start"] is True
    assert game["prediction_status"] == "prediction_ledger_required"
    assert "decision_snapshot" not in game


def test_post_kickoff_rows_never_replace_existing_pregame_decision():
    original_options = [{
        "market": "승패", "label": "", "게임번호": "7583",
        "선택": "홈", "배당": 1.75, "selection_id": "sel_saved",
    }]
    document = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 02:05",
        "sport": "bs", "league": "MLB", "home": "뉴욕양키", "away": "보스레드",
        "status": "경기전", "options": original_options,
        "prediction_record": {"selection_id": "sel_saved", "result": "pending"},
        "prediction_status": "recorded_pregame",
    }], "past": []}
    live_odds = {
        "generated_at": "2026-08-30T03:00:00+00:00",
        "markets": {"102": {"7583": {
            "game_no": "7583", "date": "08.30(일) 02:05", "sport": "bs",
            "league": "MLB", "home": "뉴욕양키", "away": "보스레드",
            "market": "승패", "label": "", "odds": [1.63, 1.91], "result": "홈패",
        }}},
    }

    refreshed, changed = refresh_document(document, live_odds)

    game = refreshed["live"][0]
    assert changed == 0
    assert game["options"] == original_options
    assert game["prediction_status"] == "recorded_pregame"
    assert game["prediction_record"]["selection_id"] == "sel_saved"
    assert "_liveOddsChanged" not in game


def test_refresh_does_not_resurrect_old_finished_game_missing_from_document():
    document = {"generated_at": "old", "rounds": [], "live": [], "past": []}
    live_odds = {
        "generated_at": "2026-08-30T03:00:00+00:00",
        "markets": {"102": {"7583": {
            "game_no": "7583", "date": "08.30(일) 02:05",
            "sport": "bs", "league": "MLB", "home": "뉴욕양키", "away": "보스레드",
            "market": "승패", "label": "", "odds": [1.63, 1.91], "result": "홈패",
        }}},
    }

    refreshed, changed = refresh_document(document, live_odds,
                                          now=datetime(2026, 9, 2, tzinfo=UTC))

    assert changed == 0
    assert refreshed["live"] == []


@pytest.mark.parametrize('sport', ['bs', 'bk', 'vl'])
def test_outage_recovers_today_finished_sports_without_pregame_prediction(tmp_path, sport):
    document = {'live': [], 'past': []}
    row = dict(game_no='1', date='10.18(일) 14:00', sport=sport, league='test',
               home='홈', away='원정', market='승패', label='', n_way=2,
               odds=[1.63, 1.91], result='홈패')
    feed = {'generated_at': '2026-10-18T11:00:00Z', 'markets': {'124': {'1': row}}}
    refreshed, changed = refresh_document(document, feed)
    assert changed == 1
    game = refreshed['live'][0]
    assert game['sport'] == sport and game['status'] == '결과확인'
    assert game['official_event_status'] == 'RESULT'
    assert game['추천'] is None
    assert game['prediction_status'] == 'prediction_ledger_required'
    assert 'decision_snapshot' not in game and 'prediction_record' not in game
    runtime = PredictionRuntime(tmp_path / 'ledger.jsonl')
    assert record_live_market_revisions(refreshed, feed['generated_at'], runtime)['predictions'] == 0
    assert list(runtime.records()) == []
    assert refresh_document(refreshed, feed)[1] == 0
    assert len(refreshed['live']) == 1


@pytest.mark.parametrize('market,result,finished', [
    ('승패', '홈패', True), ('승⑤패', '⑤', True),
    ('전반승패', '홈승', False), ('언더오버', '오버', False),
    ('승패', '취소', False), ('승패', '알수없음', False),
])
def test_recovered_display_result_does_not_rewrite_frozen_options(market, result, finished):
    import copy
    row = dict(game_no='1', date='10.18(일) 14:00', sport='bk', league='KBL',
               home='홈', away='원정', market='승패', n_way=2,
               odds=[1.63, 1.91], result='홈패')
    feed = {'generated_at': '2026-10-18T11:00:00Z', 'markets': {'124': {'1': row}}}
    document, _ = refresh_document({'live': [], 'past': []}, feed)
    game = document['live'][0]
    game.pop('official_event_status')
    game.pop('official_event_status_source')
    before = copy.deepcopy(game)
    row.update(market=market, result=result, odds=[2.0, 1.5])
    if market == '승⑤패':
        row.update(n_way=3, odds=[2.0, 2.5, 3.0])
    _, changed = refresh_document(document, feed)
    assert changed == int(finished)
    assert (game.get('official_event_status') == 'RESULT') == finished
    assert game['options'] == before['options']
    assert game['추천'] is None and 'prediction_record' not in game
    assert 'decision_snapshot' not in game
    assert refresh_document(document, feed)[1] == 0


def test_finished_recovery_preserves_archived_game_and_kst_window():
    import copy
    row = dict(game_no='1', date='10.18(일) 14:00', sport='bs', league='KBO',
               home='홈', away='원정', market='승패', n_way=2,
               odds=[1.63, 1.91], result='홈패')
    feed = {'generated_at': '2026-10-18T15:10:00Z', 'markets': {'124': {'1': row}}}
    # KST midnight: yesterday remains visible, but dates older than yesterday do not.
    recovered, changed = refresh_document({'live': [], 'past': []}, feed)
    assert changed == 1
    archived = copy.deepcopy(recovered['live'][0])
    archived['prediction_record'] = {'snapshot_id': 'frozen', 'result': 'hit'}
    document = {'live': [], 'past': [archived]}
    baseline = copy.deepcopy(document)
    assert refresh_document(document, feed)[1] == 0
    assert document == baseline
    row['date'] = '10.17(토) 14:00'
    assert refresh_document({'live': [], 'past': []}, feed)[1] == 0


def test_refresh_recalculates_when_total_line_changes_without_price_change():
    document = {"generated_at": "old", "rounds": [102], "live": [{
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "경기전", "options": [
            {"market": "언더오버", "label": "U 10.5", "line": 10.5,
             "게임번호": "7101", "선택": "언더", "배당": 1.70},
            {"market": "언더오버", "label": "U 10.5", "line": 10.5,
             "게임번호": "7101", "선택": "오버", "배당": 1.82},
        ],
    }], "past": []}
    live_odds = _live_odds()
    live_odds["markets"]["102"] = {"7101": {
        **live_odds["markets"]["102"]["7101"], "label": "U 11.5",
    }}

    refreshed, changed = refresh_document(document, live_odds)

    game = refreshed["live"][0]
    assert changed == 1
    assert {option["line"] for option in game["options"]} == {11.5}
    assert game["market_line_changed"] is True
    assert game["market_line_revision"]["before"][0]["line"] == 10.5
    assert game["market_line_revision"]["after"][0]["line"] == 11.5
    assert game["decision_snapshot"]["action"] == "market_reference"


def _pinned_pregame_game():
    """첫 게시 때 원장에 고정된 사전 픽이 붙은 live 경기."""
    return {
        "year": 2026, "round": 102, "date": "08.30(일) 18:00",
        "sport": "bs", "league": "KBO", "home": "KIA", "away": "SSG",
        "status": "경기전",
        "prediction_status": "recorded_pregame",
        "decision_snapshot": {
            "action": "market_reference", "selection_id": "sel_pinned",
            "as_of": "2026-08-29T00:00:00+00:00",
        },
        "추천": {"market": "승패", "선택": "원정", "label": "", "배당": 2.05,
                "selection_id": "sel_pinned"},
        "prediction_record": {
            "prediction_snapshot_id": "dec_pinned", "selection_id": "sel_pinned",
            "market": "승패", "label": "", "selection": "원정", "odds": 2.05,
            "result": "pending",
        },
        "options": [
            {"market": "승패", "n_way": 2, "label": "", "line": None, "선택": "홈",
             "배당": 2.00, "시장확률": 0.48, "모델확률": None, "최종확률": 0.48,
             "게임번호": "7100", "적중": None},
            {"market": "승패", "n_way": 2, "label": "", "line": None, "선택": "원정",
             "배당": 1.80, "시장확률": 0.52, "모델확률": None, "최종확률": 0.52,
             "게임번호": "7100", "적중": None},
        ],
    }


def test_recorded_pick_gets_a_consistent_new_revision_before_freeze():
    document = {"generated_at": "2026-08-29T00:00:00+00:00", "rounds": [102],
                "live": [_pinned_pregame_game()], "past": []}

    refreshed, changed = refresh_document(document, _live_odds())
    game = refreshed["live"][0]

    snapshot = game["decision_snapshot"]
    assert changed == 1
    assert snapshot["as_of"] == _live_odds()["generated_at"]
    selected = [o for o in game["options"] if o["selection_id"] == snapshot["selection_id"]
                and o["offer_id"] == snapshot["offer_id"]]
    assert len(selected) == 1
    assert selected[0]["시장확률"] == snapshot["probability"]["market"]


def test_pick_drift_clears_when_market_returns_to_the_pinned_side():
    document = {"generated_at": "2026-08-29T00:00:00+00:00", "rounds": [102],
                "live": [_pinned_pregame_game()], "past": []}
    document["live"][0]["pick_drift"] = {"stale": True}

    market_favors_away = {
        "generated_at": "2026-08-30T01:00:00+00:00",
        "markets": {"102": {"7100": {
            "game_no": "7100", "date": "08.30(일) 18:00", "sport": "bs",
            "league": "KBO", "home": "KIA", "away": "SSG",
            "market": "승패", "label": "", "n_way": 2,
            "odds": [2.10, 1.60], "result": "경기전",
        }}},
    }
    refreshed, _ = refresh_document(document, market_favors_away)
    game = refreshed["live"][0]

    assert game["decision_snapshot"]["as_of"] == market_favors_away["generated_at"]
    assert "pick_drift" not in game


def test_freeze_preserves_entire_option_revision():
    from copy import deepcopy
    game = _pinned_pregame_game()
    before = deepcopy(game)
    document = {"live": [game], "past": []}
    feed = _live_odds()
    feed["generated_at"] = "2026-08-30T08:30:00+00:00"
    _, changed = refresh_document(document, feed)
    assert changed == 0
    assert game == before


def test_unchanged_prices_repair_missing_option_ids_before_freeze(tmp_path):
    document = {"live": [_pinned_pregame_game()], "past": []}
    refresh_document(document, _live_odds())
    for option in document["live"][0]["options"]:
        option.pop("selection_id", None)
        option.pop("offer_id", None)
    _, changed = refresh_document(document, _live_odds())
    assert changed == 1
    runtime = PredictionRuntime(tmp_path / "pregame.jsonl",
                                clock=lambda: datetime(2026, 8, 30, 1, 5, tzinfo=UTC))
    counts = record_live_market_revisions(document, _live_odds()["generated_at"], runtime)
    assert counts["predictions"] == 1
    game = document["live"][0]
    assert game["prediction_record"]["prediction_snapshot_id"] == game["decision_snapshot"]["decision_id"]


def test_refresh_releases_runtime_before_persist(tmp_path, monkeypatch):
    import weakref
    import live_market_refresh as module
    document = {"live": [], "past": []}
    references = []

    def runtime_factory(path):
        runtime = PredictionRuntime(tmp_path / "release.jsonl")
        references.append(weakref.ref(runtime))
        return runtime

    monkeypatch.setattr(module, "load_artifact", lambda *args: document)
    monkeypatch.setattr(module, "refresh_document", lambda *args: (document, 1))
    monkeypatch.setattr(module, "PredictionRuntime", runtime_factory)
    monkeypatch.setattr(module, "settle_live_market_results", lambda *args: 0)
    monkeypatch.setattr(module, "record_live_market_revisions", lambda *args: {"predictions": 0})
    persisted = []

    def persist(*args):
        assert references[0]() is None
        persisted.append(args)

    monkeypatch.setattr(module, "persist_artifact", persist)
    assert module.refresh_once({"generated_at": "2026-08-30T02:00:00+00:00"}) == 0
    assert len(persisted) == 1


def test_repricing_appends_revision_without_rewriting_prior_prediction(tmp_path):
    from copy import deepcopy
    runtime = PredictionRuntime(tmp_path / "pregame.jsonl",
                                clock=lambda: datetime(2026, 8, 30, 2, 5, tzinfo=UTC))
    document, _ = refresh_document({"live": [], "past": []}, _live_odds())
    record_live_market_revisions(document, _live_odds()["generated_at"], runtime)
    original = deepcopy(runtime.records()[0])
    feed = _live_odds()
    feed["generated_at"] = "2026-08-30T02:00:00+00:00"
    feed["markets"]["102"]["7100"]["odds"] = [1.65, 1.95]
    refresh_document(document, feed)
    counts = record_live_market_revisions(document, feed["generated_at"], runtime)
    assert counts["predictions"] == 1
    assert runtime.records()[0] == original
    game = document["live"][0]
    assert game["prediction_record"]["prediction_snapshot_id"] == game["decision_snapshot"]["decision_id"]
    assert game["decision_snapshot"]["input_revision_hash"] != original["input_revision_hash"]
