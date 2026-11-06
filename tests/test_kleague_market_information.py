import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1] / "experiments/kleague-market-information-20261106"
SPEC = importlib.util.spec_from_file_location("kleague_market_information", ROOT / "study.py")
study = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(study)


def test_score_fit_recovers_rates_from_three_coherent_markets():
    rates = np.array([1.60, 0.96])
    p = study.distribution(np.log(rates))
    handicap = study.DIFF - 1
    markets = {
        "winner": (0, study.WINNER),
        "total": (2.5, np.array([study.TOTAL < 2.5, study.TOTAL > 2.5])),
        "handicap": (-1, np.array([handicap > 0, handicap == 0, handicap < 0])),
    }
    quotes = {market: {"line": line, "odds": (1.12 / (groups @ p)).tolist()}
              for market, (line, groups) in markets.items()}
    probabilities, fitted = study.fit_score(quotes, True)
    np.testing.assert_allclose(fitted, rates, atol=1e-6)
    np.testing.assert_allclose(probabilities, study.WINNER @ p, atol=1e-6)
    assert np.isclose(np.sum(probabilities), 1.0)


def test_paired_log_gain_is_evaluated_on_realized_outcome():
    row = {"kickoff_kst": "2026-07-04T19:00:00+09:00", "outcome": 1,
           "quotes": {"winner": {"odds": [2.0, 3.0, 4.0]}},
           "probabilities": {"winner_implied": [0.45, 0.25, 0.30], "poisson_winner": [0.45, 0.25, 0.30],
                             "poisson_three": [0.30, 0.45, 0.25]}}
    out = study.evaluate([row])
    gain = out["three_market_log_gain"]["winner_implied"]
    assert np.isclose(gain["log_gain_nats"], np.log(0.45 / 0.25))
    np.testing.assert_allclose(gain["intervals"]["2"], [np.log(0.45 / 0.25)] * 2)
    assert out["metrics"]["poisson_three"]["roi"] == 2.0


def test_pregame_artifact_preserves_recipe_prices_and_time():
    frozen = json.loads((ROOT / "prospective-260132.json").read_bytes())
    assert frozen["study_sha256"] == hashlib.sha256((ROOT / "study.py").read_bytes()).hexdigest()
    assert frozen["protocol_sha256"] == hashlib.sha256((ROOT / "PROTOCOL.md").read_bytes()).hexdigest()
    issued = datetime.fromisoformat(frozen["issued_at_utc"])
    assert datetime.fromisoformat(frozen["odds_received_at_kst"]) <= issued
    assert len({row["official_fixture_id"] for row in frozen["fixtures"]}) == 14
    for row in frozen["fixtures"]:
        assert issued < datetime.fromisoformat(row["kickoff_kst"])
        assert "score" not in row and "outcome" not in row
        rebuilt, _ = study.forecasts(row["quotes"])
        for model, values in rebuilt.items():
            np.testing.assert_allclose(row["probabilities"][model], values, atol=1e-6)


def test_pilot_uses_frozen_forecasts_only_after_official_identity_and_final(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    from score_pilot import settle

    frozen = json.loads((ROOT / "prospective-260132.json").read_bytes())
    sources = {league: ({"received_at_utc": "2026-11-09T01:00:00+00:00", "sha256": "example"}, [])
               for league in (1, 2)}
    for row in frozen["fixtures"]:
        _, _, league, game = row["official_fixture_id"].split(":")
        day, time = row["kickoff_kst"][:16].split("T")
        sources[int(league)][1].append({"leagueId": int(league), "gameId": int(game),
                                       "gameDate": day.replace("-", "."), "gameTime": time,
                                       "homeTeam": row["home_team_id"], "awayTeam": row["away_team_id"],
                                       "gameStatus": "FE", "endYn": "Y", "homeGoal": 0, "awayGoal": 0})
    result = settle(frozen, sources)
    assert result["n_joined"] == 14 and result["metrics"] is not None
    assert result["joined"][0]["probabilities"] == frozen["fixtures"][0]["probabilities"]
    pending = sources[1][1][0]
    pending["gameStatus"] = "BE"
    assert settle(frozen, sources)["metrics"] is None
    pending["homeTeam"] = "invalid"
    with pytest.raises(ValueError, match="team or kickoff"):
        settle(frozen, sources)


def test_forward_deduplicates_matches_and_updates_only_result_block(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT))
    from forward_cycle import END, START, link_new, update_manuscript

    fixtures = [
        {"league": "K1", "kickoff_kst": "2026-11-09T19:30:00+09:00",
         "home_team_id": "K01", "away_team_id": "K02"},
        {"league": "K1", "kickoff_kst": "2026-11-09T19:30:00+09:00",
         "home_team_id": "K03", "away_team_id": "K04"},
    ]
    schedule = [{"leagueId": 1, "gameId": game, "homeTeam": f"K{home:02d}", "awayTeam": f"K{away:02d}",
                 "gameDate": "2026.11.09", "gameTime": "19:30", "endYn": "N"}
                for game, home, away in ((301, 1, 2), (302, 3, 4))]
    new = link_new({"fixtures": fixtures}, lambda *_: ({}, schedule), {"kleague:2026:1:301"})
    assert [r["official_fixture_id"] for r in new] == ["kleague:2026:1:302"]

    manuscript = tmp_path / "paper.md"
    manuscript.write_text(f"Before\n{START}\nold\n{END}\nAfter\n", encoding="utf-8")
    result = {"outcome": 1, "quotes": {"winner": {"odds": [2.0, 3.0, 4.0]}},
              "probabilities": {"winner_implied": [0.45, 0.25, 0.30], "poisson_three": [0.30, 0.45, 0.25]}}
    update_manuscript(manuscript, 2, 14, [result])
    content = manuscript.read_text(encoding="utf-8")
    assert content.startswith(f"Before\n{START}") and content.endswith(f"{END}\nAfter\n")
    assert "공식 정산 1개" in content and "-0.58779" in content


def test_walk_forward_ignores_results_inside_embargo_and_test_week(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    import walk_forward

    monkeypatch.setattr(walk_forward, "MIN_TRAIN", 20)
    monkeypatch.setattr(walk_forward, "MIN_VALIDATION", 12)
    monkeypatch.setattr(walk_forward, "L2_STRENGTHS", (0.50,))
    raw = (ROOT / "historical.picks.jsonl").read_bytes().splitlines()[:90]
    rows = list(map(json.loads, raw))
    original = walk_forward.predict(rows)
    target = original[-1]
    assert target["selected"] is not None
    changed = [row | {"outcome": (row["outcome"] + 1) % 3}
               if row["kickoff_kst"][:10] >= target["train_before"] else row for row in rows]
    replay = walk_forward.predict(changed)[-1]
    assert replay["selected"] == target["selected"]
    assert replay["predictions"] == target["predictions"]
    assert replay["train_n"] == target["train_n"]
