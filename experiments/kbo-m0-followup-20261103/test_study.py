import copy, gzip, json
from unittest.mock import patch
import pytest
import study as s

def fixture():
    targets=[{"fixture_id":f"g{i}","event_date":f"2024-04-0{i}","home_id":"H","away_id":"A"} for i in (1,2,3)]
    scores=[(3,1),(2,2),(1,2)]
    labels={r["fixture_id"]:{"home_score":h,"away_score":a,"status":"final"} for r,(h,a) in zip(targets,scores)}
    quotes=[{"fixture_id":r["fixture_id"],"date":r["event_date"],"home_id":"H","away_id":"A","winner":s.metrics.winner(labels[r["fixture_id"]]),"price_source":{"prices":{"home":"2.00","away":"1.80"}}} for r in targets]
    preds=[{"fixture_id":r["fixture_id"],"event_date":r["event_date"],"probabilities_3way":{"home":.54,"draw":.1,"away":.36},"probabilities_2way":{"home":.6,"away":.4}} for r in targets]
    return targets,labels,quotes,{"baseline":preds,"equal_ensemble":copy.deepcopy(preds)}

def test_refunds_count_in_roi_denominator_and_missing_prices_are_explicit():
    t,l,q,p=fixture();q[0]["price_source"]["prices"]["home"]="2.50"
    d=s.summarize(p,l,q,t)
    assert d["price"]["baseline"]["net_units"]=="0.50"
    assert d["price"]["baseline"]["roi"]==pytest.approx(.5/3)
    assert d["price"]["baseline"]["refunds"]==1
    assert d["price_paired_ensemble_minus_baseline"] is None
    d=s.summarize(p,l,[q[0],q[2]],t)
    assert d["coverage"]["missing_draws"]==1 and d["coverage"]["excluded_ids"]==["g2"]

def test_identity_duplicate_and_probability_mismatch_fail_closed():
    t,l,q,p=fixture();q[0]["away_id"]="OTHER"
    with pytest.raises(ValueError,match="identity"):s.summarize(p,l,q,t)
    t,l,q,p=fixture();p["equal_ensemble"].append(copy.deepcopy(p["equal_ensemble"][0]))
    with pytest.raises(ValueError,match="duplicate"):s.summarize(p,l,q,t)
    t,l,q,p=fixture();p["baseline"][0]["probabilities_2way"]["home"]=.9
    with pytest.raises(ValueError,match="conditional"):s.summarize(p,l,q,t)

def test_ensemble_averages_unconditional_before_conditioning():
    t,l,q,p=fixture();parts=[]
    for home,draw,away in ((.4,.5,.1),(.1,.1,.8),(.2,.2,.6)):
        rows=copy.deepcopy(p["baseline"])
        for r in rows:r["probabilities_3way"]={"home":home,"draw":draw,"away":away}
        parts.append(rows)
    got=s.average(parts,t)[0]
    assert got["probabilities_2way"]["home"]==pytest.approx(.7/2.2)
    assert got["probabilities_2way"]["home"]!=pytest.approx((.8+1/9+.25)/3)

def test_pinned_reader_preserves_jsonl_boundaries_and_hashes():
    raw=gzip.compress(b'{"a":1}\n{"a":2}\n',mtime=0)
    with patch.object(s.subprocess,"check_output",return_value=raw):
        assert s.fixed("TEST","input.jsonl.gz")==[{"a":1},{"a":2}]
    assert s.SOURCES.pop("TEST:input.jsonl.gz")==s.hashlib.sha256(raw).hexdigest()

def test_committed_year_results_match_known_denominators():
    d=json.loads((s.P/"results.json").read_bytes())
    assert [(z["coverage"]["target_n"],z["coverage"]["price_n"]) for z in d["annual"].values()]==[(728,714),(731,676),(709,667)]
    assert all(len(d["halves"][str(y)])==2 for y in (2024,2025,2026))
    assert d["parameters_or_weights_reselected"] is False and d["prospective_verified_settlements"]==0


def test_full_frozen_scoring_replays_without_overwriting(capsys):
    before=(s.P/"results.json").read_bytes()
    with patch.object(s.subprocess,"check_output",side_effect=AssertionError("replay must not require old Git objects")):
        s.main(verify=True)
    assert (s.P/"results.json").read_bytes()==before
    assert "2024" in capsys.readouterr().out


def test_corrupt_frozen_blob_is_not_replaced_with_current_git(tmp_path,monkeypatch):
    manifest=(s.P/"replay-sources.json").read_bytes();(tmp_path/"replay-sources.json").write_bytes(manifest)
    key,entry=next(iter(json.loads(manifest)["sources"].items()))
    target=tmp_path/entry["relative_path"];target.parent.mkdir(parents=True);target.write_bytes(gzip.compress(b"changed",mtime=0))
    monkeypatch.setattr(s,"P",tmp_path)
    with patch.object(s.subprocess,"check_output",side_effect=AssertionError("must fail closed")):
        with pytest.raises(ValueError,match="replay blob changed"):s.fixed(*key.split(":",1))


def test_corrupt_replay_manifest_is_rejected(tmp_path,monkeypatch):
    (tmp_path/"replay-sources.json").write_bytes((s.P/"replay-sources.json").read_bytes()+b" ")
    monkeypatch.setattr(s,"P",tmp_path)
    with pytest.raises(ValueError,match="replay manifest changed"):s.fixed("TEST","input.jsonl.gz")
