import copy,gzip,importlib.util,json,sys
from datetime import datetime,timezone,timedelta
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parent));import forward as f
NOW=datetime(2026,11,3,4,0,tzinfo=timezone.utc)
def snapshot():
 rows=f.core.rows("test-actual.jsonl.gz");row=copy.deepcopy(rows[-1]);start=NOW+timedelta(days=2,hours=8);gid="20261105test0";fid=gid+"2026";row.update(fixture_id=fid,event_date="2026-11-05",season=2026)
 event={"official_game_id":gid,"season":2026,"home_id":row["home_id"],"away_id":row["away_id"],"kickoff_at":start.isoformat()};evidence={}
 for kind in ("announcement","odds","history"):
  body={"test_only":True,"kind":kind};evidence[kind]={"uri":"https://www.koreabaseball.com/test" if kind!="odds" else "https://www.betman.co.kr/test","received_at":NOW.isoformat(),"body":body,"sha256":f.digest(f.canonical(body))}
 return {"event":event,"market":{"market_id":"test-only","kind":"full_game_2way","odds":{"home":"1.80","away":"1.90"},"settlement_rule":"draw_void_refund"},"row":row,"announcement":{"lineup_confirmed":True,"pitcher_announcement_confirmed":True,"basis":"official_confirmed_pregame_announcement","roster":copy.deepcopy(row["roster"])},"evidence":evidence}
def fake_infer(row):
 r={"fixture_id":row["fixture_id"],"event_date":row["event_date"],"probabilities_3way":{"home":.54,"draw":.1,"away":.36},"probabilities_2way":{"home":.6,"away":.4}}
 return {"baseline":r,"equal_ensemble":copy.deepcopy(r)},{"test_only":"fake"}
def test_real_frozen_inference_and_receipt_shape():
 x=snapshot();f.validate_snapshot(x,NOW);p,h=f.forecast(x["row"])
 assert set(p)=={"baseline","equal_ensemble"} and set(h)=={"baseline","shrink","nb03"}
 assert all(abs(sum(r["probabilities_3way"].values())-1)<1e-9 for r in p.values())
def test_late_capture_and_postgame_roster_rejected():
 x=snapshot();start=f.moment(x["event"]["kickoff_at"])
 with pytest.raises(ValueError,match="T-30"):f.validate_snapshot(x,start-timedelta(minutes=29))
 x["announcement"]["basis"]="postgame_actual_start_verified"
 with pytest.raises(ValueError,match="pregame announcement"):f.validate_snapshot(x,NOW)
def test_same_day_history_and_unverified_odds_rejected():
 x=snapshot();source=x["row"]["raw_features"]["home_lineup_strength"]["value"];source[0]["history"][0]["date"]=x["row"]["event_date"]
 with pytest.raises(ValueError,match="history rejected"):f.validate_snapshot(x,NOW)
 x=snapshot();x["market"]["kind"]="total_goals"
 with pytest.raises(ValueError,match="two-way"):f.validate_snapshot(x,NOW)
def test_first_capture_immutable_and_formal_status_pending(tmp_path):
 p=tmp_path/"ledger.jsonl";x=snapshot();a=f.capture(x,p,clock=lambda:NOW,infer=fake_infer);old=p.read_bytes()
 with pytest.raises(ValueError,match="already frozen"):f.capture(x,p,clock=lambda:NOW,infer=fake_infer)
 assert p.read_bytes()==old and a["eligible_for_effectiveness_validation"] is False and f.status(p)["source_verified_paired_settlements"]==0
 assert not p.with_suffix('.jsonl.lock').exists()
def test_later_result_refund_and_source_verification_pending(tmp_path):
 p=tmp_path/"ledger.jsonl";x=snapshot();a=f.capture(x,p,clock=lambda:NOW,infer=fake_infer);later=f.moment(x["event"]["kickoff_at"])+timedelta(hours=4);body={"test_only":True};e={"uri":"https://www.koreabaseball.com/test","received_at":later.isoformat(),"body":body,"sha256":f.digest(f.canonical(body))};r={"event_key":a["event_key"],"official_game_id":x["event"]["official_game_id"],"season":2026,"home_id":x["event"]["home_id"],"away_id":x["event"]["away_id"],"game_date":x["row"]["event_date"],"status":"final","home_score":2,"away_score":2,"evidence":e};s=f.settle(r,p,clock=lambda:later)
 assert all(z["net_units"]=='0' and z["refund"] for z in s["paper_profits"].values())
 assert f.status(p)["local_staged_settlements"]==1 and f.status(p)["source_verified_paired_settlements"]==0
 with pytest.raises(ValueError,match="repeated result"):f.settle(r,p,clock=lambda:later)
def test_chain_tampering_and_bad_timezone_rejected(tmp_path):
 p=tmp_path/"ledger.jsonl";f.capture(snapshot(),p,clock=lambda:NOW,infer=fake_infer);raw=p.read_bytes().replace(b'"market_id":"test-only"',b'"market_id":"modified"');p.write_bytes(raw)
 with pytest.raises(ValueError,match="chain differs"):f.read_ledger(p)
 with pytest.raises(ValueError,match="timezone"):f.moment("2026-11-05T08:00:00")


def test_nested_future_receipt_rejected_and_t30_boundary_allowed():
 x=snapshot();x["row"]["raw_features"]["home_lineup_strength"]["value"][0]["history"][0]["received_at"]=(NOW+timedelta(seconds=1)).isoformat()
 with pytest.raises(ValueError,match="history receipt after capture"):f.validate_snapshot(x,NOW)
 x=snapshot();f.validate_snapshot(x,f.moment(x["event"]["kickoff_at"])-timedelta(minutes=30))

def test_inference_crossing_deadline_does_not_write(tmp_path):
 x=snapshot();start=f.moment(x["event"]["kickoff_at"]);ticks=iter([start-timedelta(minutes=31),start-timedelta(minutes=29)])
 with pytest.raises(ValueError,match="completed later"):f.capture(x,tmp_path/"ledger.jsonl",clock=lambda:next(ticks),infer=fake_infer)
 assert not (tmp_path/"ledger.jsonl").exists()
