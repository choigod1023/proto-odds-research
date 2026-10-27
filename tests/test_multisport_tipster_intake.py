import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pytest
from multisport_tipster_intake import parse_article, match_pregame


def article(**changes):
    values = dict(url="https://www.goli.tv/board/analysis/297856?page=1",
                  title="고양소노 대 부산KCC KBL 10월24일 맞대결", author="호나우두",
                  published_at="2026-10-24T02:07:00+09:00", observed_at="2026-10-24T03:00:00+09:00",
                  text="가능성이 높은 선택 > 고양 소노 승리\n배당을 노리는 선택 > 승5패 구간 5")
    return parse_article(**(values | changes))


def fixture(**changes):
    return dict(id="kbl-1", league="KBL", sport="bk", home="부산KCC", away="고양소노",
                kickoff="2026-10-24T14:00:00+09:00") | changes


def test_explicit_winner_not_alternative_market_or_article_home_order():
    a = article()
    assert not a["eligible"]
    vote = match_pregame(a, [fixture()], as_of=a["observed_at"])
    assert vote["eligible"] and vote["side"] == "away"
    assert vote["source_url"].endswith("/297856")


@pytest.mark.parametrize("changes", [
    dict(text="고양 소노가 좋아 보입니다"),
    dict(text="가능성이 높은 선택 > 고양 소노 핸디캡 승리"),
    dict(text="가능성이 높은 선택 > 고양 소노 승리\n가능성이 높은 선택 > 부산KCC 승리"),
    dict(author=""), dict(observed_at="2026-10-24T01:00:00+09:00"),
    dict(observed_at="2026-10-24T03:00:00"),
    dict(url="https://www.goli.tv.evil.test/board/analysis/1"),
    dict(url="https://www.goli.tv:444/board/analysis/1"),
    dict(title="고양소노 대 부산KCC KBL 2월31일 맞대결")])
def test_reject_uncertain_input(changes):
    with pytest.raises(ValueError):
        article(**changes)


def test_historical_article_first_read_today_is_not_pregame_evidence():
    a = article(observed_at="2026-10-26T00:00:00+09:00")
    result = match_pregame(a, [fixture()], as_of=a["observed_at"])
    assert result == {"eligible": False, "reason": "after_t30"}


def test_doubleheader_and_wrong_sport_withheld():
    a = article()
    assert not match_pregame(a, [fixture(), fixture(id="second")], as_of=a["observed_at"])["eligible"]
    assert not match_pregame(a, [fixture(sport="vl")], as_of=a["observed_at"])["eligible"]


def test_invalid_selection_is_not_silently_away():
    a = article() | {"selected_team": "다른팀"}
    assert not match_pregame(a, [fixture()], as_of=a["observed_at"])["eligible"]


def test_exact_freeze_boundary_and_no_future_observation():
    a = article()
    assert match_pregame(a, [fixture()], as_of="2026-10-24T13:30:00+09:00")["eligible"]
    assert not match_pregame(a, [fixture()], as_of="2026-10-24T13:30:01+09:00")["eligible"]
    assert not match_pregame(a, [fixture()], as_of="2026-10-24T02:00:00+09:00")["eligible"]


@pytest.mark.parametrize("league,sport", [("NBA", "bk"), ("WKBL", "bk"), ("KBO", "bs"), ("NPB", "bs"), ("KOVO컵", "vl"), ("V리그", "vl")])
def test_supported_league_identity(league, sport):
    a = article(title=f"팀A 대 팀B {league} 10월24일 맞대결", text="가능성이 높은 선택 > 팀B 승리")
    f = fixture(home="팀A", away="팀B", league=league, sport=sport)
    assert match_pregame(a, [f], as_of=a["observed_at"])["side"] == "away"
