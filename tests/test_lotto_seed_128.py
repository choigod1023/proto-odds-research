import json
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from lotto645 import DrawRecord, save_draws_jsonl
from lotto_seed_expanded import MASK, anchored_seeds, uniform_controls
from lotto_seed_128 import decode_seed, encode_seed, evaluate, evaluate_splits, main, select_halves, ticket


def draw(n, numbers):
    return DrawRecord(n, "2020-01-01", tuple(numbers), next(i for i in range(1,46) if i not in numbers))


def fixture():
    rows = [draw(i, (1,2,3,4,5,6)) for i in range(1,13)]
    odd = anchored_seeds(rows[0], 30, 12)[0]
    even = anchored_seeds(rows[1], 30, 13)[0]
    seed = encode_seed(odd, even)
    rows[2] = draw(3,ticket(seed,3))
    rows[3] = draw(4,ticket(seed,4))
    return rows, seed


def test_uint128_roundtrip_and_parity():
    assert decode_seed(encode_seed(MASK,0)) == (MASK,0)
    assert decode_seed(encode_seed(0,MASK)) == (0,MASK)
    for d in (1,3,741,1237):
        assert ticket(encode_seed(123,456),d)==ticket(encode_seed(123,789),d)
    for d in (2,4,742,1238):
        assert ticket(encode_seed(123,456),d)==ticket(encode_seed(789,456),d)
    with pytest.raises(ValueError):
        decode_seed("f"*33)
    with pytest.raises(ValueError):
        encode_seed(-1,1)


def test_training_pairing_constructs_four_hits():
    rows, expected = fixture()
    odd, even, selection = select_halves(rows[:6],30,12,13)
    assert expected in [p["seed_hex"] for p in selection["pairs"]]
    assert len(selection["pairs"])==len(odd)*len(even)
    for pair in selection["pairs"]:
        direct = [d.draw_no for d in rows[:6] if ticket(pair["seed_hex"],d.draw_no)==d.numbers_sorted]
        assert pair["training_jackpot_draws"]==direct
        assert len(direct)>=4


def brute(odd,even,draws):
    hist=[0]*7
    for o in odd:
        for e in even:
            for d in draws:
                matches=len(set(ticket(encode_seed(o,e),d.draw_no))&set(d.numbers_sorted))
                hist[matches]+=1
    return hist


def test_shared_stream_evaluation_matches_explicit_cartesian_tickets():
    rows,_=fixture()
    odd=np.array([1,2],dtype=np.uint64)
    even=np.array([3,4,5],dtype=np.uint64)
    oc=uniform_controls(odd,5,42)
    ec=uniform_controls(even,5,43)
    result=evaluate(odd,even,oc,ec,rows)
    assert result["selected_histogram"]==brute(odd,even,rows)
    assert result["tickets_per_group"]==6*12
    assert result["distinct_stream_tickets"]==2*6+3*6
    for i in range(5):
        assert result["baseline_histograms"][i]==brute(oc[i],ec[i],rows)
    selected_max=max(sum(k*v for k,v in enumerate(brute([o],[e],rows)))
                     for o in odd for e in even)
    control_max=[max(sum(k*v for k,v in enumerate(brute([o],[e],rows)))
                     for o in oc[i] for e in ec[i]) for i in range(5)]
    assert result["best_pair_comparison"]["selected_mean_matches"]==selected_max/len(rows)
    assert result["best_pair_comparison"]["fraction_control_group_best_at_least_selected_best"]==sum(v>=selected_max for v in control_max)/5
    assert np.sum([p["histogram"] for p in result["per_pair"]],axis=0).tolist()==result["selected_histogram"]
    empty=evaluate(np.array([]),even,np.empty((5,0)),ec,rows)
    assert empty["status"]=="no_survivors"
    with pytest.raises(ValueError):
        evaluate(odd,even,oc,ec[:2],rows)


def test_cli_selection_unchanged_by_heldout_outcomes(tmp_path):
    rows,_=fixture()
    ledger=tmp_path/"draws.jsonl"
    save_draws_jsonl(rows,ledger)
    def args(output):
        return ["--data",str(ledger),"--output",str(output),"--candidates-per-half","30",
                "--odd-search-seed","12","--even-search-seed","13","--train-end","6",
                "--validation-end","9","--baseline-repeats","5"]
    first=tmp_path/"first"
    second=tmp_path/"second"
    assert main(args(first))==0
    initial=json.loads((first/"experiment.json").read_text())
    seed=initial["selection"]["pairs"][0]["seed_hex"]
    rows[6:]=[draw(i,ticket(seed,i)) for i in range(7,13)]
    save_draws_jsonl(rows,ledger)
    assert main(args(second))==0
    updated=json.loads((second/"experiment.json").read_text())
    assert initial["selection"]==updated["selection"]
    assert initial["selection_hash"]==updated["selection_hash"]
    assert initial["final"]!=updated["final"]
    with pytest.raises(SystemExit):
        main(args(second))


def test_parallel_matches_serial_selection_and_complete_evaluation():
    rows,_=fixture()
    serial=select_halves(rows[:6],30,12,13,workers=1)
    parallel=select_halves(rows[:6],30,12,13,workers=2)
    assert np.array_equal(serial[0],parallel[0])
    assert np.array_equal(serial[1],parallel[1])
    assert serial[2]==parallel[2]
    odd,even,_=serial
    oc=uniform_controls(odd,5,42)
    ec=uniform_controls(even,5,43)
    assert evaluate_splits(odd,even,oc,ec,rows[6:9],rows[9:],workers=1)==evaluate_splits(odd,even,oc,ec,rows[6:9],rows[9:],workers=2)
    with pytest.raises(ValueError):
        select_halves(rows[:6],30,12,13,workers=3)
