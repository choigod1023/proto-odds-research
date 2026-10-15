import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lotto645 import DrawRecord
from lotto_seed_expanded import MASK, N, STEP, combo_rank, ranks, unmix
from lotto_seed_128 import encode_seed, save, ticket
from lotto_seed_seven import check_batch, extend_half, groups_for_seven
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from verify_lotto_seed_seven import matches_digest


def test_verification_survives_git_newlines_but_rejects_changed_content():
    lf=b'{"result": 0}\n'
    crlf=lf.replace(b'\n',b'\r\n')
    for raw in (lf,crlf):
        for recorded in (lf,crlf):
            assert matches_digest(raw,hashlib.sha256(recorded).hexdigest())
        assert not matches_digest(raw.replace(b'0',b'1'),hashlib.sha256(lf).hexdigest())


def row(n,numbers):
    return DrawRecord(n,'2020-01-01',numbers,next(i for i in range(1,46) if i not in numbers))


def test_seven_groups_include_both_directions_and_no_six():
    odd=[{'seed':'1','hits':3},{'seed':'2','hits':4}]
    even=[{'seed':'3','hits':3},{'seed':'4','hits':4}]
    groups=groups_for_seven(odd,even)
    assert {(g['odd_hits'],g['even_hits']) for g in groups}=={(3,4),(4,3),(4,4)}
    assert sum(len(g['odd'])*len(g['even']) for g in groups)==3
    assert groups_for_seven(odd[:1],even[:1])==[]
    assert groups_for_seven([],even)==[]


@pytest.mark.parametrize('parity',[0,1])
def test_native_extension_matches_full_scalar_search(tmp_path,parity):
    compiler=shutil.which('g++')
    if not compiler:
        pytest.skip('g++ required for native parity test')
    exe=tmp_path/'scan.exe'
    source=Path(__file__).resolve().parents[1]/'src/lotto_seed_scan.cpp'
    subprocess.run([compiler,'-O2','-std=c++17','-pthread','-static',str(source),'-o',str(exe)],check=True)
    anchor=1 if parity else 2
    training=[row(anchor,(1,2,3,4,5,6))]
    target=combo_rank(training[0].numbers_sorted)
    population=(MASK-target)//N+1
    offset,stride,total,prefix=17,1,120,60
    seeds=[(int(unmix(np.array([((j*stride+offset)%population)*N+target],dtype=np.uint64))[0])-anchor*STEP)&MASK for j in range(total)]
    # Force an exact four-hit half in the NEW range, including at uint64 scale.
    seed=seeds[91]
    for n in range(anchor+2,anchor+8,2):
        training.append(row(n,ticket(encode_seed(seed,seed),n)))
    counts=[sum(int(ranks([s],d.draw_no)[0])==combo_rank(d.numbers_sorted) for d in training) for s in seeds]
    hist=[counts[:prefix].count(i) for i in range(5)]
    batch={'start':0,'count':prefix,'histogram':hist,
           'survivors':[{'seed':str(s),'hits':h} for s,h in zip(seeds[:prefix],counts[:prefix]) if h>=3]}
    prior=tmp_path/'prior'; prior.mkdir()
    label='odd' if parity else 'even'
    save(prior/f'{label}-batch-000000000000.json',batch)
    outcomes=[]
    for threads in (1,3):
        output=tmp_path/str(threads); output.mkdir()
        result=extend_half(label,training,prior,output,prefix,total,prefix,exe,offset,stride,threads)
        assert result['histogram']==[counts.count(i) for i in range(5)]
        assert {int(r['seed']) for r in result['survivors']}=={s for s,h in zip(seeds,counts) if h>=3}
        assert [m['reused'] for m in result['manifest']]==[True,False]
        assert any(r['hits']==4 and int(r['seed'])==seed for r in result['survivors'])
        outcomes.append(result['survivors'])
    assert outcomes[0]==outcomes[1]
    broken=copy.deepcopy(batch); broken['start']=1
    with pytest.raises(ValueError,match='coverage'):
        check_batch(broken,training,0,prefix)
    broken=copy.deepcopy(batch); broken['histogram'][1]-=1
    with pytest.raises(ValueError):
        check_batch(broken,training,0,prefix)
