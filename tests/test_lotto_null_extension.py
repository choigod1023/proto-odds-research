from dataclasses import replace
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lotto645 import DrawRecord
from lotto_null_extension import canonical_hash,tail_precision,text_hash
from lotto_new_rounds import validate_extension


def test_tail_precision_endpoints_and_plus_one():
    zero=tail_precision(2,[0,1]); assert zero['p_mc']==pytest.approx(1/3)
    assert zero['underlying_tail_probability_interval_95'][0]==0
    all_hits=tail_precision(0,[0,1]); assert all_hits['p_mc']==1
    assert all_hits['underlying_tail_probability_interval_95'][1]==1
    one=tail_precision(1,[0,1,2]); assert one['exceedances']==2


def test_digest_is_order_and_newline_stable(tmp_path):
    assert canonical_hash({'a':1,'b':2})==canonical_hash({'b':2,'a':1})
    a=tmp_path/'a'; b=tmp_path/'b'; a.write_bytes(b'a\nb\n'); b.write_bytes(b'a\r\nb\r\n')
    assert text_hash(a)==text_hash(b)


def test_new_rounds_cannot_overlap_or_have_gaps():
    row=DrawRecord(1238,'2026-08-22',(1,2,3,4,5,6),7)
    new=[replace(row,draw_no=i,draw_date='2026-10-10') for i in range(1239,1246)]
    validate_extension([row],new)
    with pytest.raises(ValueError): validate_extension([row],new[:-1])
    with pytest.raises(ValueError): validate_extension([replace(row,draw_no=1239)],new)
    with pytest.raises(ValueError): validate_extension([row],[replace(d,draw_date='2026-10-17') for d in new])
