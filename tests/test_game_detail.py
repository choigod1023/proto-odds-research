import json

import pytest

from src import game_detail


@pytest.mark.parametrize("status", ["RESULT", "END", " result "])
def test_is_completed_game_accepts_known_completed_aliases(status):
    assert game_detail.is_completed_game({"statusCode": status})


@pytest.mark.parametrize("status", [None, "", "BEFORE", "STARTED", "LIVE", "SUSPENDED"])
def test_is_completed_game_fails_closed_for_nonfinal_status(status):
    assert not game_detail.is_completed_game({"statusCode": status})


def test_main_never_persists_in_progress_box_scores(tmp_path, monkeypatch):
    games = [
        {"gameId": "before", "statusCode": "BEFORE"},
        {"gameId": "live", "statusCode": "STARTED"},
        {"gameId": "result", "statusCode": "RESULT", "gameDate": "20260825"},
        {"gameId": "end", "statusCode": "END", "gameDate": "20260824"},
    ]
    requested = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"result": {}}

    class Session:
        def get(self, url, timeout):
            requested.append(url)
            return Response()

    monkeypatch.setattr(game_detail, "RAW", tmp_path)
    monkeypatch.setattr(game_detail, "_session", Session)
    monkeypatch.setattr(game_detail, "list_games", lambda *_args: games)
    monkeypatch.setattr(
        game_detail,
        "parse_baseball",
        lambda _result: {"home": [], "away": []},
    )
    monkeypatch.setattr(game_detail.time, "sleep", lambda _seconds: None)

    assert game_detail.main(
        ["game_detail.py", "baseball", "kbo", "2023", "2026"]
    ) == 0

    cache = json.loads(
        (tmp_path / "kbo_baseball_2023_2026.json").read_text(encoding="utf-8")
    )
    assert set(cache) == {"result", "end"}
    assert len(requested) == 2
    assert all("before" not in url and "live" not in url for url in requested)


def test_cancelled_game_is_not_completed_even_with_result_status():
    assert not game_detail.is_completed_game(
        {"statusCode": "RESULT", "cancel": True}
    )


def test_new_year_cache_seeds_previous_file_and_writes_atomically(tmp_path, monkeypatch):
    previous = tmp_path / "kbo_baseball_2023_2026.json"
    previous.write_text(json.dumps({"old": {"gameId": "old"}}), encoding="utf-8")
    monkeypatch.setattr(game_detail, "RAW", tmp_path)
    monkeypatch.setattr(game_detail, "_session", lambda: object())
    monkeypatch.setattr(game_detail, "list_games", lambda *_args: [])

    assert game_detail.main(
        ["game_detail.py", "baseball", "kbo", "2023", "2027"]
    ) == 0

    current = tmp_path / "kbo_baseball_2023_2027.json"
    assert json.loads(current.read_text(encoding="utf-8")) == {"old": {"gameId": "old"}}
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_detail_cache_is_preserved_and_not_overwritten(tmp_path, monkeypatch):
    corrupt = tmp_path / "kbo_baseball_2023_2027.json"
    corrupt.write_text('{"cut":', encoding="utf-8")
    monkeypatch.setattr(game_detail, "RAW", tmp_path)

    with pytest.raises(RuntimeError, match="preserved without overwrite"):
        game_detail.main(["game_detail.py", "baseball", "kbo", "2023", "2027"])

    assert corrupt.read_text(encoding="utf-8") == '{"cut":'
