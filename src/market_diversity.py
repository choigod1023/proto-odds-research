"""Market-specific tipster research candidates; never replace a frozen pick.

No fitted probabilities, implied vote probabilities, HTTP or wallet operations.
Existing winner-policy and highlight eligibility stay separate.
"""
from collections import defaultdict
from datetime import timedelta
import math
from fixed_tipster_hybrid import stamp
from public_tipster_market_candidates import match_candidate
from public_tipster_votes import SOURCE,fixture_key,kickoff,safe_url

VERSION="tipster-market-candidates-v1"

def build_market_candidates(game, rows, as_of, built_at):
    result={"version":VERSION,"candidate_only":True,"validated_edge":False,
            "affects_selection":False,"affects_probability":False,"offers":[],"rejected":{}}
    try:
        start=kickoff(game);cutoff=min(stamp(as_of),start-timedelta(minutes=30))
        if max(stamp(as_of),stamp(built_at))>start-timedelta(minutes=30):
            return dict(result,status="after_t30")
        fixture=fixture_key(game)
    except (ValueError,KeyError,TypeError,AttributeError):return dict(result,status="unknown_fixture")
    groups=defaultdict(list);electorate=defaultdict(list)
    for row in rows or []:
        if not isinstance(row,dict) or row.get("market_type") not in {"full_game_total","full_game_handicap"}:continue
        try:
            observed=stamp(row["observed_at"])
            if not (row.get("source")==SOURCE and row.get("event_type")=="first_observed"
                    and row.get("eligible_pre_event") is True and row.get("candidate_only") is True
                    and row.get("provenance")=="live_http_observation" and row.get("fixture")==fixture
                    and row.get("market_type")=="full_game_"+str(row.get("family"))
                    and type(row.get("line")) in (int,float) and math.isfinite(row["line"])
                    and row.get("voter") and row.get("pick_id") and safe_url(row.get("source_url",""))
                    and start-timedelta(days=3)<=observed<=cutoff):continue
            electorate[(row["voter"],row["family"],row.get("period"),row.get("settlement"))].append(row)
        except (ValueError,KeyError,TypeError,AttributeError):continue
    for votes in electorate.values():
        earliest=min(stamp(r["observed_at"]) for r in votes)
        same=[r for r in votes if stamp(r["observed_at"])==earliest]
        meanings={(r.get("line"),r.get("side")) for r in same}
        if len(meanings)!=1:
            result["rejected"]["ambiguous_first_author_vote"]=result["rejected"].get("ambiguous_first_author_vote",0)+1
            continue
        row=min(same,key=lambda r:r["pick_id"])
        try:
            option=match_candidate(row,game)
            if option is None:
                result["rejected"]["market_line_or_scope_mismatch"]=result["rejected"].get("market_line_or_scope_mismatch",0)+1
                continue
            key=(str(option.get("게임번호","")),option["market"],option.get("label") or "")
            groups[key].append((row,option))
        except (ValueError,KeyError,TypeError,AttributeError):continue
    for (number,market,label),entries in sorted(groups.items()):
        by_author=defaultdict(list)
        for row,option in entries:by_author[row["voter"]].append((row,option))
        first=[]
        for author,votes in by_author.items():
            earliest=min(stamp(r["observed_at"]) for r,o in votes)
            same=[(r,o) for r,o in votes if stamp(r["observed_at"])==earliest]
            if len({o["선택"] for r,o in same})==1:first.append(min(same,key=lambda item:item[0]["pick_id"]))
        counts=defaultdict(int)
        for r,o in first:counts[o["선택"]]+=1
        leaders=[side for side,n in counts.items() if n==max(counts.values())] if counts else []
        side=leaders[0] if len(leaders)==1 else None
        option=next((o for r,o in first if o["선택"]==side),None)
        result["offers"].append({"game_no":number,"market":market,"label":label,
            "line":entries[0][0]["line"],"period":"full_game",
            "settlement":entries[0][0]["settlement"],
            "settlement_scope":(option or entries[0][1]).get("settlement_scope"),
            "selection":side,"authors":len(first),"vote_counts":dict(counts),
            "odds":option.get("배당") if option else None,
            "status":"candidate" if side else "ambiguous_or_tied",
            "price_receipt_verified":False,
            "evidence":[{"author":r["voter"],"observed_at":r["observed_at"],
                         "source_url":r["source_url"],"text":r.get("evidence","")[:200]} for r,o in first]})
    return dict(result,status="candidates" if result["offers"] else "no_eligible_market_candidates")
