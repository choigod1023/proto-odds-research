import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from runtime_db import RuntimeDatabase
from sports_history import SportsHistoryStore
from sports_history_collect import FetchClient, FetchError, run_collection
from export_runtime import export_csv


@pytest.fixture
def store(tmp_path):
    return SportsHistoryStore(RuntimeDatabase(tmp_path / "sports.sqlite3"))


def game(event="1", **changes):
    return {"provider": "test", "event_id": event, "league": "K1", "sport": "축구",
        "home_id": "h", "away_id": "a", "home_name": "홈", "away_name": "원정",
        "kickoff_at": "2026-09-01T10:00:00Z", "status": "final", "home_score": 2,
        "away_score": 1, "score_unit": "goals", "metrics": {"home": {"xg": 1.8}, "away": {"xg": .9}}, **changes}


def test_repeat_correction_cancellation_and_knowledge_cutoff(store):
    initial = game()
    assert store.record_games([initial], observed_at="2026-09-01T12:00:00Z") == 1
    assert store.record_games([initial], observed_at="2026-09-01T13:00:00Z") == 0
    assert store.team_form("test", "K1", "h", as_of="2026-09-01T11:00:00Z")["summary"]["games"] == 0
    corrected = game(home_score=0)
    assert store.record_games([corrected], observed_at="2026-09-02T00:00:00Z") == 1
    assert store.team_form("test", "K1", "h", as_of="2026-09-01T15:00:00Z")["summary"]["wins"] == 1
    assert store.team_form("test", "K1", "h", as_of="2026-09-02T01:00:00Z")["summary"]["losses"] == 1
    store.record_games([game(status="cancelled")], observed_at="2026-09-02T02:00:00Z")
    assert store.team_form("test", "K1", "h")["summary"]["games"] == 0
    assert len(store.db.events("sports_history")) == 3


def test_doubleheaders_are_distinct_ids_and_sql_averages_only_available_metrics(store):
    store.record_games([game(), game("2", kickoff_at="2026-09-01T15:00:00Z", metrics={})],
                       observed_at="2026-09-02T00:00:00Z")
    result = store.team_form("test", "K1", "h", limit=10)
    assert result["summary"]["games"] == 2
    assert result["metrics"] == [{"name": "xg", "samples": 1, "mean": 1.8}]
    assert result["recent_games"][0]["event_id"] == "2"
    away = store.team_form("test", "K1", "a", limit=1)
    assert away["summary"]["losses"] == 1
    assert away["summary"]["avg_scored"] == 1
    assert away["metrics"] == []


def test_sources_with_same_event_or_team_id_never_mix(store):
    store.record_games([game(), game(provider="other", home_score=5)], observed_at="2026-09-02T00:00:00Z")
    assert store.team_form("test", "K1", "h")["summary"]["avg_scored"] == 2
    assert store.team_form("other", "K1", "h")["summary"]["avg_scored"] == 5


def test_team_correction_does_not_resurrect_old_identity(store):
    store.record_games([game()], observed_at="2026-09-01T12:00:00Z")
    store.record_games([game(home_id="h2")], observed_at="2026-09-01T13:00:00Z")
    assert store.team_form("test", "K1", "h")["summary"]["games"] == 0


@pytest.mark.parametrize("changes", [
    {"home_score": True}, {"home_score": -1}, {"home_score": None}, {"home_score": 1.5},
    {"status": "live"}, {"kickoff_at": "2027-01-01T00:00:00Z"},
    {"kickoff_at": "2026-09-01T10:00:00"}, {"home_id": "a"},
    {"metrics": {"home": {"xg": float("nan")}}},
    {"metrics": {"home": {"xg": -1}}}, {"metrics": {"home": {"xg": True}}},
    {"sport": "야구", "score_unit": "runs"}, {"score_unit": "points"},
])
def test_invalid_batches_are_atomic_and_never_write_ledger(store, changes):
    with pytest.raises(ValueError):
        store.record_games([game("good"), game("bad", **changes)], observed_at="2026-09-02T00:00:00Z")
    assert store.inventory() == []
    assert store.db.events("sports_history") == []


def test_date_only_preserves_unknown_kickoff_and_does_not_backdate_availability(store):
    store.record_games([game(kickoff_at=None, game_date="2024-07-14")], observed_at="2026-09-02T00:00:00Z")
    assert store.team_form("test", "K1", "h", as_of="2024-07-15T00:00:00Z")["summary"]["games"] == 0
    row = store.team_form("test", "K1", "h")["recent_games"][0]
    assert row["time_precision"] == "date" and row["kickoff_at"] is None


def test_utc_normalization_out_of_order_and_explicit_csv(store, tmp_path):
    store.record_games([game()], observed_at="2026-09-02T09:00:00+09:00")
    with pytest.raises(ValueError, match="out-of-order"):
        store.record_games([game(home_score=3)], observed_at="2026-09-01T15:00:00Z")
    target = tmp_path / "requested.csv"
    assert not target.exists()
    assert export_csv(store.db.path, target, kind="events", name="sports_history") == 1
    assert target.exists()
    assert not list(tmp_path.glob("*.jsonl"))


def test_season_snapshots_remain_separate_from_recent_games(store):
    row = {"kind": "metric_snapshot", "provider": "mlb", "league": "MLB", "sport": "야구",
        "subject_id": "693821", "subject_type": "player", "season": "2026", "group": "pitching",
        "scope": "season_snapshot", "metrics": {"xwoba_against": .313, "xera": None}}
    assert store.record_metric_snapshots([row], observed_at="2026-09-02T00:00:00Z") == 1
    assert store.record_metric_snapshots([row], observed_at="2026-09-02T01:00:00Z") == 0
    assert store.metric_snapshots(as_of="2026-09-01T23:00:00Z") == []
    assert store.metric_snapshots()[0]["metrics"]["xera"] is None
    assert store.inventory() == []
    assert len(store.db.events("sports_metric_snapshots")) == 1


def test_failed_collection_preserves_old_results_and_reports_error(store, monkeypatch):
    import sports_history_collect
    from datetime import date
    store.record_games([game()], observed_at="2026-09-02T00:00:00Z")
    def broken(*args, **kwargs):
        raise ValueError("HTML response instead of JSON")
    monkeypatch.setattr(sports_history_collect, "collect_source", broken)
    result = run_collection(store.db, ["test"], since=date(2026, 9, 1), until=date(2026, 9, 2), client=lambda _: "")
    assert result["runs"][0]["status"] == "failed"
    assert store.team_form("test", "K1", "h")["summary"]["games"] == 1


def test_fetcher_rejects_unapproved_and_fotmob_api_before_any_request(store):
    client = FetchClient(store.db, interval=0)
    for url in ["http://statsapi.mlb.com/", "https://www.fotmob.com/api/matchDetails", "https://localhost/private",
                "https://www.fotmob.com/%61pi/matchDetails"]:
        with pytest.raises(FetchError):
            client(url)
    assert client.calls == 0


class Response:
    def __init__(self, code, content=b"{}", headers=None):
        self.status_code = code
        self.headers = headers or {"Content-Type": "application/json"}
        self.content = content
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def iter_content(self, _size):
        yield self.content


class Session:
    def __init__(self, response):
        self.headers = {}
        self.response = response
        self.calls = 0
    def get(self, *args, **kwargs):
        self.calls += 1
        return self.response


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_failures_are_logged_once_without_retry_or_erasing_data(store, status):
    session = Session(Response(status))
    client = FetchClient(store.db, session=session, interval=0)
    with pytest.raises(FetchError, match=str(status)):
        client("https://statsapi.mlb.com/api/v1/schedule")
    assert session.calls == 1
    assert store.db.events("sports_fetches")[0]["status"] == "failed"


def test_fetch_raw_response_preserved_in_db_and_budget_enforced(store):
    client = FetchClient(store.db, session=Session(Response(200, b'{"games":[]}')),
                         interval=0, max_requests=1)
    assert json.loads(client("https://statsapi.mlb.com/api/v1/schedule")) == {"games": []}
    audit = store.db.events("sports_fetches")[0]
    raw = store.db.get_document(audit["response_document"])
    assert raw["body"] == '{"games":[]}'
    with pytest.raises(FetchError, match="budget"):
        client("https://statsapi.mlb.com/api/v1/schedule")


def test_redirect_target_is_validated_before_following(store):
    session = Session(Response(302, headers={"Location": "https://localhost/secrets"}))
    with pytest.raises(FetchError, match="unapproved"):
        FetchClient(store.db, session=session, interval=0)("https://statsapi.mlb.com/api/v1/schedule")
    assert session.calls == 1


def test_future_cancellation_can_invalidate_but_never_add_form(store):
    store.record_games([game(status="cancelled", kickoff_at="2026-09-03T10:00:00Z")],
                       observed_at="2026-09-02T00:00:00Z")
    assert store.team_form("test", "K1", "h")["summary"]["games"] == 0
    row = store.db.events("sports_history")[0]
    assert row["metrics"] == {} and row["home_score"] is None


def test_dispatch_preserves_naver_season_zeroes_and_source_sport_codes(monkeypatch):
    from datetime import date
    from sports_history_collect import collect_source
    import sports_metric_sources
    captured = {}
    def collect(fetch, **kwargs):
        captured.update(kwargs)
        return []
    monkeypatch.setattr(sports_metric_sources, "collect_naver_season_metrics", collect)
    collect_source("naver-stats:KOVO여", lambda _: "", since=date(2026, 9, 1),
                   until=date(2026, 9, 2), limit=3, season="022")
    assert captured == {"category": "wkovo", "sport": "vl", "league": "KOVO여", "season": "022", "limit": 3}


def test_cli_preserves_season_id(tmp_path, monkeypatch):
    import sports_history_collect
    captured = {}
    def collect(db, sources, **kwargs):
        captured.update(kwargs)
        return {"runs": []}
    monkeypatch.setattr(sports_history_collect, "run_collection", collect)
    assert sports_history_collect.main(["collect", "--db", str(tmp_path / "db.sqlite3"),
        "--source", "naver-stats:KOVO남", "--season", "022"]) == 0
    assert captured["season"] == "022"


def test_season_report_never_claims_game_date_coverage(store, monkeypatch):
    from datetime import date
    import sports_history_collect
    monkeypatch.setattr(sports_history_collect, "collect_source", lambda *a, **kw: [])
    result = run_collection(store.db, ["naver-stats:NBA"], since=date(2026, 9, 1),
                            until=date(2026, 9, 2), season="2025", client=lambda _: "")
    row = result["runs"][0]
    assert row["status"] == "no_metrics" and row["season"] == "2025"
    assert row["since"] is None and row["until"] is None and row["latest_game"] is None


def test_real_naver_adapter_output_limit_persists_only_labelled_sample(store):
    from datetime import date
    rows = [{"gameId": str(i), "categoryId": "kbo", "gameDate": "2026-09-01",
        "gameDateTime": "2026-09-01T18:30:00", "statusCode": "RESULT",
        "homeTeamCode": "h", "homeTeamName": "Home", "homeTeamScore": 2,
        "awayTeamCode": "a", "awayTeamName": "Away", "awayTeamScore": 1} for i in range(4)]
    fetch = lambda _: json.dumps({"code": 200, "success": True,
                                  "result": {"games": rows, "gameTotalCount": 4}})
    result = run_collection(store.db, ["naver:KBO"], since=date(2026, 9, 1),
                            until=date(2026, 9, 1), limit=3, client=fetch)
    report = result["runs"][0]
    assert report["status"] == "partial" and report["reason"] == "output_limit"
    assert report["inserted_versions"] == 3
    assert store.team_form("naver", "KBO", "h")["summary"]["games"] == 3


def test_budget_skip_preserves_original_metric_provenance_but_not_corrected_scores(store):
    first = game(source_url="https://statsapi.mlb.com/boxscore", detail_fetch_status="fetched")
    store.record_games([first], observed_at="2026-09-01T12:00:00Z")
    skipped = game(metrics={"home": {}, "away": {}}, metric_status="not_available",
                   source_url="https://statsapi.mlb.com/schedule", detail_fetch_status="not_requested_budget")
    store.record_games([skipped], observed_at="2026-09-02T00:00:00Z")
    result = store.team_form("test", "K1", "h")
    assert result["metrics"][0]["mean"] == 1.8
    row = result["recent_games"][0]
    assert row["metrics_observed_at"] == "2026-09-01T12:00:00.000000+00:00"
    assert row["metrics_source_url"] == first["source_url"]
    assert store.record_games([skipped], observed_at="2026-09-02T01:00:00Z") == 0
    store.record_games([{**skipped, "home_score": 3}], observed_at="2026-09-02T02:00:00Z")
    assert store.team_form("test", "K1", "h")["metrics"] == []


def test_explicit_missing_details_do_not_reuse_stale_metrics(store):
    store.record_games([game()], observed_at="2026-09-01T12:00:00Z")
    store.record_games([game(metrics={}, detail_fetch_status="fetched")], observed_at="2026-09-02T00:00:00Z")
    assert store.team_form("test", "K1", "h")["metrics"] == []
