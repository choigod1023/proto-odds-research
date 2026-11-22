import csv, gzip, importlib.util, json, hashlib
from pathlib import Path
import pytest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('kbo_data_builder',HERE/'build.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)


def test_actual_dataset_contract_and_hashes():
    inv=json.loads((HERE/'inventory.json').read_text(encoding='utf8'))
    rows=[json.loads(x) for x in (HERE/'rows.jsonl').read_text(encoding='utf8').splitlines()]
    labels=[json.loads(x) for x in (HERE/'labels.jsonl').read_text(encoding='utf8').splitlines()]
    assert len(rows)==len(labels)==inv['counts']['fixtures']==2913
    assert len({r['fixture_id'] for r in rows})==len(rows)
    assert all(r['availability_tier']=='retrospective_unproven' for r in rows)
    for path,sha in inv['output_sha256'].items():assert hashlib.sha256((HERE/path).read_bytes()).hexdigest()==sha
    assert len({r['market']['market_id'] for r in rows if r['market']})==inv['counts']['with_market']
    for row,label in zip(rows,labels):
        assert row['fixture_id']==label['fixture_id'] and 'home_score' not in row and 'away_score' not in row
        assert label['market_id']==(row['market']['market_id'] if row['market'] else None)
        if row['market']: assert row['kickoff_at'] and set(row['market']['selections'])=={'home','away'} and row['market']['odds_observed_at'] is None
        for value in row['features'].values():
            if value['availability_basis']=='retrospective_prior_calendar_day_assumption' and value.get('latest_source_date'):
                assert value['latest_source_date'] < row['kickoff_at'][:10] if row['kickoff_at'] else value['latest_source_date'] < str(row['season']+1)


def test_roster_excludes_substitution_and_rejects_missing():
    players=[{'order':i,'code':str(i),'pos':'一'} for i in range(1,10)]
    players.insert(1,{'order':1,'code':'99','pos':'교'})
    assert builder.roster({'data':{'home':players}},'home')==[str(i) for i in range(1,10)]
    assert builder.roster({'data':{'home':players[:-1]}},'home') is None


def test_target_and_same_day_stats_do_not_enter_features(tmp_path,monkeypatch):
    path=tmp_path/'inputs';path.mkdir();out=tmp_path/'out';out.mkdir()
    games=[{'source_fixture_id':k,'date':day,'home':'A','away':'B','hg':h,'ag':a} for k,day,h,a in [('g1','2025-03-01',1,0),('g2','2025-03-02',2,1),('g3','2025-03-02',3,0)]]
    def batter(code):return {'order':int(code),'code':code,'pos':'一','ab':1,'hit':1,'bb':0,'hr':0}
    bat={g['source_fixture_id']:{'date':g['date'],'home':'A','away':'B','home_score':g['hg'],'away_score':g['ag'],'data':{'stadium':'X','home':[batter(str(i)) for i in range(1,10)],'away':[batter(str(i)) for i in range(11,20)]}} for g in games}
    # away batting orders are 1..9, with distinct player IDs
    for b in bat.values():
        for i,v in enumerate(b['data']['away'],1):v['order']=i
    pitchers={g['source_fixture_id']:{'date':g['date'],'home':'A','away':'B','home_score':g['hg'],'away_score':g['ag'],'data':{'home':[{'pcode':'h','name':'H','er':1,'hit':2,'bb':0,'hr':0,'kk':1}], 'away':[{'pcode':'a','name':'V','er':1,'hit':2,'bb':0,'hr':0,'kk':1}]}} for g in games}
    starters=[{'gameId':g['source_fixture_id'],'datetime':g['date']+'T14:00:00','home':'A','away':'B','home_score':g['hg'],'away_score':g['ag'],'home_starter':'H','away_starter':'V'} for g in games]
    with gzip.open(path/'games.gz','wt',encoding='utf8') as f:
        for g in games:f.write(json.dumps(g)+'\n')
    for n,obj in [('batters',bat),('pitchers',pitchers),('starters',starters)]: (path/(n+'.json')).write_text(json.dumps(obj),encoding='utf8')
    (path/'tipster.jsonl').write_text('',encoding='utf8')
    fields=['league','sport','market_family','market_tag','n_way','market_label','home','away','date_text','year','odds','is_void','round','game_no']
    with (path/'market.csv').open('w',encoding='utf8',newline='') as f:csv.DictWriter(f,fieldnames=fields).writeheader()
    monkeypatch.setattr(builder,'OUT',out);monkeypatch.setattr(builder,'CSV',path/'market.csv');monkeypatch.setattr(builder,'PATHS',{'games':path/'games.gz','batters':path/'batters.json','pitchers':path/'pitchers.json','starters':path/'starters.json','domestic_market':path/'market.csv','tipster':path/'tipster.jsonl'})
    builder.build(); before=[json.loads(x) for x in (out/'rows.jsonl').read_text(encoding='utf8').splitlines()]
    assert before[1]['features']['home_team_prior']['value']==before[2]['features']['home_team_prior']['value']
    assert before[1]['features']['home_lineup_prior']['value']==before[2]['features']['home_lineup_prior']['value']
    assert all(r['market'] is None for r in before)
    bat['g2']['data']['home'][0]['hit']=9999; pitchers['g2']['data']['home'][0]['er']=9999
    # Poison the target final score consistently in every input; same-day features remain unchanged.
    games[1]['hg']=999; bat['g2']['home_score']=999; pitchers['g2']['home_score']=999; starters[1]['home_score']=999
    with gzip.open(path/'games.gz','wt',encoding='utf8') as f:
        for g in games:f.write(json.dumps(g)+'\n')
    (path/'batters.json').write_text(json.dumps(bat),encoding='utf8');(path/'pitchers.json').write_text(json.dumps(pitchers),encoding='utf8');(path/'starters.json').write_text(json.dumps(starters),encoding='utf8')
    builder.build(); after=[json.loads(x) for x in (out/'rows.jsonl').read_text(encoding='utf8').splitlines()]
    assert before[1]['features']==after[1]['features'] and before[2]['features']==after[2]['features']


def test_market_selection_rejects_partial_and_different_line(tmp_path):
    fields=['league','sport','market_family','market_tag','n_way','market_label','home','away','date_text','year','odds','is_void','round','game_no']
    base=dict(league='KBO',sport='bs',market_family='승패',market_tag='hm',n_way='2',market_label='',home='A 3',away='1 B',date_text='03.01(토) 14:00',year='2025',odds='1.70,2.00',is_void='False',round='1',game_no='001')
    p=tmp_path/'market.csv'
    with p.open('w',encoding='utf8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows([base,{**base,'game_no':'002','odds':'1.7'},{**base,'game_no':'003','market_label':'H -1.5'}])
    found,counts=builder.read_market(p)
    assert len(found[('2025-03-01','A','B')])==1
    assert counts['market_invalid_or_void']==1
