"""Read-only independent K1/K2 lineup, fixed prediction and settlement audit."""
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import subprocess
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

REVIEW=Path(__file__).resolve().parents[2]
K1=Path('C:/Users/user/Documents/Codex/k1model'); K2=Path('C:/Users/user/Documents/Codex/k2model')
P1=Path('experiments/kleague1-lineup-challenger-20261103'); P2=Path('experiments/kleague2-lineup-challenger-20261103'); DC=Path('experiments/kleague2-dc-lineup-20261103')
SRC1='1f5ecca83860d61d49c4a91b0ec186e55b7d2778'; SRC2='99dc30bd60b76184d09e4678425d182ebd47d437'; PRICE='4f3ee6b2199b5fda5b87e110d8b1a69ffffb6386'
H1='b8f5117d2ec60f37562007dcd050c62d35e25d23'; H2='32c690e55df246db5e122a037fd4eda0cd2c6be2'
def sha(x): return hashlib.sha256(x).hexdigest()
def git(repo,commit,path): return subprocess.check_output(['git','show',f'{commit}:{path}'],cwd=repo)
def pin(repo,commit,path):
    b=(repo/path).read_bytes(); assert b==git(repo,commit,path.as_posix()),str(path); return b
def js(path): return json.loads(path.read_bytes())
def lines(path):
    b=path.read_bytes(); return [json.loads(x) for x in (gzip.decompress(b) if path.suffix=='.gz' else b).splitlines()]
def equal(a,b,tol=1e-10): assert abs(a-b)<tol,(a,b)
def pick(p): return max(range(3),key=lambda k:(p[k],-k))
def actual(score): return 0 if score[0]>score[1] else 1 if score[0]==score[1] else 2
def xi_history(games):
    # Independently select strict-earlier records and reject team-days with multiple games.
    counts=collections.Counter((g['day'],t) for g in games for t in g['teams']); out={}
    for g in games:
        features=[]
        for team,current in zip(g['teams'],g['xi']):
            past=[]
            for x in games:
                if x['day']>=g['day'] or team not in x['teams'] or counts[(x['day'],team)]!=1: continue
                past.append((x['day'],x['id'],set(x['xi'][x['teams'].index(team)])))
            past.sort(); recent=[x[2] for x in past[-3:]]; now=set(current)
            features.append([len(now & recent[-1])/11 if recent else 0.,sum(len(now & p) for p in recent)/11,float(not recent),float(len(recent)<3)])
        out[g['id']]=features
    return out
def rates(game,feats,state,coeff):
    index=state['teams']; n=len(index); team=game['teams']; candidate=len(coeff)==2+2*n+8
    assert len(coeff)==2+2*n+(8 if candidate else 0)
    result=[]
    for side in (0,1):
        own,opp=team[side],team[1-side]; v=[1.,float(side==0)]+[float(index.get(own)==i) for i in range(n)]+[float(index.get(opp)==i) for i in range(n)]
        if candidate:
            a,b=feats[side],feats[1-side]; v += [a[0],b[0],a[1],b[1],a[2],b[2],a[3],b[3]]
        result.append(math.exp(math.fsum(x*y for x,y in zip(v,coeff))))
    return result
def distribution(h,a,rho=0.):
    hs=[math.exp(-h)]; aw=[math.exp(-a)]
    for i in range(1,60): hs.append(hs[-1]*h/i); aw.append(aw[-1]*a/i)
    tau={(0,0):1-h*a*rho,(0,1):1+h*rho,(1,0):1+a*rho,(1,1):1-rho}; assert min(tau.values())>0
    p=[0.,0.,0.]
    for i in range(60):
        for j in range(60):p[0 if i>j else 1 if i==j else 2]+=hs[i]*aw[j]*tau.get((i,j),1.)
    equal(sum(p),1.,1e-12); return p,min(tau.values())
def normalized(odds):
    inverse=[1/x for x in odds]; return [x/sum(inverse) for x in inverse]
def scores(rows,method):
    hits=sum(pick(r['p'][method])==r['y'] for r in rows); n=len(rows)
    return {'n':n,'hits':hits,'accuracy':hits/n,'logloss':sum(-math.log(r['p'][method][r['y']]) for r in rows)/n,'brier':sum(sum((p-int(i==r['y']))**2 for i,p in enumerate(r['p'][method])) for r in rows)/n}
def settled(rows,method):
    net=Decimal(0);monthly=collections.defaultdict(Decimal);daily=collections.defaultdict(Decimal)
    for r in rows:
        choice=pick(r['p'][method]); amount=Decimal(str(r['odds'][choice]))-1 if choice==r['y'] else Decimal(-1)
        net+=amount;monthly[r['day'][:7]]+=amount;daily[r['day']]+=amount
    balance=peak=dd=Decimal(0)
    for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
    return net,monthly,dd
def compare_metrics(ref,rows,mode,league):
    calc=scores(rows,mode)
    assert (ref['n'],ref['hits'])==(calc['n'],calc['hits'])
    for k in ('accuracy','logloss','brier'):equal(ref[k],calc[k],1e-12)
def compare_returns(ref,rows,mode,league):
    net,month,dd=settled(rows,mode)
    nk,mk,dk=('net','monthly_net','daily_close_max_drawdown') if league==1 else ('profit_units','monthly_profit_units','daily_closing_max_drawdown_units')
    equal(float(net),ref[nk],1e-10);equal(float(net/len(rows)),ref['roi'],1e-12);equal(float(dd),ref[dk],1e-10)
    assert set(month)==set(ref[mk])
    for k,v in month.items():equal(float(v),ref[mk][k],1e-10)
    return str(net)

# Exact model-checkout bytes against the source PR blobs and both model heads.
S1='experiments/kleague1-official-lineup-20261103/';S2='experiments/kleague2-official-lineup-20261103/cohort/';SP='experiments/kleague-official-prices-20261103/'
source1={'lineups2025.jsonl':S1+'season-2025/snapshots/6353654f50760e59b4d1/fixtures.jsonl','lineups2025-manifest.json':S1+'season-2025/snapshots/6353654f50760e59b4d1/manifest.json','lineups2026.jsonl':S1+'fixtures.jsonl','lineups2026-manifest.json':S1+'manifest.json','schedule2026.json':S1+'cohort-plan.json'}
source2={'lineups.jsonl':S2+'lineups.jsonl','census-2025.json':S2+'census-2025.json','census-2026.json':S2+'census-2026.json'}
for name,path in source1.items(): assert pin(K1,H1,P1/'inputs'/name)==git(REVIEW,SRC1,path)
for name,path in source2.items(): assert pin(K2,H2,P2/'inputs'/name)==git(REVIEW,SRC2,path)
for year in (2025,2026):
    manifest=js(K1/P1/f'inputs/lineups{year}-manifest.json'); assert manifest['fixtures_sha256']==sha((K1/P1/f'inputs/lineups{year}.jsonl').read_bytes())
official2=json.loads(git(REVIEW,SRC2,S2+'manifest.json'))['sha256_exact_git_bytes']
for name in source2: assert official2['cohort/'+name]==sha((K2/P2/'inputs'/name).read_bytes())
for repo,head,folder,name in [(K1,H1,P1,'prices.jsonl.gz'),(K2,H2,P2,'joined-prices.jsonl.gz')]: assert pin(repo,head,folder/'inputs'/name)==git(REVIEW,PRICE,SP+'joined-prices.jsonl.gz')
for repo,head,folder in [(K1,H1,P1),(K2,H2,P2)]: assert pin(repo,head,folder/'inputs/price-manifest.json')==git(REVIEW,PRICE,SP+'manifest.json')
assert js(K1/P1/'inputs/price-manifest.json')['joined_gzip_sha256']==sha((K1/P1/'inputs/prices.jsonl.gz').read_bytes())
for repo,head,folder,paths in [(K1,H1,P1,['protocol.json','frozen/manifest.json','frozen/predictions.jsonl','evaluation/results.json','evaluation/ledger.jsonl']),(K2,H2,P2,['protocol.json','frozen/model-parameters.json','frozen/validation-selection.json','frozen/test-predictions.jsonl','results/metrics.json','results/scored.jsonl']),(K2,H2,DC,['protocol.json','frozen/parameters.json','frozen/selection.json','frozen/test-predictions.jsonl','results/metrics.json','results/scored.jsonl'])]:
    for path in paths: pin(repo,head,folder/path)
for repo,freeze,head,folder,files in [(K1,'77bb7c3e',H1,P1,['frozen/manifest.json','frozen/predictions.jsonl']),(K2,'e8288f5d',H2,P2,['frozen/model-parameters.json','frozen/test-predictions.jsonl']),(K2,'bfb3c162',H2,DC,['frozen/parameters.json','frozen/test-predictions.jsonl'])]:
    assert subprocess.run(['git','merge-base','--is-ancestor',freeze,head],cwd=repo).returncode==0
    for path in files: assert (repo/folder/path).read_bytes()==git(repo,freeze,(folder/path).as_posix())

g1=[]
for year in (2025,2026):
    for x in lines(K1/P1/f'inputs/lineups{year}.jsonl'):
        if not x['fixture_id'].startswith(f'kleague:{year}:1:'): continue
        assert x['match_status']=='FE' and x['league']=='KLEAGUE1'
        g1.append({'id':x['fixture_id'],'day':x['day'],'kickoff':x['day']+'T'+x['kickoff_local_display']+':00+09:00','teams':[s['team_id'] for s in x['lineups']],'xi':[[p['player_id'] for p in s['starters']] for s in x['lineups']],'score':[x['scores']['home'],x['scores']['away']]})
assert len(g1)==432 and sum(g['day'][:4]=='2025' for g in g1)==228
sched={x['fixture_id']:x for x in js(K1/P1/'inputs/schedule2026.json')['targets']}; assert len(sched)==228
assert sum(s['game_status']=='FE' and s['end_yn']=='Y' for s in sched.values())==204
for g in g1:
    if g['day'][:4]=='2026':
        s=sched[g['id']]; assert (s['day'],s['home_team_id'],s['away_team_id'],s['game_status'],s['end_yn'],s['home_goal'],s['away_goal'])==(g['day'],*g['teams'],'FE','Y',*g['score'])

census={f"kleague:{r['year']}:{r['meetSeq']}:{r['gameId']}":r for year in (2025,2026) for r in js(K2/P2/f'inputs/census-{year}.json')['fixtures']}
assert len(js(K2/P2/'inputs/census-2026.json')['fixtures'])==272 and sum(r['year']==2026 and r['gameStatus']=='FE' and r['endYn']=='Y' for r in census.values())==248
g2=[]; excluded=[]
for e in lines(K2/P2/'inputs/lineups.jsonl'):
    r=census[e['fixture_id']]; assert r['leagueId']==2 and r['meetSeq']==2
    if r['codeName']!='일반': excluded.append((e['fixture_id'],r['codeName'])); continue
    if r['gameStatus']!='FE' or r['endYn']!='Y': continue
    assert e['official_result']==[r['homeGoal'],r['awayGoal']] and e['kickoff_kst']==r['gameDate'].replace('.','-')+'T'+r['gameTime']+':00+09:00'
    assert [x['team_id'] for x in e['teams']]==[r['homeTeam'],r['awayTeam']]
    assert e['pregame_announcement_confirmed'] is False and e['publication_at'] is None
    g2.append({'id':e['fixture_id'],'day':e['kickoff_kst'][:10],'kickoff':e['kickoff_kst'],'teams':[x['team_id'] for x in e['teams']],'xi':[[p['player_id'] for p in x['starting_eleven']] for x in e['teams']],'score':e['official_result']})
assert len(g2)==521 and len(excluded)==2 and {int(x[0].split(':')[-1]) for x in excluded}=={274,275}
for games in (g1,g2):
    assert len({g['id'] for g in games})==len(games)
    for g in games:
        assert len(g['teams'])==2 and g['teams'][0]!=g['teams'][1] and all(len(x)==len(set(x))==11 and all(isinstance(p,str) and len(p)==8 and p.isdigit() for p in x) for x in g['xi'])
split1=[sum(g['day']<='2025-12-25' for g in g1),sum('2026-01-01'<=g['day']<='2026-06-24' for g in g1),sum('2026-06-24'<g['day']<'2026-07-01' for g in g1),sum(g['day']>='2026-07-01' for g in g1)]
split2=[sum(g['day'][:4]=='2025' for g in g2),sum('2026-01-01'<=g['day']<'2026-07-01' for g in g2),sum(g['day']>='2026-07-01' for g in g2)]
assert split1==[228,90,0,114] and split2==[273,120,128]
assert max(g['day'] for g in g2 if '2026-01-01'<=g['day']<'2026-07-01')=='2026-06-07'
features1=xi_history(g1);features2=xi_history(g2)
# Recompute the actual first test target after poisoning all target/future scores and other future XIs.
for games,baseline in ((g1,features1),(g2,features2)):
    target=min((g for g in games if g['day']>='2026-07-01'),key=lambda g:(g['day'],g['id']))
    poisoned=json.loads(json.dumps(games))
    for g in poisoned:
        if g['day']>=target['day']:
            g['score']=[999,999]
            if g['id']!=target['id']:g['xi']=[['99999999']*11,['88888888']*11]
    assert xi_history(poisoned)[target['id']]==baseline[target['id']]
assert all(len(f)==2 and all(0<=s[0]<=1 and 0<=s[1]<=3 and s[2] in (0,1) and s[3] in (0,1) for s in f) for f in list(features1.values())+list(features2.values()))
prices=lines(K1/P1/'inputs/prices.jsonl.gz'); qby=collections.defaultdict(list)
for q in prices:qby[q['fixture_id']].append(q)
excluded_prices={x['fixture_id']:x for x in (json.loads(line) for line in git(REVIEW,PRICE,SP+'exclusions.jsonl').splitlines())}
for fid,code in [('kleague:2026:1:144','1'),('kleague:2026:2:163','4')]:
    x=excluded_prices[fid]; assert x['reason']=='selection_buy_reject_status' and any(str(z['buyReject'])==code for z in x['details'])
@lru_cache(maxsize=None)
def official_prices(file,expected):
    compressed=git(REVIEW,PRICE,SP+file); body=gzip.decompress(compressed); assert sha(body)==expected
    j=json.loads(body);t=j['compSchedules'];return str(j['gmTs']),[dict(zip(t['keys'],v)) for v in t['datas']]
def price_for(g,league):
    candidates=qby.get(g['id'],[]); assert len(candidates)<=1
    if not candidates:return None
    q=candidates[0]; mt=q['market_type']; values=q['prices']; score=g['score']; winner=actual(score)
    valid=(q['league_id']==league and q['season']==int(g['day'][:4]) and q['gmId']=='G101' and q['betman_league_code']==('SC001' if league==1 else 'SC003') and [q['home_team_id'],q['away_team_id']]==g['teams'] and q['kickoff_kst']==g['kickoff'] and str(q['gameReject'])=='0' and str(q['buyReject'])=='0' and q['official_score']==score and str(q['settlement_code'])==str(winner) and mt['handi']==0 and mt.get('gameSubject') in (None,'') and ((mt.get('betId') is None and mt.get('betTypId') is None) or (str(mt.get('betId'))=='1' and str(mt.get('betTypId'))=='1' and mt.get('betNm')=='축구 승무패')))
    if not valid:return None
    odds=[values[k] for k in ('home','draw','away')]; assert all(isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x) and x>1 for x in odds)
    ts,rs=official_prices(q['response_file'],q['response_sha256']); assert ts==str(q['gmTs']); candidates=[r for r in rs if int(r['matchSeq'])==int(q['matchSeq'])]; assert len(candidates)==1; r=candidates[0]
    assert r['leagueCode']==q['betman_league_code'] and [r['winAllot'],r['drawAllot'],r['loseAllot']]==odds and str(r['gameReject'])=='0' and str(r['buyReject'])=='0' and str(r['gameResult'])==str(winner)
    assert [r['homeId'],r['awayId']]==g['teams'] and r['handi']==0 and r.get('gameSubject') in ('',None)
    assert (r.get('betId') is None and r.get('betTypId') is None) or (str(r.get('betId'))=='1' and str(r.get('betTypId'))=='1' and r.get('betNm')=='축구 승무패')
    assert r['mchScore']==f'{score[0]}:{score[1]}' and dt.datetime.fromtimestamp(r['gameDate']/1000,dt.timezone(dt.timedelta(hours=9))).isoformat()==g['kickoff']
    return q,odds

summary={};errors=[]
for league,games,features,repo,folder in [(1,g1,features1,K1,P1),(2,g2,features2,K2,P2)]:
    allgames={g['id']:g for g in games}; test={g['id']:g for g in games if g['day']>='2026-07-01'}
    if league==1:
        ps=lines(repo/folder/'frozen/predictions.jsonl'); states=js(repo/folder/'frozen/manifest.json')['states']; dcstates=None
        result=js(repo/folder/'evaluation/results.json'); led={x['id']:x for x in lines(repo/folder/'evaluation/ledger.jsonl')}
    else:
        ps=lines(repo/folder/'frozen/test-predictions.jsonl'); states=js(repo/folder/'frozen/model-parameters.json'); dcstates=js(K2/DC/'frozen/parameters.json'); dc={x['fixture_id']:x for x in lines(K2/DC/'frozen/test-predictions.jsonl')}
        result=js(repo/folder/'results/metrics.json'); dcresult=js(K2/DC/'results/metrics.json'); led={x['id']:x for x in lines(K2/DC/'results/scored.jsonl')}
    assert len(ps)==len(test) and {x['fixture_id'] for x in ps}==set(test)
    assert sum(side[2] for fid in test for side in features[fid])==0 and sum(side[3] for fid in test for side in features[fid])==0
    if league==2:assert set(dc)==set(test)
    rows=[]
    for p in ps:
        fid=p['fixture_id']; g=test[fid]; f=features[fid]; got=p['lineup_features'] if league==1 else p['features']
        assert got==f
        if league==1: assert p['day']==g['day'] and p['teams']==g['teams'] and not ({'score','y','outcome'} & set(p))
        else: assert p['team_ids']==g['teams'] and p['kickoff_kst']==g['kickoff'] and sha(json.dumps(f,separators=(',',':')).encode())==p['features_sha256'] and not ({'score','y','outcome'} & set(p))
        pr={}
        for mode in ('team','lineup'):
            name=mode if league==1 or mode=='lineup' else 'team_only'; state=states[name]; coef=state.get('coeff',state.get('coefficients')); assert state['alpha']==100
            r=rates(g,f,state,coef); calculated,_=distribution(*r); saved=p[mode+'_p'] if league==1 else p['models'][name]['p']; savedrate=p[mode+'_rates'] if league==1 else p['models'][name]['rate']
            errors.extend(abs(a-b) for a,b in zip(r,savedrate));errors.extend(abs(a-b) for a,b in zip(calculated,saved)); assert all(abs(a-b)<1e-11 for a,b in zip(calculated,saved)); pr[mode]=saved
            if league==2:
                state=dcstates[name]; coef=state['theta'][:-1]; r=rates(g,f,state,coef); calc,mintau=distribution(*r,state['rho']); saved=dc[fid]['models'][name]
                assert dc[fid]['features_sha256']==p['features_sha256'] and saved['min_tau']>0
                errors.extend(abs(a-b) for a,b in zip(r,saved['rates']));errors.extend(abs(a-b) for a,b in zip(calc,saved['p']));assert all(abs(a-b)<1e-11 for a,b in zip(calc,saved['p']));equal(mintau,saved['min_tau'],1e-12);pr['dc_'+mode]=saved['p']
        quote=price_for(g,league); row={'id':fid,'day':g['day'],'y':actual(g['score']),'p':pr,'odds':None}
        if quote:
            q,odds=quote;row['odds']=odds;row['p']['market']=normalized(odds)
            assert fid in led
            if league==1: assert p['price']['odds']==odds and p['price']['product_id']==[q['gmId'],q['gmTs'],q['matchSeq']]
            else: assert led[fid]['odds']==odds
            savedmarket=p['market_p'] if league==1 else led[fid]['p']['market']
            assert all(abs(a-b)<1e-12 for a,b in zip(row['p']['market'],savedmarket))
        else:
            if league==1: assert p['price'] is None and fid not in led
            else: assert led[fid]['odds'] is None
        if league==2:
            assert led[fid]['outcome']==row['y'] and led[fid]['date']==row['day']
            for n in ('team','lineup','dc_team','dc_lineup'):
                mapped={'team':'poisson_team','lineup':'poisson_lineup'}.get(n,n);assert led[fid]['p'][mapped]==pr[n]
        else:
            if quote: assert led[fid]['score']==g['score'] and led[fid]['y']==row['y'] and led[fid]['odds']==row['odds']
        rows.append(row)
    priced=[r for r in rows if r['odds'] is not None]; assert len(priced)==(113 if league==1 else 127)
    assert [r['id'] for r in rows if r['odds'] is None]==['kleague:2026:1:144' if league==1 else 'kleague:2026:2:163']
    out={'test':len(rows),'priced':len(priced),'missing_price':len(rows)-len(priced),'test_hits':{},'priced_net':{}}
    for n in ('team','lineup'):
        ref=(result['all_test_1x2'][n] if league==1 else result['test_all']['team_only' if n=='team' else n]);compare_metrics(ref,rows,n,league);out['test_hits'][n]=scores(rows,n)['hits']
    for n in (('team','lineup','market') if league==1 else ('team','lineup','dc_team','dc_lineup','market')):
        if league==1: compare_metrics(result['priced_common'][n],priced,n,1); out['priced_net'][n]=compare_returns(result['priced_common'][n],priced,n,1)
        else:
            key={'team':'poisson_team','lineup':'poisson_lineup'}.get(n,n);compare_metrics(dcresult['price_metrics'][key],priced,n,2);out['priced_net'][n]=compare_returns(dcresult['price_returns'][key],priced,n,2)
            if n!='market':compare_metrics(dcresult['test_metrics'][key],rows,n,2);out['test_hits'][key]=scores(rows,n)['hits']
    summary['K'+str(league)]=out
assert max(errors)<1e-11
print(json.dumps({'status':'PASS','max_prediction_error':max(errors),'K1_split':split1,'K2_split':split2,'K2_playoff_excluded':excluded,'summary':summary},ensure_ascii=False,sort_keys=True))
