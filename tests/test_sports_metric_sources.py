"""Synthetic response fixtures; live smoke data is deliberately not persisted."""
import copy
import json
import math
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sports_metric_sources import (  # noqa: E402
    MetricSourceSchemaError,
    NAVER_SEASON_SOURCES,
    collect_mlb_expected,
    collect_naver_season_metrics,
)


class Fetch:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload)


def split(**changes):
    return {
        "season": "2026", "sport": {"id": 1}, "gameType": "R",
        "player": {"id": 693821},
        "stat": {"avg": ".252", "slg": ".401", "woba": ".313", "wobaCon": ".351"},
        **changes,
    }


def block(group="pitching", stat_type="expectedStatistics", splits=None):
    return {"type": {"displayName": stat_type}, "group": {"displayName": group},
            "splits": [split()] if splits is None else splits}


def mlb(blocks=None):
    return {"people": [{"id": 693821, "fullName": "Example Player",
                        "stats": [block()] if blocks is None else blocks}]}


def team(**changes):
    return {"teamId": "A", "teamName": "Example Team", "seasonId": "2025",
            "categoryId": "nba", "pointsPerGame": "117.8",
            "fieldGoalThrowSuccessRate": 48.5, **changes}


def naver(rows=None):
    return {"code": 200, "success": True,
            "result": {"seasonTeamStats": [team()] if rows is None else rows}}


def assert_snapshot(row, subject_type):
    assert row["kind"] == "metric_snapshot"
    assert row["scope"] == "season_snapshot"
    assert row["subject_type"] == subject_type
    for key in ("provider", "league", "sport", "subject_id", "name", "season",
                "group", "source_url", "source_type"):
        assert isinstance(row[key], str) and row[key]
    assert isinstance(row["raw_metrics"], dict)
    assert set(row["raw_field_names"]) == set(row["raw_metrics"])
    assert all(value is None or (type(value) in (int, float) and math.isfinite(value))
               for value in row["metrics"].values())
    assert not {"game_id", "match_id", "game_datetime", "home_team", "away_team"} & row.keys()
    json.dumps(row, allow_nan=False)


@pytest.mark.parametrize("reverse", [False, True])
def test_mlb_selects_namespace_and_groups_independent_of_block_order(reverse):
    blocks = [block(stat_type="season", splits=[split(stat={"woba": ".999"})]),
              block("hitting"), block("fielding"), block()]
    if reverse:
        blocks.reverse()
    fetch = Fetch(mlb(blocks))
    rows = collect_mlb_expected(fetch, [693821], 2026, 10)
    assert len(rows) == 2
    by_group = {r["group"]: r for r in rows}
    assert by_group["pitching"]["metrics"] == {
        "xba_against": .252, "xslg_against": .401, "xwoba_against": .313, "xera": None,
    }
    assert by_group["hitting"]["metrics"] == {"xba": .252, "xslg": .401, "xwoba": .313}
    for row in rows:
        assert_snapshot(row, "player")
        assert row["source_type"] == "expectedStatistics"
        assert row["source_metadata"]["type_namespace"] == "expectedStatistics." + row["group"]
        assert "undocumented" in row["source_metadata"]["api_stability"]
        assert "interpreted" in row["source_metadata"]["mapping_interpretation"]
        assert row["raw_metrics"]["wobaCon"] == ".351"
        assert "wobaCon" not in row["source_metadata"]["metric_field_map"].values()
    assert len(fetch.urls) == 1
    query = parse_qs(urlsplit(fetch.urls[0]).query)
    assert query == {"personIds": ["693821"], "hydrate": [
        "stats(group=[hitting,pitching],type=[expectedStatistics],season=2026)"]}


@pytest.mark.parametrize("changes", [
    {"season": "2025"}, {"season": None}, {"season": ""},
    {"sport": {"id": 11}}, {"sport": {}}, {"sport": {"id": True}},
    {"gameType": "S"}, {"gameType": "P"}, {"gameType": None},
    {"player": {"id": 123}},
])
def test_mlb_filters_unverified_scope_and_other_players(changes):
    fetch = Fetch(mlb([block(splits=[split(**changes), split()])]))
    rows = collect_mlb_expected(fetch, [693821], 2026, 1)
    assert len(rows) == 1 and rows[0]["season"] == "2026"
    assert len(fetch.urls) == 1


def test_mlb_missing_season_is_not_filled_from_request_or_block():
    raw = split()
    del raw["season"]
    stats = block(splits=[raw])
    stats["season"] = "2026"
    assert collect_mlb_expected(Fetch(mlb([stats])), [693821], 2026, 5) == []


def test_mlb_batches_ids_once_and_limit_counts_groups_not_players():
    payload = mlb([block("hitting"), block()])
    other = {"id": 123, "fullName": "Other Player", "stats": [block(splits=[split(player={"id": 123})])]}
    unsolicited = {"id": 999, "stats": [block()]}
    payload["people"] = [unsolicited, *payload["people"], other]
    fetch = Fetch(payload)
    rows = collect_mlb_expected(fetch, [693821, 123, 693821], 2026, 1)
    assert len(rows) == 1 and rows[0]["subject_id"] == "693821"
    assert len(fetch.urls) == 1
    assert parse_qs(urlsplit(fetch.urls[0]).query)["personIds"] == ["693821,123"]
    assert len(collect_mlb_expected(Fetch(payload), [693821, 123], 2026, 10)) == 3


@pytest.mark.parametrize("bad", [None, "", "--", "NaN", "Infinity", "-inf", "1e400", True, [], {}, float("nan")])
def test_invalid_metric_values_are_null_and_raw_remains_inspectable(bad):
    pitcher = collect_mlb_expected(Fetch(mlb([block(splits=[split(stat={"woba": bad})])])),
                                   [693821], 2026, 1)[0]
    assert pitcher["metrics"]["xwoba_against"] is None
    assert pitcher["metrics"]["xera"] is None
    assert pitcher["availability"] == "not_available"
    assert "woba" in pitcher["raw_metrics"]
    assert_snapshot(pitcher, "player")
    row = collect_naver_season_metrics(Fetch(naver([team(pointsPerGame=bad)])),
                                       "nba", "2025", "bk", "NBA", 1)[0]
    assert row["metrics"]["pointsPerGame"] is None
    assert_snapshot(row, "team")


def test_nonstandard_json_numbers_and_overflow_do_not_leak_nan_or_infinity():
    text = json.dumps(mlb()).replace('".252"', 'NaN').replace('".401"', 'Infinity').replace('".313"', '1e400')
    row = collect_mlb_expected(Fetch(text), [693821], 2026, 1)[0]
    assert all(v is None for v in row["metrics"].values())
    assert row["raw_metrics"]["avg"] == "NaN"
    assert row["raw_metrics"]["slg"] == "Infinity"
    assert row["raw_metrics"]["woba"] == "1e400"
    assert_snapshot(row, "player")


def test_missing_metrics_stay_unavailable_and_zero_is_available():
    row = collect_mlb_expected(Fetch(mlb([block(splits=[split(stat={})])])), [693821], 2026, 1)[0]
    assert row["availability"] == "not_available"
    assert all(v is None for v in row["metrics"].values())
    raw_team = {"teamId": "A", "teamName": "A", "seasonId": "2025", "categoryId": "nba"}
    row = collect_naver_season_metrics(Fetch(naver([raw_team])), "nba", "2025", "bk", "NBA", 1)[0]
    assert row["availability"] == "not_available"
    assert all(v is None for v in row["metrics"].values())
    assert_snapshot(row, "team")
    raw_team["pointsPerGame"] = 0
    row = collect_naver_season_metrics(Fetch(naver([raw_team])), "nba", "2025", "bk", "NBA", 1)[0]
    assert row["availability"] == "available" and row["metrics"]["pointsPerGame"] == 0


@pytest.mark.parametrize("payload", [{"people": []}, mlb([]), {"people": [{"id": 693821}]}])
def test_mlb_legitimate_no_stats_returns_empty(payload):
    assert collect_mlb_expected(Fetch(payload), [693821], 2026, 10) == []


@pytest.mark.parametrize("payload", [
    "not json", "[]", {}, {"people": None}, {"people": {}}, {"people": [None]},
    {"people": [{"id": 693821, "stats": None}]}, mlb([None]),
    mlb([{"type": {}}]), mlb([{"type": {"displayName": "expectedStatistics"}}]),
    mlb([block(splits={})]), mlb([block(splits=[None])]),
    mlb([block(splits=[split(stat=None)])]), mlb([block(splits=[split(stat=[])])]),
])
def test_mlb_schema_errors_raise(payload):
    with pytest.raises(MetricSourceSchemaError):
        collect_mlb_expected(Fetch(payload), [693821], 2026, 10)


def test_naver_preserves_validated_names_units_and_unknown_raw_fields():
    raw = team(pointsPerGame="117.81234", futureMetric=5, updateDateTime="2026-04-13T14:30:12")
    original = copy.deepcopy(raw)
    fetch = Fetch(naver([raw]))
    row = collect_naver_season_metrics(fetch, "nba", "2025", "bk", "NBA", 5)[0]
    assert_snapshot(row, "team")
    assert row["metrics"]["pointsPerGame"] == 117.81234
    assert row["metrics"]["fieldGoalThrowSuccessRate"] == 48.5
    assert row["metrics"]["turnoverPerGame"] is None
    assert "futureMetric" not in row["metrics"]
    assert row["raw_metrics"] == original
    assert row["source_type"] == "seasonTeamStats"
    assert fetch.urls == ["https://api-gw.sports.naver.com/statistics/categories/nba/seasons/2025/teams"]


@pytest.mark.parametrize("category,source_category,sport", [
    ("nba", "nba", "bk"), ("kbl", "kbl", "bk"), ("wkbl", "wkbl", "bk"),
    ("kovo", "kovo", "vl"), ("wkovo", "wkovo", "vl"),
])
def test_curated_naver_categories_use_validated_source_paths(category, source_category, sport):
    fetch = Fetch(naver([team(categoryId=source_category, seasonId="022")]))
    row = collect_naver_season_metrics(fetch, category, "022", sport, "League", 1)[0]
    assert row["season"] == "022"
    assert f"/categories/{source_category}/seasons/022/teams" in fetch.urls[0]
    assert row["source_metadata"]["source_category"] == source_category
    assert len(fetch.urls) == 1


def test_public_registry_can_be_passed_directly_by_runner():
    assert set(NAVER_SEASON_SOURCES) == {"NBA", "KBL", "WKBL", "KOVO남", "KOVO여"}
    assert NAVER_SEASON_SOURCES["KOVO여"]["category"] == "wkovo"
    for league, spec in NAVER_SEASON_SOURCES.items():
        fetch = Fetch(naver([team(categoryId=spec["category"])]))
        row = collect_naver_season_metrics(fetch, season="2025", limit=1, **spec)[0]
        assert row["league"] == league and row["sport"] == spec["sport"]


def test_volleyball_success_is_never_relabelled_as_efficiency_or_sideout():
    raw = team(categoryId="kovo", seasonId="022", hittingSuccessRate="51.772",
               receiveEfficiency="34.07", receiveSuccessRate=99, setRatio=1.47273,
               hittingEfficiency=88, sideout=77, xg=66)
    row = collect_naver_season_metrics(Fetch(naver([raw])), "kovo", "022", "vl", "KOVO남", 1)[0]
    assert row["metrics"]["hittingSuccessRate"] == 51.772
    assert row["metrics"]["receiveEfficiency"] == 34.07
    assert row["metrics"]["setRatio"] == 1.47273
    assert not {"hittingEfficiency", "receiveSuccessRate", "sideout", "xg", "pointsPerGame"} & row["metrics"].keys()
    raw.pop("receiveEfficiency")
    row = collect_naver_season_metrics(Fetch(naver([raw])), "kovo", "022", "vl", "KOVO남", 1)[0]
    assert row["metrics"]["receiveEfficiency"] is None


def test_naver_filters_season_category_before_output_limit_and_never_defaults_season():
    missing = team()
    del missing["seasonId"]
    rows = [missing, team(seasonId=None), team(seasonId="2024"), team(categoryId="kbl"),
            team(categoryId=None), team(), team(teamId="B")]
    fetch = Fetch(naver(rows))
    result = collect_naver_season_metrics(fetch, "nba", "2025", "bk", "NBA", 1)
    assert len(result) == 1 and result[0]["subject_id"] == "A"
    assert len(fetch.urls) == 1
    assert collect_naver_season_metrics(Fetch(naver(rows[:5])), "nba", "2025", "bk", "NBA", 10) == []
    assert collect_naver_season_metrics(Fetch(naver([])), "nba", "2025", "bk", "NBA", 10) == []


@pytest.mark.parametrize("payload", [
    "<html>error</html>", "null", {}, {"result": None}, {"result": {}},
    {"result": {"seasonTeamStats": None}}, {"result": {"seasonTeamStats": {}}},
    naver([None]), naver([team(teamId=None)]), naver([team(teamName=None)]),
    {**naver(), "success": False}, {**naver(), "code": 500},
])
def test_naver_schema_and_source_errors_raise(payload):
    with pytest.raises(MetricSourceSchemaError):
        collect_naver_season_metrics(Fetch(payload), "nba", "2025", "bk", "NBA", 10)


def test_empty_work_does_not_fetch():
    fetch = Fetch({})
    assert collect_mlb_expected(fetch, [], 2026, 10) == []
    assert collect_mlb_expected(fetch, [693821], 2026, 0) == []
    assert collect_naver_season_metrics(fetch, "nba", "2025", "bk", "NBA", 0) == []
    assert fetch.urls == []


@pytest.mark.parametrize("limit", [-1, True, 1.5, None])
def test_invalid_limits_rejected_without_fetch(limit):
    fetch = Fetch({})
    with pytest.raises(ValueError):
        collect_mlb_expected(fetch, [693821], 2026, limit)
    with pytest.raises(ValueError):
        collect_naver_season_metrics(fetch, "nba", "2025", "bk", "NBA", limit)
    assert fetch.urls == []


@pytest.mark.parametrize("ids,season", [([True], 2026), ([0], 2026), (["693821"], 2026),
                                       (None, 2026), ([693821], None), ([693821], "2026")])
def test_invalid_mlb_inputs_rejected_without_fetch(ids, season):
    fetch = Fetch({})
    with pytest.raises(ValueError):
        collect_mlb_expected(fetch, ids, season, 1)
    assert fetch.urls == []


@pytest.mark.parametrize("category,season,sport,league", [
    ("unknown", "2025", "bk", "NBA"), ("nba", "2025", "vl", "NBA"),
    ("kovo_w", "022", "vl", "KOVO여"),
    ("nba", "", "bk", "NBA"), ("nba", None, "bk", "NBA"),
    ("nba", 2025, "bk", "NBA"), ("nba", " 2025", "bk", "NBA"),
    ("nba", "2025", "bk", ""),
])
def test_invalid_naver_inputs_rejected_without_fetch(category, season, sport, league):
    fetch = Fetch({})
    with pytest.raises(ValueError):
        collect_naver_season_metrics(fetch, category, season, sport, league, 1)
    assert fetch.urls == []


@pytest.mark.parametrize("status", [403, 429])
@pytest.mark.parametrize("source", ["mlb", "naver"])
def test_access_errors_propagate_without_retries_or_alternate_endpoints(status, source):
    calls = []
    error = HTTPError("https://example.invalid/", status, "blocked", {}, None)

    def fetch(url):
        calls.append(url)
        raise error

    with pytest.raises(HTTPError) as raised:
        if source == "mlb":
            collect_mlb_expected(fetch, [693821], 2026, 1)
        else:
            collect_naver_season_metrics(fetch, "nba", "2025", "bk", "NBA", 1)
    assert raised.value is error
    assert len(calls) == 1
