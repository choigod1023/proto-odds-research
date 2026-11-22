import hashlib
import json
import math
from types import SimpleNamespace
import pytest
from scripts.audit_nba_player_projection import day_of, shooting, verified


def test_cold_players_use_prior_without_target_participation():
    ref=SimpleNamespace(clip=lambda p:min(1-1e-6,max(1e-6,p)),
        logit=lambda p:math.log(p/(1-p)),sigmoid=lambda x:1/(1+math.exp(-x)))
    attempts=[10,20,10,10,0,10,20,10,0]
    makes=[8,10,4,3,0,4,7,3,0]
    rotation={'past-player-a':120.,'past-player-b':120.}
    for spatial in (False,True):
        values=shooting(rotation,{},attempts,makes,240.,attempts,makes,spatial,ref)
        assert values == pytest.approx([25/50,14/40])


def test_time_boundary_uses_kst_calendar():
    assert day_of('2025-01-01T23:00:00Z') == '2025-01-02'
    assert day_of('2025-01-01T14:59:59Z') == '2025-01-01'


def test_source_receipt_rejects_changed_bytes(tmp_path):
    path=tmp_path/'event.json'
    body=b'{"fixture":"one"}'
    path.write_bytes(body)
    path.with_suffix('.receipt.json').write_text(json.dumps(dict(status=200,sha256=hashlib.sha256(body).hexdigest())))
    assert verified(path) == {'fixture':'one'}
    path.write_bytes(b'{"fixture":"two"}')
    with pytest.raises(ValueError,match='source_hash'):
        verified(path)
