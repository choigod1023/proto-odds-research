"""Independently audit frozen KBO prior-history repair and eight comparisons."""
import base64, collections, gzip, hashlib, json, math, re, subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path
import numpy as np

REVIEW=Path(__file__).resolve().parents[2]
DATA=Path('C:/Users/user/Documents/Codex/kbodata'); SOURCE=Path('C:/Users/user/Documents/Codex/kbor')
MODEL=Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/kbo-player-model-20261103'); ODDS=Path('C:/Users/user/Documents/Codex/kbo-official-odds-20261103')
OFFICIAL=Path('C:/Users/user/Documents/Codex/klu')
D=Path('experiments/kbo-player-data-20261103');S=Path('experiments/kbo-official-record-gap-20261103');M=Path('experiments/kbo-player-model-20261103'); O=Path('experiments/kbo-official-odds-20261103/audit-original-2026')
INPUT='0d980b40697997021256be9f42f6fd254b3c1049';HANDOFF='13291d23e855e0d500327cb39ac64a480e395837';SRC='d11367b7b37f3ea34f4c08987ca042af9235575e';FREEZE='c91cc13d55360706fdebf4b8b970f1d2b2589d25';RESULT='b160d3d186002b90ff00117543bdc3fc19d3e866';OLD='29b3ebe623b2260d28139ebe3ef422ed442cfacf'
HASH={'projected':'8b090ed9b204a50584c5e2a854bd0c6210a133a8518de78db51ca8f4217aa23f','official_batting_only':'8e8bc1850c18af2e258a26805e3090abce621264adf15b0b6d1d748ab57a8e06','official_starter_only':'65b4cad44b30e8185760dbabbcaebe4dd26f29dffa3ac7fe57dd7e05cc05c757','official_batting_and_starter':'82a1e8630fafe2e790957900e981512fdfc8e70ba536f5ced45ac785f79dc706','incomplete_batters':'9132f681b25a8bb413fe03f1d4dfdb8a58b046f0ec9f422278c2c7d4806a2ef4'}
N=('original_projected','official_batting_only','official_starter_only','official_batting_and_starter'); PLAYER={s+'_'+x for s in ('home','away') for x in ('lineup_strength','starter_strength','bullpen_load')}
def sha(b):return hashlib.sha256(b).hexdigest()
def git(repo,commit,path):return subprocess.check_output(['git','show',f'{commit}:{path.as_posix()}'],cwd=repo)
def pin(repo,commit,path,want=None,work=True):
    b=git(repo,commit,path)
    if work:assert (repo/path).read_bytes()==b,str(path)
    if want:assert sha(b)==want,(str(path),sha(b))
    return b
def rows(b):return [json.loads(x) for x in gzip.decompress(b).splitlines()]
def idx(a):
    d={x['fixture_id']:x for x in a};assert len(d)==len(a);return d
def close(a,b,tol=1e-10):assert abs(a-b)<tol,(a,b)
def nat(x):return type(x) is int and x>=0
def outs(x):
    m=re.fullmatch(r'(\d+)(?:\.(\d)|\s*([⅓⅔]))?',str(x).strip())
    if not m:return None
    q=m[2] or {'⅓':'1','⅔':'2'}.get(m[3],'0');return int(m[1])*3+int(q) if q in ('0','1','2') else None
def scalar(k,v):
    if v is None:return None
    if 'lineup' in k:
        a=[x for p in v for x in p['history'] or [] if all(nat(x.get(z)) for z in ('ab','hit','bb')) and x['hit']<=x['ab']];den=sum(x['ab']+x['bb'] for x in a)
        return sum(x['hit']+x['bb'] for x in a)/den if den else None
    if 'starter' in k:
        a=[x for x in v['appearances'] if nat(x.get('er')) and nat(x.get('outs'))];den=sum(x['outs'] for x in a)
        return 27*sum(x['er'] for x in a)/den if den else None
    g=v['games'];return float(sum(x['relief']['outs'] for x in g)) if g and all(nat((x.get('relief') or {}).get('outs')) for x in g) else None
def rates(row,feature,m):
    out=[]
    for own,opp in [('home','away'),('away','home')]:
        names=[own+'_team_strength',opp+'_team_strength',own+'_rest_days',opp+'_rest_days','park_factor',own+'_lineup_strength',opp+'_starter_strength',opp+'_bullpen_load'];v=[feature[k] for k in names];t=m['teams']
        x=[1.,float(own=='home')]+[float(row[own+'_id']==z) for z in t]+[float(row[opp+'_id']==z) for z in t]
        x += [(n-m['mean'][i])/m['scale'][i] if n is not None else 0. for i,n in enumerate(v)];x += [float(n is None) for n in v]
        assert len(x)==len(m['beta']) and m['alpha']==100
        out.append(math.exp(math.fsum(a*b for a,b in zip(x,m['beta']))))
    return out
def prob(h,a):
    p=[math.exp(-h)];q=[math.exp(-a)]
    for i in range(1,80):p.append(p[-1]*h/i);q.append(q[-1]*a/i)
    return [math.fsum(p[i]*q[j] for i in range(80) for j in range(80) if (i>j if k==0 else i==j if k==1 else i<j)) for k in range(3)]
def choose(p,classes):return max(classes,key=p.get)
def outcome(y):return 'home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw'

contract=git(REVIEW,'497cf651',Path('experiments/kbo-player-challenger-20261103/fresh-history-comparison-contract.json'));assert sha(contract)=='781ac8cea06ef5a645e8b3075208083f5a460c622c329fba6f05be10c76cc903'
manifest=json.loads(pin(DATA,INPUT,D/'fresh-history-2026-manifest.json','5b0ea2b96787f04189330d6f3ecc55a18e846400d7afde93cc857b64fe8483e3',False))
final_manifest=json.loads(pin(DATA,HANDOFF,D/'fresh-history-2026-manifest.json'))
assert manifest['outputs_sha256']==final_manifest['outputs_sha256']==HASH and manifest['source_commit']==SRC
assert {k:v for k,v in final_manifest.items() if k!='counts'}=={k:v for k,v in manifest.items() if k!='counts'}
assert {k:v for k,v in final_manifest['counts'].items() if k!='batter_zero_pa'}==manifest['counts'] and final_manifest['counts']['batter_zero_pa']==518
raw={k:pin(DATA,INPUT,D/('fresh-history-2026-'+('incomplete-batters' if k=='incomplete_batters' else k)+'.jsonl.gz'),h) for k,h in HASH.items()}
fresh={k:idx(rows(b)) for k,b in raw.items() if k!='incomplete_batters'};incomplete=rows(raw['incomplete_batters'])
source_files={'artifacts/normalized-records.jsonl.gz':'e9d84a6ee8dd3d0262fa6ef04a8fc6e23c79b6b5b04f52fc85a4223b41bc3334','v2/artifacts/supplement-v2.jsonl.gz':'0293a109c20c3f3344215dd1bd7bfad5e55f2755ada22e01e3c0a5329fd67c84','artifacts/raw-responses.jsonl.gz':'8aeaf579f7ac84a642c3017810f8cf42c101bda2f6290a7fa3779a1d6fa72500','v2/artifacts/official-daily-raw.jsonl.gz':'53ed7b68ee3c8c2f642f114bff7138d28d80c01932b1841911e19032a543ac39'}
source={k:pin(SOURCE,SRC,S/k,v) for k,v in source_files.items()};v1=idx(rows(source['artifacts/normalized-records.jsonl.gz']));v2=idx(rows(source['v2/artifacts/supplement-v2.jsonl.gz']));assert len(v1)==256 and set(v1)==set(v2)
raw_hashes=set()
for name in ('artifacts/raw-responses.jsonl.gz','v2/artifacts/official-daily-raw.jsonl.gz'):
    for entry in rows(source[name]):
        b=base64.b64decode(entry['raw_base64'],validate=True);e=entry['metadata'];assert len(b)==e['bytes'] and sha(b)==e['sha256'];raw_hashes.add(e['sha256'])
base=idx(rows(pin(MODEL,RESULT,M/'phase2/features.jsonl.gz','346edd84d4388f99cc9ad23f7ad530560445b4bc06b6f841b745dabffa1a8b42')))
parameters=json.loads(pin(MODEL,RESULT,M/'phase2/predictions.json','d841cd9549a9dab4f3c9612e2fbdea84b9f914b390da4f9c80c0d9e2b3e5d317'))['models']['player']
pred=json.loads(pin(MODEL,FREEZE,M/'fresh-history/predictions.json','9bcb16da33843f3e60b869c62d4b2cb67f65e346b87c1be215e49a2b9b5c1f0e')); old=json.loads(pin(MODEL,OLD,M/'factorial/predictions.json','669a0016f07275ed9136aa55fdf9f17c96f2c497a32d323a3cad9a2a0dcbab17'));result=json.loads(pin(MODEL,RESULT,M/'fresh-history/results.json','0e4cfa3398a5cf07d99fb17fab6e533cb52b55be6a1b8293e5c92b92a3f94391'))
assert subprocess.run(['git','merge-base','--is-ancestor',FREEZE,RESULT],cwd=MODEL).returncode==0
assert pred['eligible_pregame'] is False and pred['historical_receipt_verified'] is False and result['actual_wallet_replay'] is False
frozen=idx(pred['predictions']);older=idx(old['predictions']);assert len(frozen)==709 and set(frozen)==set(older) and all(set(x)==set(older) for x in fresh.values())
archive=rows(pin(DATA,INPUT,Path('data/model_candidates/kbo/archived-games.jsonl.gz'),'c1561a252f226edcfaceffefedc0295394b2905a7368078b96cf71ba76389fce'));games={g['source_fixture_id']:g for g in archive};assert len(games)==len(archive)
oldbat=json.loads(pin(DATA,INPUT,Path('data/raw/detail/kbo_batters_indiv_2023_2026.json'),'24bdea9094046bdab4967d314c043d548a252599b652009d5ffdabd341cc84e8'))
oldpit=json.loads(pin(DATA,INPUT,Path('data/raw/detail/kbo_baseball_2023_2026.json'),'c87faa6a43140dad067860086b00140fe8f05883a9e42121fd2e8f7233775a66'))
oldprior=idx(rows(pin(DATA,INPUT,D/'prior-projected-features.jsonl.gz','eca78a16ca59ccc1de6eca47713acded513541c59cddbbb4d8dd193e7c5b27ae')))
official=idx([json.loads(x) for x in pin(OFFICIAL,'b24739cf',Path('experiments/kbo-official-lineups-20261103/official-lineups-2026.jsonl'),'f82acede0093fa2a6a2176018a812ac9c279ad893d3ffb3bed9a16cb37b6c5d3').splitlines()])
starter_rows=json.loads(pin(DATA,INPUT,Path('data/raw/kbo_starters.json'),'8e9a415c76c72dc6f0a57e284e2f1ea22dad42c7bb904d54f8dce08f4754c606')); starts=collections.defaultdict(list)
for x in starter_rows:starts[x['gameId']].append(x)
team_history=collections.defaultdict(list)
bh=collections.defaultdict(list);ph=collections.defaultdict(list);relief={};bat_evidence={};counts=collections.Counter();bad=[]
for g in sorted(archive,key=lambda x:(x['date'],x['source_fixture_id'])):
    fid=g['source_fixture_id'];day=g['date'];v=v1.get(fid);w=v2.get(fid)
    if v:
        assert day>'2026-07-26' and v['game_date']==day.replace('-','') and v['status']=='ok' and v['completed'] is True and w['official_game_id']==v['official_game_id'] and w['game_date']==v['game_date']
        assert v['evidence']['box']['sha256'] in raw_hashes
    for side in ('home','away'):
        line=None;starter=None
        if day<='2026-07-26':
            box=oldbat.get(fid); slots=collections.defaultdict(list)
            for x in ((box or {}).get('data') or {}).get(side,[]):
                if type(x.get('order')) is int and 1<=x['order']<=9 and x.get('code') and x.get('pos')!='교':slots[x['order']].append(str(x['code']))
            if set(slots)==set(range(1,10)):
                candidate=[slots[i][0] for i in range(1,10)]
                if len(set(candidate))==9:line=candidate
            pit=oldpit.get(fid);ps=((pit or {}).get('data') or {}).get(side,[])
            claim=starts[fid][0].get(side+'_starter') if len(starts[fid])==1 else None
            if ps and ps[0].get('pcode') and (not claim or ps[0].get('name')==claim):starter=str(ps[0]['pcode'])
        if fid in official:
            o=official[fid]
            line=[x['player_id'] for x in sorted(o['lineups'][side],key=lambda x:x['order'])] if o['lineup_status']=='official_flag_true_profile_name_crossmatched' else None
            pitcher=(o.get('starting_pitchers') or {}).get(side) or {};starter=pitcher.get('player_id')
        team_history[g[side]].append({'fid':fid,'date':day,'lineup':line,'starter':starter})
        if v:
            a=v['statistics']['batters'][side];b=w['batters'][side];assert len(a)==len(b)
            for i,(x,y) in enumerate(zip(a,b)):
                assert y['v1_row_index']==i and (x['name'],x['player_id'])==(y['name'],y['player_id']) and y['official_v1']=={k:x[k] for k in ('AB','H','R','RBI')}
                if y['status']!='matched':counts['incomplete_'+y['status']]+=1;bad.append((fid,side,i,x['player_id'],y['status']));continue
                daily=y['daily_official_row'];pid=x['player_id']; assert pid and y['daily_evidence']['sha256'] in raw_hashes and str(y['daily_evidence']['query']['playerId'])==pid
                assert daily['date']==v['game_date'] and all(daily[k]==x[k] for k in ('AB','H','R','RBI')) and daily['BB']==y['BB'] and all(nat(z) for z in (x['AB'],x['H'],y['BB'])) and x['H']<=x['AB']
                bh[pid].append({'fixture_id':fid,'date':day,'ab':x['AB'],'hit':x['H'],'bb':y['BB'],'hr':y.get('HR')});bat_evidence[(fid,pid)]=y['daily_evidence']['sha256'];counts['complete']+=1
                if x['AB']==0 and y['BB']==0:counts['zero_ab_bb']+=1;counts['zero_ab_bb_pa_'+str(daily['PA'])]+=1
            ps=v['statistics']['pitchers'][side];supp=w['pitcher_id_supplement'][side];assert len(ps)==len(supp);linked=[]
            for i,(x,y) in enumerate(zip(ps,supp)):
                assert y['v1_row_index']==i and y['name']==x['name'] and y['original_player_id']==x['player_id']
                pid=x['player_id']
                if y['status']=='verified_official_profile':pid=y['supplement_player_id'];assert pid==x['source_numeric_id_claim'] and y['profile_evidence']['sha256'] in raw_hashes
                else:assert y['status'] in ('already_linked','no_official_numeric_claim')
                linked.append((x,pid))
            duplicate={p for p,n in collections.Counter(p for _,p in linked if p).items() if n>1}
            for x,pid in linked:
                if not pid or pid in duplicate or not nat(x['outs']) or not nat(x['ER']):counts['pitcher_duplicate' if pid in duplicate else 'pitcher_missing']+=1;continue
                ph[pid].append({'fixture_id':fid,'date':day,'outs':x['outs'],'er':x['ER'],'r':x['R']});counts['pitcher_complete']+=1
            quality=v['quality']['pitchers'][side]
            if len([x for x in ps if x['is_starter']])==1 and all(nat(x['outs']) for x in ps) and quality['team_equals_individual_outs'] is True and sum(x['outs'] for x in ps)==quality['team_outs']:
                relief[(fid,side)]=sum(x['outs'] for x in ps if not x['is_starter'])
        elif day<='2026-07-26':
            bat=oldbat.get(fid);pit=oldpit.get(fid)
            for box in (bat,pit):
                if box:assert (box['date'],box['home'],box['away'])==(day,g['home'],g['away'])
            for x in ((bat or {}).get('data') or {}).get(side,[]):
                if x.get('code') and all(nat(x.get(k)) for k in ('ab','hit','bb')) and x['hit']<=x['ab']:bh[str(x['code'])].append({'fixture_id':fid,'date':day,**{k:x.get(k) for k in ('ab','hit','bb','hr')}})
            ps=((pit or {}).get('data') or {}).get(side,[])
            for x in ps:
                n=outs(x.get('inn'))
                if x.get('pcode') and nat(n) and nat(x.get('er')):ph[str(x['pcode'])].append({'fixture_id':fid,'date':day,'outs':n,'er':x['er'],'r':x.get('r')})
            if ps and all(x.get('pcode') and nat(outs(x.get('inn'))) for x in ps[1:]):relief[(fid,side)]=sum(outs(x['inn']) for x in ps[1:])
assert counts=={'complete':3939,'zero_ab_bb':537,'zero_ab_bb_pa_0':518,'zero_ab_bb_pa_1':19,'incomplete_daily_not_captured':2799,'incomplete_missing_player_id':227,'pitcher_complete':1805,'pitcher_duplicate':8,'pitcher_missing':764}
assert len(incomplete)==3026 and all(x['bb'] is None for x in incomplete) and sorted(bad,key=str)==sorted([(x['fixture_id'],x['side'],x['row_index'],x['player_id'],x['reason']) for x in incomplete],key=str)

errors=[];lineage=0;nulls=collections.Counter()
def temporal(obj,day,target):
    if isinstance(obj,dict):
        for k,v in obj.items():
            if k in ('fixture_id','id') and v is not None:assert v!=target and v in games and games[v]['date']<day,(target,k,v)
            elif k in ('date','latest_source_date') and v is not None:assert v<day,(target,k,v)
            elif k=='source_fixture_ids':assert all(z!=target and z in games and games[z]['date']<day for z in v)
            if k!='provenance':temporal(v,day,target)
    elif isinstance(obj,list):
        for x in obj:temporal(x,day,target)
for fid,pr in frozen.items():
    day=base[fid]['event_date'];assert (day,pr['cohort'])==(older[fid]['event_date'],older[fid]['cohort'])
    common=fresh['projected'][fid]
    for side in ('home','away'):
        history=[x for x in team_history[games[fid][side]] if x['date']<day]
        latest=history[-1]['date'] if history else None;last=[x for x in history if x['date']==latest] if latest else []
        selected=last[0]['lineup'] if len(last)==1 else None
        projection=common['projection'];l=projection[side+'_lineup'];assert l['player_ids']==selected and l['source_fixture_ids']==([last[0]['fid']] if selected else [])
        recent={}
        for x in reversed(history):
            if x['starter'] and x['starter'] not in recent:recent[x['starter']]=x
            if len(recent)==5:break
        starter=None
        if len(recent)==5:
            oldest=min(x['date'] for x in recent.values());choices=[pid for pid,x in recent.items() if x['date']==oldest]
            if len(choices)==1:starter=choices[0]
        p=projection[side+'_starter'];assert p['player_id']==starter
        assert [(x['player_id'],x['fixture_id'],x['date']) for x in p['rotation_candidates']]==[(pid,x['fid'],x['date']) for pid,x in sorted(recent.items())]
    for k in N:
        name='projected' if k=='original_projected' else k;row=fresh[name][fid];assert row['scenario']=='fresh_history_'+name and row['received_at'] is None and row['completed_at'] is None
        assert row['projection']==common['projection'];temporal(row['projection'],day,fid)
        assert {x:v for x,v in row['features'].items() if x not in PLAYER}=={x:v for x,v in oldprior[fid]['features'].items() if x not in PLAYER}
        feature={x:v['value'] for x,v in base[fid]['features'].items()}
        for side in ('home','away'):
            allowed=set()
            if 'batting' in name:allowed.add(side+'_lineup_strength')
            if 'starter' in name:allowed.add(side+'_starter_strength')
            for component in ('lineup_strength','starter_strength','bullpen_load'):
                key=side+'_'+component;entry=row['features'][key];v=entry['value'];feature[key]=scalar(key,v);nulls[(k,key)]+=int(feature[key] is None)
                temporal(entry,day,fid)
                if key not in allowed:assert entry==common['features'][key]
                assert entry['completed_at'] is None and entry['received_at'] is None
                assert all(games[z]['date']<day for z in entry['source_fixture_ids'])
                if component=='lineup_strength' and v:
                    if 'batting' in name: assert [x['player_id'] for x in v]==[x['player_id'] for x in row['official_lineup_context']['lineups'][side]]
                    else: assert [x['player_id'] for x in v]==row['projection'][side+'_lineup']['player_ids']
                    for x in v:
                        actual=x['history'] or [];expected=[z for z in bh[x['player_id']] if z['date']<day][-10:]
                        assert [{k:z[k] for k in ('fixture_id','date','ab','hit','bb','hr')} for z in actual]==expected
                        for z in actual:
                            if z['fixture_id'] in v1:
                                assert z['provenance']['box']['sha256']==v1[z['fixture_id']]['evidence']['box']['sha256'] and z['provenance']['daily']['sha256']==bat_evidence[(z['fixture_id'],x['player_id'])]
                        lineage+=sum(z['fixture_id'] in v1 for z in actual)
                elif component=='starter_strength' and v:
                    assert v['projected_player_id']==(row['official_schedule_pitcher_context']['pitchers'][side]['player_id'] if 'starter' in name else row['projection'][side+'_starter']['player_id'])
                    actual=v['appearances'];expected=[z for z in ph[v['projected_player_id']] if z['date']<day][-10:]
                    assert [{k:z[k] for k in ('fixture_id','date','outs','er','r')} for z in actual]==expected
                    for z in actual:
                        if z['fixture_id'] in v1:assert z['provenance']['box']['sha256']==v1[z['fixture_id']]['evidence']['box']['sha256']
                    lineage+=sum(z['fixture_id'] in v1 for z in actual)
                elif component=='bullpen_load' and v:
                    for x in v['games']:
                        z=games[x['id']];assert 1<=(date.fromisoformat(day)-date.fromisoformat(z['date'])).days<=2
                        oldside='home' if z['home']==games[fid][side] else 'away';assert z[oldside]==games[fid][side] and x['relief']['outs']==relief[(x['id'],oldside)]
                        lineage+=int(x['id'] in v1)
        r=rates(base[fid],feature,parameters);p=prob(*r);saved=pr['settings'][k];errors.extend(abs(a-b) for a,b in zip(r,[saved['home_rate'],saved['away_rate']]));errors.extend(abs(a-b) for a,b in zip(p,[saved['probabilities_3way'][x] for x in ('home','draw','away')]))
        nd=p[0]+p[2];errors.extend([abs(p[0]/nd-saved['probabilities_2way']['home']),abs(p[2]/nd-saved['probabilities_2way']['away'])])
assert max(errors)<1e-9
for (name,key),n in nulls.items():assert n==pred['null_feature_counts'][name][key]

# Use committed labels and fixed phase3 price ledger only after prediction blobs are verified.
labels=idx(rows(pin(MODEL,RESULT,M/'phase2/test-labels.jsonl.gz','885b5dc0d182fa91d29ea4d74a078fb8835acf817095604106564864d0e19dc9')))
price=json.loads(pin(MODEL,RESULT,M/'phase3/overlay-results.json','24938f0f5489d22bbc76a830e2f48e2491da6687d3f4a5a2f9e8e8c376790d23'))
audits=idx(rows(pin(ODDS,'9c7986bc',O/'audit-original-2026-prices.jsonl.gz')))
groups={'full_709':set(frozen),'original_193':{f for f,x in frozen.items() if x['cohort']=='original_193'},'new_516':{f for f,x in frozen.items() if x['cohort']=='new_complement'},'through_2026_07_26':{f for f,x in frozen.items() if x['event_date']<='2026-07-26'},'after_2026_07_26':{f for f,x in frozen.items() if x['event_date']>'2026-07-26'}}
assert [len(groups[k]) for k in groups]==[709,193,516,470,239] and result['fresh_prediction_commit']==FREEZE
summary={}
for group,ids in groups.items():
    r=result['results'][group];assert r['threeway']['n']==len(ids);summary[group]={'n':len(ids),'hits':{},'prices':{}}
    for prefix,source in [('old_',older),('fresh_',frozen)]:
        for name in N:
            hits=0;ll=br=0.
            for fid in ids:
                p=source[fid]['settings'][name]['probabilities_3way'];y=outcome(labels[fid]);hits+=int(choose(p,('home','draw','away'))==y);ll-=math.log(p[y]);br+=sum((p[k]-int(k==y))**2 for k in ('home','draw','away'))
            ref=r['threeway']['models'][prefix+name];assert ref['hits']==hits;close(ref['accuracy_3way'],hits/len(ids),1e-12);close(ref['logloss_3way'],ll/len(ids),1e-12);close(ref['brier_3way'],br/len(ids),1e-12);summary[group]['hits'][prefix+name]=hits
    for name in N:
        buckets=collections.defaultdict(lambda:np.zeros(4));changed3=changed2=0;diff3=[];diff2=[]
        for fid in sorted(ids,key=lambda z:(older[z]['event_date'],z)):
            y=outcome(labels[fid]);a=frozen[fid]['settings'][name];b=older[fid]['settings'][name];p=a['probabilities_3way'];q=b['probabilities_3way'];u=a['probabilities_2way'];v=b['probabilities_2way']
            changed3+=choose(p,('home','draw','away'))!=choose(q,('home','draw','away'));changed2+=choose(u,('home','away'))!=choose(v,('home','away'))
            diff3.extend(abs(p[k]-q[k]) for k in ('home','draw','away'));diff2.extend(abs(u[k]-v[k]) for k in ('home','away'))
            def metric(x):return (int(choose(x,('home','draw','away'))==y),-math.log(x[y]),sum((x[k]-int(k==y))**2 for k in ('home','draw','away')))
            buckets[date.fromisoformat(older[fid]['event_date']).isocalendar()[:2]]+=np.array([1,*[x-z for x,z in zip(metric(p),metric(q))]])
        ref=r['threeway']['fresh_vs_corresponding_old'][name];assert (ref['weeks'],ref['changed_3way_picks'],ref['changed_2way_picks'])==(len(buckets),changed3,changed2)
        close(ref['mean_abs_probability_3way'],sum(diff3)/len(diff3),1e-12);close(ref['mean_abs_probability_2way'],sum(diff2)/len(diff2),1e-12)
        matrix=np.array([buckets[k] for k in sorted(buckets)]);rng=np.random.default_rng(20261103);draw=matrix[rng.integers(0,len(matrix),size=(5000,len(matrix)))].sum(axis=1)
        for i,key in enumerate(('accuracy_delta_ci95','logloss_delta_ci95','brier_delta_ci95'),1):
            for a,b in zip(np.quantile(draw[:,i]/draw[:,0],[.025,.975]),ref[key]):close(a,b,1e-12)
    for pc,ledger in result['price_ledgers'][group].items():
        prior={x['fixture_id']:x for x in price['ledgers'][pc] if x['fixture_id'] in ids};assert {x['fixture_id'] for x in ledger}==set(prior) and len(ledger)==len(prior)==r['prices'][pc]['n']
        assert r['prices'][pc]['denominator']==len(ids) and r['prices'][pc]['refunds']==sum(x['winner']=='draw' for x in ledger)
        sensitivity=[x for x in ledger if x['price_source']['origin']=='official_restored' or audits[x['fixture_id']]['official_result']['buyReject']=='0']
        out={'n':len(ledger),'refunds':r['prices'][pc]['refunds'],'buyReject0_n':len(sensitivity),'buyReject0_refunds':sum(x['winner']=='draw' for x in sensitivity),'net':{},'buyReject0_net':{}}
        for name in tuple(prefix+x for prefix in ('old_','fresh_') for x in N)+('team','market'):
            total=Decimal(0);monthly=collections.defaultdict(Decimal);daily=collections.defaultdict(Decimal);wins=void=0
            for x in ledger:
                fid=x['fixture_id'];assert x['price_source']==prior[fid]['price_source'] and x['winner']==outcome(labels[fid]);f=x['forecasts'][name];p=f['probabilities'];pick=('home','away')[int(p[1]>p[0])];y=x['winner']
                if name.startswith(('old_','fresh_')):
                    sr=older[fid] if name.startswith('old_') else frozen[fid];setting=name.split('_',1)[1];q=sr['settings'][setting]['probabilities_2way'];assert all(abs(a-q[k])<1e-12 for a,k in zip(p,('home','away')))
                else:
                    assert f==prior[fid]['forecasts'][name]
                    if name=='team':assert all(abs(a-older[fid]['frozen_team']['probabilities_2way'][k])<1e-12 for a,k in zip(p,('home','away')))
                net=Decimal(0) if y=='draw' else Decimal(str(x['price_source']['prices'][pick]))-1 if pick==y else Decimal(-1)
                assert f['pick']==pick and f['void']==(y=='draw') and f['hit']==(pick==y) and Decimal(f['net'])==net
                total+=net;daily[x['date']]+=net;monthly[x['date'][:7]]+=net;wins+=f['hit'];void+=f['void']
            equity=peak=dd=Decimal(0)
            for day in sorted(daily):equity+=daily[day];peak=max(peak,equity);dd=max(dd,peak-equity)
            ref=r['prices'][pc]['models'][name];assert (ref['bets'],ref['hits'],ref['voids'])==(len(ledger),wins,void) and Decimal(ref['net_units'])==total and {k:Decimal(v) for k,v in ref['monthly_units'].items()}==monthly
            if ledger:assert Decimal(ref['max_daily_close_drawdown_units'])==dd;close(ref['roi_including_refunds'],float(total/len(ledger)),1e-12)
            else:assert ref['max_daily_close_drawdown_units'] is None and ref['roi_including_refunds'] is None
            out['net'][name]=str(total);out['buyReject0_net'][name]=str(sum((Decimal(x['forecasts'][name]['net']) for x in sensitivity),Decimal(0)))
        for name in N:
            buckets=collections.defaultdict(lambda:np.zeros(4));changed=0
            for x in ledger:
                a=x['forecasts']['fresh_'+name];b=x['forecasts']['old_'+name];g=buckets[date.fromisoformat(x['date']).isocalendar()[:2]]
                if not a['void']:g[0]+=1;g[1]+=int(a['hit'])-int(b['hit'])
                g[2]+=1;g[3]+=float(Decimal(a['net'])-Decimal(b['net']));changed+=a['pick']!=b['pick']
            ref=r['prices'][pc]['fresh_vs_corresponding_old'][name];assert (ref['weeks'],ref['changed'])==(len(buckets),changed)
            if len(buckets)>=2:
                matrix=np.array([buckets[k] for k in sorted(buckets)]);rng=np.random.default_rng(20261103);draw=matrix[rng.integers(0,len(matrix),size=(5000,len(matrix)))].sum(axis=1)
                for a,b in zip(np.quantile(draw[:,1]/draw[:,0],[.025,.975]),ref['accuracy_delta_ci95']):close(a,b,1e-12)
                for a,b in zip(np.quantile(draw[:,3]/draw[:,2],[.025,.975]),ref['roi_delta_ci95']):close(a,b,1e-12)
            else:assert ref['accuracy_delta_ci95'] is None and ref['roi_delta_ci95'] is None
        summary[group]['prices'][pc]=out
print(json.dumps({'status':'PASS','source_counts':dict(counts),'lineage_references':lineage,'max_prediction_error':max(errors),'prediction_sha256':sha(git(MODEL,FREEZE,M/'fresh-history/predictions.json')),'results_sha256':sha(git(MODEL,RESULT,M/'fresh-history/results.json')),'summary':summary},ensure_ascii=False,sort_keys=True))
