from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import snapshot  # noqa: E402


def _row(result):
    return SimpleNamespace(result=result)


def test_find_live_rounds_retries_a_transient_empty_seq(monkeypatch):
    """wisetoto 가 한 회차에서 순간적으로 빈 페이지를 줘도 스캔이 끊기지 않는다."""
    monkeypatch.setattr(snapshot.time, "sleep", lambda *_: None)
    monkeypatch.setattr(snapshot, "SCAN_RANGE", 6)

    seq_calls = {"101": 0, "102": 0}

    def fake_seq(year, rnd, sess):
        if rnd == 102:
            seq_calls["102"] += 1
            # 첫 두 번은 봇 차단 페이지처럼 빈 응답, 세 번째에 복구된다.
            return None if seq_calls["102"] < 3 else "seq-102"
        if rnd in (101, 103):
            return f"seq-{rnd}"
        return None                     # 104 이상은 회차 없음

    def fake_fetch(sess, year, rnd, seq):
        return [_row("경기전"), _row("홈승")] if rnd in (101, 102, 103) else []

    monkeypatch.setattr(snapshot, "get_master_seq", fake_seq)
    monkeypatch.setattr(snapshot, "_fetch", fake_fetch)

    live = snapshot.find_live_rounds(object(), 2026, 101)

    # 102 에서 재시도했으므로 103 까지 스캔이 이어져야 한다.
    assert live == [101, 102, 103]
    assert seq_calls["102"] == 3


def test_find_live_rounds_stops_when_a_round_is_truly_absent(monkeypatch):
    monkeypatch.setattr(snapshot.time, "sleep", lambda *_: None)
    monkeypatch.setattr(snapshot, "SCAN_RANGE", 6)
    monkeypatch.setattr(snapshot, "get_master_seq",
                        lambda year, rnd, sess: "seq" if rnd <= 102 else None)
    monkeypatch.setattr(snapshot, "_fetch",
                        lambda sess, year, rnd, seq: [_row("경기전")])

    live = snapshot.find_live_rounds(object(), 2026, 101)

    assert live == [101, 102]
