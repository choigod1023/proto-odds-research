import importlib.util
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
from scripts import measure_market_refresh_memory as benchmark


def test_small_synthetic_fixture_produces_valid_chain_and_unique_games(tmp_path,monkeypatch):
    from runtime_db import RuntimeDatabase
    from prediction_ledger import PredictionLedger
    path=tmp_path/'isolated.sqlite3'
    info=benchmark.create_fixture(path,ROOT/'docs/data/picks_v2.json',live_count=3,past_count=2,prediction_count=8,settlement_count=3,history_count=5)
    assert info['seed_games']==632 and info['synthetic_unique_events']==5
    assert info['ledger_counts']=={'prediction':8,'settlement':3}
    monkeypatch.setenv('PROODD_DB_PATH',str(path))
    db=RuntimeDatabase(); document=db.get_artifact('picks_v2')
    games=document['live']+document['past']; assert len({g['event_id'] for g in games})==5
    assert all('🏟️' in g['synthetic_fixture'] for g in games)
    records=PredictionLedger(tmp_path/'unused').records(); assert len(records)==11
    assert sum(r['record_type']=='settlement' for r in records)==3
    with db.connect() as c: assert c.execute('SELECT COUNT(*) FROM match_results').fetchone()[0]==5


def test_invalid_fixture_counts_fail_before_use(tmp_path):
    with pytest.raises(ValueError):
        benchmark.create_fixture(tmp_path/'empty.sqlite3',ROOT/'docs/data/picks_v2.json',live_count=2,past_count=1,prediction_count=2,settlement_count=1)


def test_isolated_children_preserve_identical_outputs(tmp_path):
    import multiprocessing as mp
    import shutil
    seed=tmp_path/'seed.sqlite3'
    benchmark.create_fixture(seed,ROOT/'docs/data/picks_v2.json',live_count=3,past_count=2,prediction_count=8,settlement_count=3,history_count=5)
    results=[]; ctx=mp.get_context('spawn')
    for mode in ('before','after'):
        path=tmp_path/(mode+'.sqlite3'); shutil.copyfile(seed,path)
        q=ctx.Queue(); process=ctx.Process(target=benchmark.child,args=(mode,path,q)); process.start(); process.join(30)
        if process.is_alive():
            process.terminate(); process.join(); pytest.fail('synthetic child timed out')
        assert process.exitcode==0
        result=q.get(timeout=5); q.close(); assert result.get('exit_code')==0, result
        assert result['settlements_preserved']==3 and result['attached_hits']==3
        assert {'load','ledger_load','ledger_verified','serialize_persist'} <= result['phases'].keys()
        results.append(result)
    assert results[0]['artifact_sha256']==results[1]['artifact_sha256']
    assert results[0]['ledger_sha256']==results[1]['ledger_sha256']
