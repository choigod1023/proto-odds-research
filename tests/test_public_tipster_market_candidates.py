import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from public_tipster_market_candidates import extract_candidates, match_candidate


def options(market, label, line, selections, number="10"):
    return {"options": [dict(market=market, label=label, line=line, 선택=s,
                             배당=1.8, 게임번호=number) for s in selections]}


def test_observed_goli_fixture_without_lines_is_withheld():
    # The repository's 2026-10-29 observed-page fixture, including the
    # first-half boundary; do not invent a line from the current market.
    section = "애들레이드 유나이티드 승\n핸디 승\n오버\n전반전 예측\n[언더오버] 2.5 언더"
    assert extract_candidates(section) == []
    assert extract_candidates("예상 승리팀 : KIA 타이거즈\n승1패 : KIA 승리\n핸디 : 삼성 승리") == []


def test_total_exact_market_and_evidence():
    candidate, = extract_candidates("[언더오버] 2.5 오버\n전반전 예측\n[언더오버] 2.5 언더")
    assert candidate == dict(family="total", side="over", line=2.5, period="full_game",
                             settlement="2way", evidence="[언더오버] 2.5 오버")
    game = options("언더오버", "U 2.5", 2.5, ["언더", "오버"])
    assert match_candidate(candidate, game) == game["options"][1]
    game["options"][1]["line"] = 3.5
    assert match_candidate(candidate, game) is None


@pytest.mark.parametrize("section", [
    "[언더오버] 오버", "[언더오버] 2.25 오버", "[언더오버] 2.5 오버\n[언더오버] 2.5 언더",
    "[언더오버] 2.5 오버\n[언더오버] 2.5 오버", "[언더오버] 2.5 오버\n언더오버 언더",
    "1쿼터 [언더오버] 2.5 오버", "[핸디] 홈 +1.5", "[핸디 2way] 홈 +1.25",
])
def test_ambiguous_or_missing_line(section):
    assert extract_candidates(section) == []


def test_handicap_settlement_and_market_completeness():
    two, = extract_candidates("[핸디 2way] 원정 홈기준 -1.5")
    three, = extract_candidates("[핸디 3way] 무 홈기준 -1.0")
    g2 = options("핸디캡", "H -1.5", -1.5, ["핸디홈", "핸디원정"])
    g3 = options("핸디캡", "H -1.0", -1.0, ["핸디홈", "핸디무", "핸디원정"])
    assert match_candidate(two, g2)["선택"] == "핸디원정"
    assert match_candidate(three, g3)["선택"] == "핸디무"
    assert match_candidate(two, g3) is None
    assert match_candidate(three, g2) is None
    g2["options"].append(dict(g2["options"][0]))
    assert match_candidate(two, g2) is None
    assert match_candidate(two, options("핸디캡", "H +1.5", 1.5, ["핸디홈", "핸디원정"])) is None


def test_reject_duplicate_markets_invalid_odds_and_half_markets():
    candidate, = extract_candidates("언더오버 8.5 언더")
    game = options("언더오버", "8.5", 8.5, ["언더", "오버"])
    game["options"] += options("언더오버", "8.5", 8.5, ["언더", "오버"], "11")["options"]
    assert match_candidate(candidate, game) is None
    game["options"] = game["options"][:2]
    game["options"][0]["배당"] = float("nan")
    assert match_candidate(candidate, game) is None
    assert match_candidate(candidate, options("전반언더오버", "8.5", 8.5, ["언더", "오버"])) is None


def test_pick79_observed_rows_are_recorded_but_never_matched():
    from public_tipster_market_candidates import extract_unverified_pick79
    section = (Path(__file__).parent / "fixtures_public_market" / "pick79-20261028-san-antonio.txt").read_text(encoding="utf-8")
    assert extract_candidates(section) == []
    total, handicap = extract_unverified_pick79(section)
    assert (total["side"], total["line"], total["period"], total["settlement"]) == ("over", 187.5, "unknown", "unknown")
    assert (handicap["selected_team"], handicap["team_line"], handicap["period"], handicap["settlement"]) == ("샌안토니오 스퍼스", -4.5, "unknown", "unknown")
    assert match_candidate(total, options("언더오버", "187.5", 187.5, ["언더", "오버"])) is None
    assert match_candidate(handicap, options("핸디캡", "H -4.5", -4.5, ["핸디홈", "핸디원정"])) is None
    assert extract_unverified_pick79("추천 픽\nO/U\n언더\n177.5") == [dict(family="total", side="under", line=177.5, period="unknown", settlement="unknown", evidence="O/U / 언더 / 177.5")]


def test_pick79_unverified_duplicate_and_bad_rows_are_withheld():
    from public_tipster_market_candidates import extract_unverified_pick79
    assert extract_unverified_pick79("추천 픽\nO/U\n오버\n2.5\nO/U\n언더\n2.5") == []
    assert extract_unverified_pick79("추천 픽\n핸디캡\n홈\n-4.5\nO/U\n오버\n2.25") == []
    assert extract_unverified_pick79("전반전\nO/U\n오버\n187.5") == []
