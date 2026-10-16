from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from lotto_quantum_9999 import write_archive,audit_archives


def test_checkpoint_roundtrip_and_resume(tmp_path):
    payload={"protocol_hash":"fixed","start":0,"end":2,"rows":[{"a":1},{"a":2}]}
    write_archive(tmp_path/"histories-00000-00001.json.gz",payload)
    rows,manifest=audit_archives(tmp_path,"fixed")
    assert rows==payload["rows"] and len(manifest)==1
    write_archive(tmp_path/"histories-00002-00002.json.gz",{**payload,"start":2,"end":3,"rows":[{"a":3}]})
    assert audit_archives(tmp_path,"fixed")[0]==rows+[{"a":3}]
    with pytest.raises(AssertionError):
        audit_archives(tmp_path,"changed")


def test_checkpoint_rejects_gap(tmp_path):
    write_archive(tmp_path/"histories-00001-00001.json.gz",{"protocol_hash":"fixed","start":1,"end":2,"rows":[{}]})
    with pytest.raises(AssertionError):
        audit_archives(tmp_path,"fixed")
