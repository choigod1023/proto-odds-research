from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest
from scripts import featured_price_shadow as shadow

NOW = datetime(2026, 11, 9, 10, tzinfo=timezone.utc)
KICKOFF = NOW + timedelta(hours=1)


def prediction(league):
    odds = [(1.65, "승패", "", None, "홈", "w"),
            (2.10, "승패", "", None, "원정", "wr"),
            (1.30, "핸디캡", "H +2.5", 2.5, "핸디홈", "h"),
            (2.95, "핸디캡", "H +2.5", 2.5, "핸디원정", "hr"),
            (1.46, "언더오버", "U 12.5", 12.5, "언더", "u"),
            (2.55, "언더오버", "U 12.5", 12.5, "오버", "ur")]
    options = [dict(odds=p, market=m, label=l, line=n, selection=s, selection_id=i, offer_id=f"off_{i}")
               for p, m, l, n, s, i in odds]
    published = dict(options[0], settlement_rule="official_winner", version="featured-pregame-v1")
    at = (NOW - timedelta(minutes=1)).isoformat()
    items = [dict(options[i], settlement_rule=rule, automatic_recommendation=True,
                  period="full_game", market_observed_at=at)
             for i, rule in ((2, "handicap_half"), (4, "total_half"))]
    return dict(event_id="e", kickoff=KICKOFF.isoformat(), as_of=at, captured_at=at,
                market_observed_at=at,
                predictions=dict(options[0], featured_pick=published, action="market_reference",
                                 market_recommendations=dict(version="multi-market-recommendations-v1", items=items)),
                features=dict(options=options, fixture=dict(league=league, sport="bk" if league=="NBA" else "bs")))


@pytest.mark.parametrize(("league", "expected"), [("NBA", "h"), ("KBO", "u")])
def test_three_arms_share_prices_and_strict_floor(league, expected):
    row = prediction(league)
    picks = shadow.freeze(row, NOW)["arms"]
    assert picks[shadow.BASE]["selection_id"] == "w"
    assert picks[shadow.ARMS[league]]["selection_id"] == expected
    assert picks[shadow.LOW]["selection_id"] == "u"
    with pytest.raises(ValueError, match="cutoff"):
        shadow.freeze(row, KICKOFF - timedelta(minutes=30))
    row["predictions"]["featured_pick"]["odds"] = 1.55
    with pytest.raises(ValueError, match="could not be reproduced"):
        shadow.freeze(row, NOW)


def test_capture_settlement_revision_and_no_backfill(tmp_path):
    source = sqlite3.connect(":memory:")
    source.execute("CREATE TABLE prediction_records(ledger_sequence INTEGER PRIMARY KEY, "
                   "record_type TEXT, snapshot_id TEXT, record_json TEXT)")
    state = shadow.database(tmp_path / "paper.sqlite3")
    row = prediction("NBA")
    source.execute("INSERT INTO prediction_records VALUES(1,'prediction','s',?)", (json.dumps(row),))
    seq, started = shadow.process(source, state, NOW)
    first = shadow.report(state, NOW, seq, started)["comparisons"]["NBA"]
    assert first["probability_first"]["pending"] == 1
    pick = shadow.freeze(row, NOW)["arms"]["probability_first"]
    settled = KICKOFF + timedelta(hours=2)
    outcome = dict(selection_id="w", result="miss",
                   market_recommendation_results={"off_h": pick | {"result": "hit"}})
    source.execute("INSERT INTO prediction_records VALUES(2,'settlement','s',?)",
                   (json.dumps(dict(settled_at=settled.isoformat(), outcome=outcome)),))
    seq, started = shadow.process(source, state, settled)
    stats = shadow.report(state, settled, seq, started)["comparisons"]["NBA"]["probability_first"]
    assert stats["common_settled"] == 1
    assert stats["baseline"]["roi"] == -1
    assert stats["alternative"]["roi"] == 0.3

    outcome["market_recommendation_results"]["off_h"]["line"] = -1.5
    source.execute("INSERT INTO prediction_records VALUES(3,'settlement','s',?)",
                   (json.dumps(dict(settled_at=settled.isoformat(), outcome=outcome)),))
    late = row | {"event_id": "missed"}
    source.execute("INSERT INTO prediction_records VALUES(4,'prediction','late',?)", (json.dumps(late),))
    seq, started = shadow.process(source, state, settled)
    stats = shadow.report(state, settled, seq, started)["comparisons"]["NBA"]["probability_first"]
    assert stats["captured"] == stats["pending"] == 1
    assert stats["common_settled"] == 0
    source.close()
    state.close()
