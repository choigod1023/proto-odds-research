import json, pathlib, sys
DIR=pathlib.Path('experiments/npb-recovery-20261102'); sys.path.insert(0,str(DIR))
from expanded_recover import index_complete, read_documents

def test_original_sample_survives_and_denominator_is_preserved():
    rows=[json.loads(x) for x in (DIR/'cohort-expanded.jsonl').read_text(encoding='utf8').splitlines()]
    assert len(rows)==935 and len({x['game_id_string'] for x in rows})==935
    sample={'20230330RTNH0','20230331HIYA0','20230331JNYO0'}
    restored={x['game_id_string'] for x in rows if x['status']=='retrospective_names_recovered'}
    assert sample<=restored
    assert all(x['actual_pregame_receipt']=='not_verified' for x in rows)
    assert all(x['same_price_product_time']=='not_checked' for x in rows)

def test_raw_documents_verified_and_index_incomplete_rejected():
    docs=read_documents()
    index=next(b for u,(b,m,p) in docs.items() if u.endswith('gm20230331.html'))
    assert index_complete(index)
    assert not index_complete(index.replace(b'pet2023_g_',b'unknown_logo_'))

def test_status_and_denominators_reconcile():
    manifest=json.loads((DIR/'manifest-expanded.json').read_text(encoding='utf8'))
    assert sum(manifest['statuses'].values())==935
    assert manifest['network_attempts']<=300
    assert manifest['official_player_id_pairs_recovered']==0
    assert manifest['actual_pregame_receipt_recovered']==0
    assert manifest['same_price_product_time'] is None
