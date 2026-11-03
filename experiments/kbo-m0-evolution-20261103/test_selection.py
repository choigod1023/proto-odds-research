import sys,copy
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parent/"common"));import core

def test_fixed_validation_selection_and_missing_fixture_rejection():
 d=core.load();p=core.baseline_reference()["development_predictions"];r=core.choose({"baseline":p},d["labels"])
 assert r["selected"]=="baseline" and r["metrics"]["baseline"]["n"]==731
 with pytest.raises(ValueError,match="complete fixed2025"):core.choose({"baseline":p[:-1]},d["labels"])
 with pytest.raises(ValueError,match="populations differ"):core.choose({"baseline":p,"candidate":p[:-1]},d["labels"])

def test_duplicate_prediction_rejected():
 d=core.load();p=core.baseline_reference()["development_predictions"]
 with pytest.raises(ValueError,match="duplicate"):core.choose({"baseline":p+[p[0]]},d["labels"])

def test_raw_to_model_normalization_does_not_mutate_input():
 d=core.load();r=d["train"][100];original=copy.deepcopy(r);x=core.normalize(r);x["features"]["home_lineup_strength"]["value"]=-99
 assert r==original


def test_invalid_or_inconsistent_probability_vectors_rejected():
 labels=core.unique(core.rows("development-labels.jsonl.gz"));p=copy.deepcopy(core.baseline_reference()["development_predictions"][0]);bad=copy.deepcopy(p);bad["probabilities_2way"]["home"]=float("nan")
 with pytest.raises(ValueError,match="probability vector"):core.metrics([bad],labels)
 bad=copy.deepcopy(p);bad["probabilities_2way"]={"home":.99,"away":.01}
 with pytest.raises(ValueError,match="inconsistent"):core.metrics([bad],labels)
