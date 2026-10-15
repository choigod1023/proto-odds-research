import json
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from lotto645 import save_draws_jsonl
from lotto_seed_expanded import N, STEP, unmix
from lotto_seed_128 import encode_seed, ticket
from lotto_seed_five import build_selection, scan_half
from lotto_seed_six import main
from test_lotto_seed_five import executable, fixture, row


def six_fixture():
    rows,odd,_=fixture()
    even=unmix(np.array([20261015*N],dtype=np.uint64))-np.uint64((2*STEP)&((1<<64)-1))
    seed=encode_seed(odd[0],even[0])
    for n in (4,6):
        rows[n-1]=row(n,ticket(seed,n))
    return rows,odd,even


def test_even_native_threshold_and_six_pair_validation(executable):
    rows,odd,even=six_fixture()
    evens=[d for d in rows[:8] if not d.draw_no%2]
    selected,counts=scan_half(executable,evens,100,40,20261015,1000003,2)
    assert even[0] in selected
    assert counts['3']>=1
    result=build_selection(odd,even,rows[:8],{},counts,minimum_even=3)
    assert result['pairs'][0]['training_jackpot_draws']==[1,2,3,4,5,6]
    rows[5]=row(6,(1,2,3,4,5,6))
    with pytest.raises(ValueError):
        build_selection(odd,even,rows[:8],{},counts,minimum_even=3)
    with pytest.raises(ValueError):
        scan_half(executable,rows[:8],100,40,20261015,1000003,2)


def test_six_cli_parallel_equivalence_and_no_leakage(executable,tmp_path):
    rows,odd,even=six_fixture()
    ledger=tmp_path/'ledger.jsonl'
    save_draws_jsonl(rows,ledger)
    def args(output,workers):
        return ['--executable',str(executable),'--data',str(ledger),'--output',str(output),
                '--odd-candidates','100','--even-candidates','100','--batch-size','40',
                '--native-threads','2','--workers',str(workers),'--train-end','8',
                '--validation-end','10','--baseline-repeats','5']
    first,second=tmp_path/'serial',tmp_path/'parallel'
    assert main(args(first,1))==0
    assert main(args(second,2))==0
    a=json.loads((first/'experiment.json').read_text())
    b=json.loads((second/'experiment.json').read_text())
    assert a['protocol']['minimum_pair_wins']==6
    assert a['selection']==b['selection']
    assert a['validation']==b['validation'] and a['final']==b['final']
    assert len(list(second.glob('odd-batch-*.json')))==3
    assert len(list(second.glob('even-batch-*.json')))==3
    seed=encode_seed(odd[0],even[0])
    rows[8:]=[row(i,ticket(seed,i)) for i in range(9,13)]
    save_draws_jsonl(rows,ledger)
    third=tmp_path/'changed'
    assert main(args(third,2))==0
    c=json.loads((third/'experiment.json').read_text())
    assert a['selection']==c['selection']
    assert a['final']!=c['final']
    assert '6회 이상' in (third/'report.md').read_text(encoding='utf-8')
