from match_progress import named_match_progress


def test_period_scores_preserve_zero_missing_extra_and_duplicate():
    raw = {"gameStatus": "FINAL", "teams": {
        "home": {"periodData": [{"period":1,"score":0},{"period":10,"score":2},{"period":2,"score":True}]},
        "away": {"periodData": [{"period":1,"score":1},{"period":1,"score":1},{"period":10,"score":None}]}}}
    assert named_match_progress(raw,"baseball")["period_scores"] == [
        {"period":1,"home":0,"away":None},{"period":2,"home":None,"away":None},{"period":10,"home":2,"away":None}]
    raw["gameStatus"] = "READY"
    assert named_match_progress(raw,"baseball") == {}


def test_events_keep_provider_order_dedupe_and_ignore_unreliable_terminal_clock():
    goal={"playText":"선수 득점","eventType":"GOAL","displayTime":"01:07","period":2,"locationType":"AWAY"}
    end={"playText":"경기 종료","eventType":"FULLTIME","displayTime":"12:00","period":2}
    result=named_match_progress({"broadcasts":[goal,goal,end]},"soccer")
    assert len(result["timeline"]) == 2
    assert result["timeline"][0]["time"] == "67′"
    assert result["timeline"][0]["side"] == "away"
    assert "time" not in result["timeline"][1]
    assert "score" not in result["timeline"][0]  # upstream non-goal scores can reset to 0


def test_latest_event_is_not_presented_as_full_history():
    result=named_match_progress({"broadcast":{"playText":"득점","period":2,"displayTime":"00:00"}},"volleyball")
    assert result["timeline_scope"] == "latest"
    assert "time" not in result["timeline"][0]
    assert named_match_progress({},"basketball")["timeline"] == []
