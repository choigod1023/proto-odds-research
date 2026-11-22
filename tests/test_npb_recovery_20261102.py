import importlib.util, json, pathlib, sys
import pytest
DIR = pathlib.Path('experiments/npb-recovery-20261102')
sys.path.insert(0, str(DIR))
from archived_2023 import parse, load

def test_three_real_pages_and_postgame_receipt():
    rows=load()
    assert len(rows)==3
    game=next(r for r in rows if r['date']=='2023-03-30')
    assert (game['home'],game['away'])==('닛폰햄','라쿠텐')
    assert (game['home_starter_name'],game['away_starter_name'])==('加藤貴','田中将')
    assert (game['home_score'],game['away_score'])==(1,3)
    assert game['official_player_id'] is None
    assert game['source_observed_at']>'2023-03-30'

def test_hash_corruption_rejected():
    p=next((DIR/'raw').glob('07ca*.json')); meta=json.loads(p.read_text(encoding='utf8'))
    with pytest.raises(ValueError): parse(p.with_suffix('.html').read_bytes()+b'x',meta)

def test_daily_index_is_not_a_game():
    p=next((DIR/'raw').glob('e522*.json')); meta=json.loads(p.read_text(encoding='utf8'))
    assert parse(p.with_suffix('.html').read_bytes(),meta) is None

def test_output_partition_and_no_pregame_claim():
    data=[json.loads(x) for x in (DIR/'cohort.jsonl').read_text(encoding='utf8').splitlines()]
    assert len(data)==935 and len({x['game_id_string'] for x in data})==935
    found=[x for x in data if x['reason']=='retrospective_both_starter_names_only']
    assert len(found)==3 and all(x['official_result']=='score_matches_original' for x in found)
    assert all(x['pregame_receipt']=='not_verified' and x['price_link']=='not_checked' for x in data)

def test_daily_index_identifies_unique_game_and_doubleheader():
    from archived_2023 import daily_pairs
    p=next((DIR/'raw').glob('e522*.html'))
    pairs=daily_pairs(p.read_bytes())
    assert [x for x in pairs if x[1]==('g','d')]==[('s2023033100942.html',('g','d'))]
    from recover import unique_daily
    assert unique_daily(pairs,('g','d'),'s2023033100942.html')
    assert not unique_daily(pairs+[('s2023033100999.html',('g','d'))],('g','d'),'s2023033100942.html')
