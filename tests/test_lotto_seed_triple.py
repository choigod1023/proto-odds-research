import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lotto645 import DrawRecord, save_draws_jsonl
from lotto_seed_expanded import MASK, N, STEP, combo_rank, ranks, ticket_masks, unmix
from lotto_seed_triple import main, native_scan


@pytest.fixture(scope="module")
def executable(tmp_path_factory):
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("native scanner tests require g++")
    root = Path(__file__).resolve().parents[1]
    exe = tmp_path_factory.mktemp("native") / "scan.exe"
    subprocess.run([compiler, "-O2", "-std=c++17", "-pthread", "-static",
                    str(root / "src/lotto_seed_scan.cpp"), "-o", str(exe)], check=True)
    return exe


def draw(number, combo):
    return DrawRecord(number, "2020-01-01", combo,
                      next(n for n in range(1, 46) if n not in combo))


def fixture():
    anchor = draw(1, (1, 2, 3, 4, 5, 6))
    seed = (int(unmix(np.array([20261015 * N], dtype=np.uint64))[0])-STEP) & MASK
    rows = [anchor]
    for number in range(2, 13):
        mask = int(ticket_masks(ranks([seed], number))[0])
        rows.append(draw(number, tuple(n for n in range(1, 46) if mask & (1 << (n-1)))))
    return rows, seed


def test_native_matches_python_and_batch_partition(executable):
    training, expected = fixture()
    training = training[:6]
    count = 1000
    target = combo_rank(training[0].numbers_sorted)
    population = (MASK-target)//N+1
    q = (np.arange(count, dtype=np.uint64)*1000003+20261015) % np.uint64(population)
    candidates = unmix(q*np.uint64(N)+np.uint64(target))-np.uint64(STEP)
    # Exactly three hits must survive; a separate exactly-two-hit seed must not.
    other_mask = int(ticket_masks(ranks(candidates[1:2], 4))[0])
    training[3] = draw(4, tuple(n for n in range(1,46) if other_mask & (1 << (n-1))))
    training[4:] = [draw(i, (1,2,3,4,5,6)) for i in (5,6)]
    hits = sum((ranks(candidates,d.draw_no)==combo_rank(d.numbers_sorted)).astype(int) for d in training)
    assert hits[0] == 3
    assert hits[1] == 2
    native = native_scan(executable,training,0,count,20261015,1000003,4)
    assert native["histogram"] == np.bincount(hits,minlength=7).tolist()
    assert {int(row["seed"]) for row in native["survivors"]} == set(map(int,candidates[hits>=3]))
    assert expected in candidates[hits>=3]
    first = native_scan(executable,training,0,400,20261015,1000003,1)
    second = native_scan(executable,training,400,600,20261015,1000003,2)
    assert (np.array(first["histogram"])+second["histogram"]).tolist()==native["histogram"]
    assert first["survivors"]+second["survivors"]==native["survivors"]


def test_resume_without_duplicate_count_and_protocol_guard(executable,tmp_path):
    ledger=tmp_path/"ledger.jsonl"
    save_draws_jsonl(fixture()[0],ledger)
    output=tmp_path/"results"
    args=["--executable",str(executable),"--data",str(ledger),"--output",str(output),
          "--candidate-count","20","--batch-size","10","--workers","2",
          "--train-end","6","--validation-end","9","--baseline-repeats","5"]
    assert main(args)==0
    before=json.loads((output/"experiment.json").read_text())
    assert before["survival_counts"]["1"]==20
    assert before["survival_counts"]["3"]>=1
    assert main(args)==0
    assert json.loads((output/"experiment.json").read_text())==before
    with pytest.raises(SystemExit):
        main(args+["--candidate-count","21"])


def test_native_invalid_permutation_rejected(executable):
    with pytest.raises(subprocess.CalledProcessError):
        native_scan(executable,fixture()[0],0,10,0,0,1)
