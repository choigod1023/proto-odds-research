import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from lotto645 import DrawRecord, save_draws_jsonl
from lotto_seed_expanded import N, STEP, anchored_seeds, combo_rank, ranks, unmix
from lotto_seed_128 import encode_seed, ticket
from lotto_seed_five import build_selection, main, scan_odd


@pytest.fixture(scope="module")
def executable(tmp_path_factory):
    compiler=shutil.which("g++")
    if compiler is None:
        pytest.skip("native search test requires g++")
    exe=tmp_path_factory.mktemp("native")/"scan.exe"
    source=Path(__file__).resolve().parents[1]/"src/lotto_seed_scan.cpp"
    subprocess.run([compiler,"-O2","-std=c++17","-pthread","-static",str(source),"-o",str(exe)],check=True)
    return exe


def row(n,numbers):
    return DrawRecord(n,"2020-01-01",numbers,next(x for x in range(1,46) if x not in numbers))


def fixture():
    rows=[row(i,(1,2,3,4,5,6)) for i in range(1,13)]
    odd=unmix(np.array([20261015*N],dtype=np.uint64))-np.uint64(STEP)
    even=anchored_seeds(rows[1],30,13)[:1]
    seed=encode_seed(odd[0],even[0])
    for n in (3,4,5):
        rows[n-1]=row(n,ticket(seed,n))
    other=unmix(np.array([(20261015+1000003)*N],dtype=np.uint64))-np.uint64(STEP)
    rows[6]=row(7,ticket(encode_seed(other[0],even[0]),7))
    return rows,odd,even


def test_native_three_hit_threshold_and_partition(executable):
    rows,odd,even=fixture()
    training=[d for d in rows[:8] if d.draw_no%2]
    chosen,counts=scan_odd(executable,training,100,40,20261015,1000003,2)
    assert int(odd[0]) in chosen
    seeds=unmix((np.arange(100,dtype=np.uint64)*np.uint64(1000003)+np.uint64(20261015))*np.uint64(N))-np.uint64(STEP)
    hits=sum((ranks(seeds,d.draw_no)==combo_rank(d.numbers_sorted)).astype(int) for d in training)
    assert hits[0]==3 and hits[1]==2
    assert np.array_equal(chosen,np.sort(seeds[hits>=3]))
    assert counts['1']==100
    assert counts['2']==int((hits>=2).sum())
    assert np.array_equal(scan_odd(executable,training,100,100,20261015,1000003,1)[0],chosen)
    selection=build_selection(chosen,even,rows[:8],counts,{})
    assert all(len(p['training_jackpot_draws'])>=5 for p in selection['pairs'])
    with pytest.raises(ValueError):
        build_selection(seeds[1:2],even,rows[:8],{}, {})


def test_serial_parallel_cli_and_heldout_independence(executable,tmp_path):
    rows,_,_=fixture()
    ledger=tmp_path/'ledger.jsonl'
    save_draws_jsonl(rows,ledger)
    def args(path,workers):
        return ['--executable',str(executable),'--data',str(ledger),'--output',str(path),
                '--odd-candidates','100','--even-candidates','30','--even-search-seed','13',
                '--batch-size','40','--native-threads','2','--workers',str(workers),
                '--train-end','8','--validation-end','10','--baseline-repeats','5']
    first=tmp_path/'serial'
    second=tmp_path/'parallel'
    assert main(args(first,1))==0
    assert main(args(second,2))==0
    a=json.loads((first/'experiment.json').read_text())
    b=json.loads((second/'experiment.json').read_text())
    assert a['selection']==b['selection']
    assert a['validation']==b['validation'] and a['final']==b['final']
    seed=a['selection']['pairs'][0]['seed_hex']
    rows[8:]=[row(i,ticket(seed,i)) for i in range(9,13)]
    save_draws_jsonl(rows,ledger)
    third=tmp_path/'changed-holdout'
    assert main(args(third,2))==0
    c=json.loads((third/'experiment.json').read_text())
    assert a['selection']==c['selection']
    assert a['final']!=c['final']
    assert '5회 이상' in (third/'report.md').read_text(encoding='utf-8')
    with pytest.raises(SystemExit):
        main(args(third,1))
