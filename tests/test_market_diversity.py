import copy,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from public_tipster_votes import parse_page,match_vote
from public_tipster_market_candidates import candidate_events,extract_candidates,match_candidate
from market_diversity import build_market_candidates
from ai_decision import build_decision_snapshot

AT="2026-11-04T06:00:00Z"
URL="https://pick79.com/posts/detail/market-test/"

def fixture(sport="sc",league="K리그1"):
    return dict(sport=sport,league=league,home="서울",away="부산",year=2026,date="11.04(수) 19:00",options=[
        dict(market="승무패" if sport=="sc" else "승패",n_way=3 if sport=="sc" else 2,label="",선택=s,배당=p,시장확률=q,게임번호="1") for s,p,q in
        ([("홈",2.,.45),("무",3.5,.25),("원정",3.,.30)] if sport=="sc" else [("홈",1.8,.55),("원정",2.,.45)])]+[
        dict(market="언더오버",n_way=2,label="U 2.5",line=2.5,선택=s,배당=1.9,시장확률=.5,게임번호="2") for s in ("언더","오버")])

def html(choice="[언더오버] 2.5 오버"):
    graph={"@graph":[{"@type":"Article","author":{"name":"작성자"},"datePublished":"2026-11-04T10:00:00Z"},
        {"@type":"SportsEvent","homeTeam":{"name":"서울"},"awayTeam":{"name":"부산"},"superEvent":{"name":"K리그1"},"sport":"Soccer","startDate":"2026-11-04T10:00:00Z","eventStatus":"https://schema.org/EventScheduled"}]}
    return '<script type="application/ld+json">'+json.dumps(graph)+'</script><div class="section-widget"><h2>추천 픽</h2><div><div><div><div>승무패</div><div>서울</div></div><div>승</div></div></div><p>'+choice+'</p></div>'

def event():
    body=html();winner=match_vote(parse_page(URL,body,AT),[fixture()]);return candidate_events(winner,URL,body)[0]

def test_same_received_page_records_extra_market_without_replacing_winner():
    body=html();winner=match_vote(parse_page(URL,body,AT),[fixture()]);extra,=candidate_events(winner,URL,body)
    assert winner["market_type"]=="full_game_winner" and winner["side"]=="홈"
    assert extra["market_type"]=="full_game_total" and extra["side"]=="over"
    assert extra["observed_at"]==winner["observed_at"] and extra["content_hash"]==winner["content_hash"]
    assert extra["pick_id"]!=winner["pick_id"]
    edited=html("[언더오버] 2.5 언더");edited_winner=match_vote(parse_page(URL,edited,AT),[fixture()])
    other,=candidate_events(edited_winner,URL,edited)
    assert other["pick_id"]==extra["pick_id"] # An edit cannot become a second first vote.
    with pytest.raises(ValueError,match="source_bytes"):candidate_events(winner,URL,edited)

@pytest.mark.parametrize("sport,league",[("sc","K리그1"),("sc","K리그2"),("bs","MLB"),("bs","NPB"),("bk","KBL"),("bk","NBA"),("vl","V리그남"),("vl","V리그여")])
def test_all_sports_use_exact_market_candidate_without_probability_changes(sport,league):
    g=fixture(sport,league);e=event()
    from public_tipster_votes import fixture_key
    e["fixture"]=fixture_key(g);before=copy.deepcopy(g)
    result=build_market_candidates(g,[e],AT,AT);offer,=result["offers"]
    assert offer["selection"]=="오버" and offer["authors"]==1 and offer["price_receipt_verified"] is False
    assert result["affects_selection"] is False and result["affects_probability"] is False
    assert g==before

def test_no_cross_fixture_late_half_or_wrong_line():
    e=event();g=fixture()
    for changed in ({"fixture":"wrong"},{"observed_at":"2026-11-04T09:31:00Z"},{"provenance":"claimed_publication"}):
        assert build_market_candidates(g,[dict(e,**changed)],AT,AT)["offers"]==[]
    assert build_market_candidates(g,[e],"2026-11-04T09:31:00Z","2026-11-04T09:31:00Z")["status"]=="after_t30"
    for field,value in (("period","first_half"),("line",3.5),("n_way",3)):
        other=copy.deepcopy(g);other["options"][-1][field]=value
        assert build_market_candidates(other,[e],AT,AT)["offers"]==[]

def test_unique_first_author_vote_and_conflict_withheld():
    e=event();later=dict(e,side="under",observed_at="2026-11-04T06:01:00Z",pick_id="later")
    result=build_market_candidates(fixture(),[e,e,later],"2026-11-04T06:02:00Z",AT)
    assert result["offers"][0]["selection"]=="오버" and result["offers"][0]["authors"]==1
    conflict=dict(e,side="under",pick_id="conflict")
    result=build_market_candidates(fixture(),[e,conflict],AT,AT)
    assert result["offers"]==[] and result["rejected"]["ambiguous_first_author_vote"]==1

def test_first_author_line_is_fixed_before_matching_current_price():
    e=event();earlier=dict(e,line=3.5,side="under",observed_at="2026-11-04T05:59:00Z",pick_id="earlier")
    result=build_market_candidates(fixture(),[e,earlier],AT,AT)
    assert result["offers"]==[] and result["rejected"]["market_line_or_scope_mismatch"]==1

def test_input_failure_is_not_reported_as_absent_opinion():
    result=build_market_candidates(fixture(),[{"_tipster_load_error":True}],AT,AT)
    assert result["status"]=="tipster_input_unavailable" and result["offers"]==[]

def test_reversed_source_home_handicap_is_not_silently_reoriented():
    g=fixture();g["home"],g["away"]="부산","서울"
    body=html("[핸디 2way] 홈 -1.5");winner=match_vote(parse_page(URL,body,AT),[g])
    assert winner["side"]=="원정"
    assert candidate_events(winner,URL,body)==[]

@pytest.mark.parametrize("tail",["언더","오버","언더오버 언더"])
def test_standalone_unknown_or_conflicting_total_is_withheld(tail):
    assert extract_candidates("[언더오버] 2.5 오버\n"+tail)==[]

def test_noninteger_three_way_handicap_rejected():
    assert extract_candidates("[핸디 3way] 무 -1.5")==[]
    assert extract_candidates("[핸디 2way] 원정 -1.5")==[]

def test_snapshot_keeps_operating_winner_while_exposing_other_market_candidate():
    g=fixture();e=event();snapshot=build_decision_snapshot(g,as_of=AT,built_at=AT,tipster_votes=[e])
    other=build_decision_snapshot(fixture(),as_of=AT,built_at=AT,tipster_votes=[])
    assert g["market_candidates"]["offers"][0]["selection"]=="오버"
    assert g["추천"]["market"]=="승무패"
    assert snapshot["probability"]["final"]!=1
    for key in ("selection_id","offer_id","input_revision_hash","probability"):
        assert snapshot[key]==other[key]
