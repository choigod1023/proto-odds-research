from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from lotto_quantum_99999 import load_block
from lotto_quantum_9999 import write_archive


def test_streamed_block_requires_frozen_protocol_and_continuity(tmp_path):
    path=tmp_path/"chunk.gz"
    payload={"protocol_hash":"frozen","start":9999,"end":10000,"rows":[{"value":1}]}
    write_archive(path,payload)
    assert load_block(path,9999,"frozen")==payload
    with pytest.raises(AssertionError):
        load_block(path,10000,"frozen")
    with pytest.raises(AssertionError):
        load_block(path,9999,"changed")
