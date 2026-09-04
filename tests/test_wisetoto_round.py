import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from wisetoto import get_master_seq  # noqa: E402


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


class _Session:
    def __init__(self, text: str) -> None:
        self._text = text

    def get(self, *args, **kwargs) -> _Resp:
        return _Resp(self._text)


def test_get_master_seq_extracts_real_value():
    html = "get_gameinfo_body('proto','pt1','2026','105','','','31436',now_sports,now_sort)"
    assert get_master_seq(2026, 105, _Session(html)) == "31436"


def test_get_master_seq_rejects_placeholder_zero_seq():
    # 발매되지 않은 회차를 요청하면 wisetoto 는 seq 자리에 '0' 을 넣은 껍데기 페이지를 준다.
    # 이걸 유효한 seq 로 받으면 최신 회차 탐지가 존재하지 않는 회차까지 올라간다.
    html = "get_gameinfo_body('proto','pt1','2026','511','','','0','','')"
    assert get_master_seq(2026, 511, _Session(html)) is None


def test_get_master_seq_returns_none_when_pattern_absent():
    assert get_master_seq(2026, 999, _Session("<html>no call here</html>")) is None
