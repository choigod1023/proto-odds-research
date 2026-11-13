import sys
from pathlib import Path
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from lotto_quantum_million import starts,read_new,tail
from lotto_quantum_9999 import write_archive,canonical_hash


def test_fixed_grid_covers_exactly_900000_new_histories():
    grid=list(starts())
    assert len(grid)==1800 and grid[0]==99999 and grid[-1]+500==999999
    assert all(b-a==500 for a,b in zip(grid,grid[1:]))


def test_out_of_order_block_is_independently_validated(tmp_path):
    protocol={"model":"fixed"}
    path=tmp_path/"histories-100499-100998.json.gz"
    p={"protocol_hash":canonical_hash(protocol),"start":100499,"end":100999,"rows":[{}]*500}
    write_archive(path,p)
    assert read_new(path,protocol)==p
    with pytest.raises(AssertionError):read_new(path,{"model":"changed"})


def test_tail_uses_exact_plus_one_and_all_ties():
    assert tail(0,np.zeros(999999))["p_upper_plus_one"]==1
    assert tail(1,np.zeros(999999))["p_upper_plus_one"]==.000001
    assert tail(1,np.array([0.,1.,2.]))["p_upper_plus_one"]==.75
