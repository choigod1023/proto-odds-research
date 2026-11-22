import copy,gzip,hashlib,importlib.util,json
from datetime import date,timedelta
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('kbo_projected_data',HERE/'build_prior_projection.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


def test_baseball_outs_conversion():
    for text,outs in [('5.2',17),('5 ⅔',17),('1 ⅓',4),('0',0),('8',24),('0 ⅔',2)]: assert p.innings_outs(text)==outs
    for text in ('5.3','1.5','unknown','⅓'):assert p.innings_outs(text) is None


def make_input():
    games=[];bat={};pit={};starters={}
    for i in range(7):
        day=(date(2025,4,1)+timedelta(days=i)).isoformat();gid='g'+str(i)
        g={'source_fixture_id':gid,'date':day,'home':'A','away':'B','hg':i,'ag':1};games.append(g)
        b={'data':{'stadium':'Park'}}; q={'data':{}}
        st={}
        for side,prefix in [('home','h'),('away','a')]:
            b['data'][side]=[{'order':x,'code':prefix+str(x),'pos':'X','ab':3,'hit':1,'bb':0,'hr':0} for x in range(1,10)]
            q['data'][side]=[{'pcode':prefix+str(i%5),'name':prefix+str(i%5),'inn':'5 ⅔','er':1,'r':1,'hit':2,'bb':1,'hr':0,'kk':2,'bf':70},{'pcode':prefix+'r','inn':'1 ⅓','bf':20}]
            st[side+'_starter']=prefix+str(i%5)
        bat[gid]=b;pit[gid]=q;starters[gid]=st
    return games,bat,pit,starters


def test_current_player_score_and_stats_poison_invariant():
    games,bat,pit,starters=make_input(); baseline=p.generate(games,bat,pit,starters)
    poisoned=copy.deepcopy((games,bat,pit,starters));g,b,q,s=poisoned
    g[-1]['hg']=999;g[-1]['ag']=888
    for side in ('home','away'):
        b['g6']['data'][side][0]['code']='TARGET_CHANGED'
        b['g6']['data'][side][0]['hit']=99999
        q['g6']['data'][side][0]['pcode']='OTHER_TARGET_STARTER'
        q['g6']['data'][side][0]['r']=99999
        s['g6'][side+'_starter']='OTHER_NAME'
    changed=p.generate(g,b,q,s)
    assert baseline[-1]==changed[-1]
    assert baseline[-1]['projection']['home_lineup']['basis']=='projected_from_prior_games'
    assert baseline[-1]['projection']['home_starter']['player_id']=='h1'
    assert baseline[-1]['features']['home_starter_strength']['value']['appearances'][0]['outs']==17


def test_same_day_changes_do_not_leak():
    games,bat,pit,starters=make_input();g=copy.deepcopy(games[-1]);g['source_fixture_id']='g6b';games.append(g)
    bat['g6b']=copy.deepcopy(bat['g6']);pit['g6b']=copy.deepcopy(pit['g6']);starters['g6b']=copy.deepcopy(starters['g6'])
    baseline=p.generate(games,bat,pit,starters)
    bat['g6']['data']['home'][0]['hit']=999;pit['g6']['data']['home'][0]['r']=999;games[-2]['hg']=999
    modified=p.generate(games,bat,pit,starters)
    assert next(x for x in baseline if x['fixture_id']=='g6b')==next(x for x in modified if x['fixture_id']=='g6b')


def test_real_features_unique_hash_and_no_future_source_ids():
    manifest=json.loads((HERE/'prior-projected-manifest.json').read_text(encoding='utf8'))
    path=HERE/'prior-projected-features.jsonl.gz'; assert hashlib.sha256(path.read_bytes()).hexdigest()==manifest['output_sha256']
    with gzip.open(path,'rt',encoding='utf8') as f: rows=[json.loads(x) for x in f]
    with gzip.open(p.GAMES,'rt',encoding='utf8') as f: games=[json.loads(x) for x in f]
    days={g['source_fixture_id']:g['date'] for g in games}
    assert len(rows)==len(days)==2913 and len({r['fixture_id'] for r in rows})==2913
    for r in rows:
        day=days[r['fixture_id']]; assert r['scenario']=='projected_from_prior_games'
        assert r['completed_at'] is None and r['received_at'] is None
        for x in r['features'].values():
            assert x['completed_at'] is None and x['received_at'] is None
            assert all(days[key]<day for key in x['source_fixture_ids'])
            assert x['latest_source_date'] is None or x['latest_source_date']<day
        for side in ('home','away'):
            x=r['projection'][side+'_lineup'];assert all(days[key]<day for key in x['source_fixture_ids'])
            x=r['projection'][side+'_starter'];assert all(days[v['fixture_id']]<day for v in x['rotation_candidates'])
