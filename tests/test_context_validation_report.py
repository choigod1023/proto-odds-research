import importlib.util
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from context_validation_report import percent


def test_missing_percentage_is_not_zero():
    assert percent(None)=='산출 불가'
    assert percent(0)=='0.00%'


def test_report_command_rejects_existing_output_before_reading_sources(tmp_path, monkeypatch):
    import context_validation_report as module
    output=tmp_path/'existing.sqlite3'
    output.write_bytes(b'keep')
    monkeypatch.setattr(sys,'argv',['report','--run-dir',str(tmp_path/'missing'),
                                  '--mlb-summary',str(tmp_path/'missing.json'),
                                  '--database',str(output),'--report',str(tmp_path/'r.md'),
                                  '--summary',str(tmp_path/'r.json')])
    with pytest.raises(SystemExit):
        module.main()
    assert output.read_bytes()==b'keep'
