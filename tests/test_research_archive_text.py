import gzip
import json
from scripts.audit_research_archive_text import audit
import pytest


def archive(tmp_path, rows):
    path = tmp_path/"source.jsonl.gz"
    path.write_bytes(gzip.compress("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in rows).encode("utf8")))
    return path


def test_literal_unicode_survives_independently_of_console_encoding(tmp_path):
    path = archive(tmp_path, [dict(league="NBA", home="인디페이", away="LA클리퍼")])
    before = path.read_bytes()
    result = audit(path, "NBA", {"인디페이": "IND", "LA클리퍼": "LAC"})
    assert result["rows"] == 1 and result["distinct_team_names"] == 2
    assert result["literal_replacement_names"] == {} and result["unmapped_names"] == []
    assert json.loads(json.dumps(result, ensure_ascii=True))["team_names"] == {"LA클리퍼": 1, "인디페이": 1}
    assert path.read_bytes() == before


def test_real_replacement_is_reported_and_never_repaired_from_result(tmp_path):
    path = archive(tmp_path, [dict(league="NBA", home="\ufffd팀", away="LA클리퍼", y=0),
                              dict(league="NBA", home="\ufffd팀", away="LA클리퍼", y=1)])
    result = audit(path, "NBA", {"LA클리퍼": "LAC"})
    assert result["literal_replacement_names"] == {"\ufffd팀": 2}
    assert result["unmapped_names"] == ["\ufffd팀"]


def test_non_utf8_is_rejected_instead_of_lossy_decode(tmp_path):
    path = tmp_path/"source.jsonl"
    path.write_bytes(b'{"league":"NBA","home":"\xff"}')
    with pytest.raises(UnicodeDecodeError):
        audit(path, "NBA")


def test_alias_collisions_are_visible_and_other_leagues_are_not_mixed(tmp_path):
    path = archive(tmp_path, [dict(league="NBA", home="A", away="B"),
                              dict(league="KBL", home="C", away="D")])
    result = audit(path, "NBA", {"A": "same", "B": "same"})
    assert result["rows"] == 1 and result["many_names_to_one_alias"] == {"same": ["A", "B"]}
