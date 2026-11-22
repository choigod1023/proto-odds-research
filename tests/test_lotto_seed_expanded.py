from dataclasses import replace
import math
from pathlib import Path
import random
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lotto645 import DrawRecord
from lotto_seed_expanded import (A, B, MASK, N, STEP, anchored_seeds, combo_rank,
                                evaluate, main, mix, popcount, ranks, select,
                                ticket_masks, uniform_controls, unmix)


def numbers(mask):
    return tuple(n for n in range(1, 46) if int(mask) & (1 << (n - 1)))


def fixture(count=12):
    return [DrawRecord(i, "2020-01-01", numbers(ticket_masks(ranks([123], i))[0]),
                       next(n for n in range(1, 46) if n not in numbers(ticket_masks(ranks([123], i))[0])))
            for i in range(1, count + 1)]


def test_inverse_and_scalar_generator_agree():
    values = np.array([0, MASK, 1] + [random.Random(i).getrandbits(64) for i in range(1000)], dtype=np.uint64)
    assert np.array_equal(unmix(mix(values)), values)
    for seed in values[:20]:
        for draw in (1, 740, 1238):
            x = (int(seed) + draw * STEP) & MASK
            x = ((x ^ (x >> 30)) * A) & MASK
            x = ((x ^ (x >> 27)) * B) & MASK
            x ^= x >> 31
            assert int(ranks([seed], draw)[0]) == x % N


def test_combinadic_roundtrip_and_bitcounts():
    values = np.array([0, N - 1] + random.Random(1).sample(range(N), 10000))
    masks = ticket_masks(values)
    assert np.all(popcount(masks) == 6)
    assert [combo_rank(numbers(m)) for m in masks] == values.tolist()
    assert numbers(masks[0]) == (1, 2, 3, 4, 5, 6)
    assert numbers(masks[1]) == (40, 41, 42, 43, 44, 45)
    assert popcount(np.array([0, MASK], dtype=np.uint64)).tolist() == [0, 64]


def test_anchor_and_two_hit_selection_without_holdout_leakage():
    draws = fixture()
    candidates = anchored_seeds(draws[0], 100, 12)
    assert len(set(map(int, candidates))) == 100
    assert np.all(ranks(candidates, 1) == combo_rank(draws[0].numbers_sorted))
    combo = numbers(ticket_masks(ranks(candidates[:1], 2))[0])
    draws[1] = replace(draws[1], numbers_sorted=combo,
                       bonus_number=next(n for n in range(1, 46) if n not in combo))
    selected, counts = select(draws[:6], 100, 12)
    assert int(candidates[0]) in selected
    assert counts["1"] == 100
    assert counts["2"] >= 1
    original = selected.copy()
    draws[6:] = fixture()[6:]
    assert np.array_equal(original, select(draws[:6], 100, 12)[0])


def test_equal_budget_baseline_and_histograms():
    selected = np.array([123], dtype=np.uint64)
    controls = uniform_controls(selected, 20, 42)
    assert 123 not in controls
    assert len(set(map(int, controls.ravel()))) == 20
    assert np.array_equal(controls, uniform_controls(selected, 20, 42))
    result = evaluate(selected, controls, fixture())
    assert result["selected_jackpots"] == 12
    assert result["selected_mean_matches"] == 6
    assert all(sum(hist) == result["tickets_per_group"] for hist in result["baseline_histograms"])
    assert result["baseline_fraction_at_least_selected"] == 0
    assert evaluate(np.array([], dtype=np.uint64), np.empty((2, 0)), fixture())["status"] == "no_survivors"


def test_baseline_matches_uniform_expectation():
    controls = uniform_controls(np.array([123], dtype=np.uint64), 10000, 42)
    result = evaluate(np.array([123], dtype=np.uint64), controls, fixture())
    assert abs(result["baseline_mean_matches"] - 6 * 6 / 45) < .01


def test_cli_protocol_and_no_overwrite(tmp_path):
    from lotto645 import save_draws_jsonl
    ledger = tmp_path / "draws.jsonl"
    save_draws_jsonl(fixture(), ledger)
    args = ["--data", str(ledger), "--output", str(tmp_path / "result"),
            "--candidate-count", "10", "--train-end", "6", "--validation-end", "9",
            "--baseline-repeats", "2"]
    assert main(args) == 0
    assert (tmp_path / "result/report.md").exists()
    with pytest.raises(SystemExit):
        main(args)
