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
