from pathlib import Path
import importlib.util,json
import pytest
P=Path('experiments/nba-official-information-20261103/audit.py')
spec=importlib.util.spec_from_file_location('nba_official_audit',P)
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def test_real_official_html_three_periods():
    paths=sorted((P.parent/'raw').glob('box-*.html'))
    rows=[m.extract_postgame(p) for p in paths]
    assert {r['game_id'] for r in rows} == {'0022500274','0022500585','0022501197'}
    assert all(r['namespace']=='postgame_only' and all(len(t['postgame_starters'])==5 for t in r['teams']) for r in rows)
    assert len({r['game_code'][:6] for r in rows})==3

def test_reject_incomplete_lineup(tmp_path):
    players=[{'personId':i,'firstName':'A','familyName':str(i),'position':'G'} for i in range(4)]
    team={'teamId':1,'teamTricode':'ABC','players':players}
    game={'gameId':'0022500001','gameStatus':3,'awayTeam':team,'homeTeam':team}
    payload={'props':{'pageProps':{'game':game}}}
    p=tmp_path/'bad.html'; p.write_text('<script id="__NEXT_DATA__" type="application/json">'+json.dumps(payload)+'</script>',encoding='utf8')
    with pytest.raises(ValueError,match='starter five'):m.extract_postgame(p)
