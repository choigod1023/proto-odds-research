from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lotto645 import DrawRecord
from lotto_seed_survival import tickets, match_counts, run_seed_survival, markdown_report
from lotto_pipeline import main


def fixture_draws(seed=7, count=12):
    return [DrawRecord(i, "2020-01-01", combo,
                       next(n for n in range(1, 46) if n not in combo))
            for i, combo in enumerate(tickets(seed, count), 1)]


def experiment(draws, **kwargs):
    return run_seed_survival(draws, candidate_count=10, train_end=6,
                             validation_end=9, baseline_repeats=4, **kwargs)


def test_generator_reproducible_valid_and_split_stream_consistent():
    draws = fixture_draws()
    assert list(tickets(7, 12)) == list(tickets(7, 12))
    assert all(len(set(d.numbers_sorted)) == 6 for d in draws)
    assert match_counts(7, draws[6:]) == [6] * 6
    assert list(tickets(7, 6)) == list(tickets(7, 12))[:6]


def test_repeated_jackpot_selection_and_equal_budget():
    result = experiment(fixture_draws(), min_wins=2)
    assert result["selection"]["selected_seeds"] == [7]
    assert result["training_jackpot_counts"]["7"] == 6
    for stage in result["selection"]["stages"]:
        assert 7 in stage["seeds"]
    for key in ("validation", "final"):
        assert result[key]["selected"]["jackpots"] == 3
        assert all(run["tickets"] == 3 for run in result[key]["baseline"]["runs"])
    assert all(s >= 10 for group in result["selection"]["control_seed_groups"] for s in group)


def test_heldout_outcomes_cannot_change_selection():
    draws = fixture_draws()
    replacement = fixture_draws(seed=99)
    changed = draws[:6] + replacement[6:]
    before, after = experiment(draws), experiment(changed)
    assert before["selection"] == after["selection"]
    assert before["selection_hash"] == after["selection_hash"]
    assert before["final"] != after["final"]
    assert before == experiment(draws)


def test_survival_ladder_removes_single_hit_seeds():
    draws = fixture_draws(seed=99)
    seven, eight = fixture_draws(seed=7), fixture_draws(seed=8)
    draws[:2] = seven[:2]
    draws[2] = eight[2]
    result = experiment(draws, min_wins=2)
    assert result["selection"]["stages"][0]["seeds"] == [7, 8]
    assert result["selection"]["stages"][1]["seeds"] == [7]
    assert result["selection"]["stages"][2]["seeds"] == []
    assert result["selection"]["selected_seeds"] == [7]


def test_extinction_is_explicit_without_fallback():
    result = experiment(fixture_draws(), min_wins=7)
    assert result["selection"]["selected_seeds"] == []
    assert result["final"]["status"] == "no_survivors"
    assert result["final"]["baseline"] is None
    assert "생존 시드가 없어" in markdown_report(result)


@pytest.mark.parametrize("updates", [dict(train_end=9), dict(validation_end=12),
                                     dict(candidate_count=0), dict(min_wins=0),
                                     dict(baseline_repeats=0), dict(start_seed=-1)])
def test_invalid_protocol(updates):
    options = dict(train_end=6, validation_end=9, candidate_count=10)
    options.update(updates)
    with pytest.raises(ValueError):
        run_seed_survival(fixture_draws(), **options)


def test_cli_exports_protocol_and_markdown(tmp_path):
    from lotto645 import save_draws_jsonl
    ledger = tmp_path / "draws.jsonl"
    save_draws_jsonl(fixture_draws(), ledger)
    assert main(["--data", str(ledger), "seed-survival", "--candidate-count", "10",
                 "--train-end", "6", "--validation-end", "9", "--baseline-repeats", "2",
                 "--report-dir", str(tmp_path / "report")]) == 0
    assert (tmp_path / "report" / "experiment.json").exists()
    assert "최종 평가" in (tmp_path / "report" / "report.md").read_text(encoding="utf-8")
