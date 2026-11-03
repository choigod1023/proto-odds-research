"""Independent read-only audit of pinned KBO full-cohort inputs; no model scoring."""
import copy
import gzip
import hashlib
import html
import json
import re
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path

DATA = Path('C:/Users/user/Documents/Codex/kbodata')
OFF = Path('C:/Users/user/Documents/Codex/klu')
HERE = Path(__file__).resolve().parents[2]
P = Path('experiments/kbo-player-data-20261103')
SOURCE = Path('experiments/kbo-official-lineups-20261103/official-lineups-2026.jsonl')
RAW = OFF / '.research-state/kbo-lineups-v5/raw'
EXPECTED = {'official_batting_only':'789a413ab35575b2461dd2d6943485e88be55f96e1eab9364b6e706691ee3e58', 'official_starter_only':'c5481b7fd7a36bc345e11ec953225ab659431408059cbb03fedab40050df6a46', 'official_batting_and_starter':'f16c08073737188b9bd2dec9de59791cdeb72033e6bba8c3061061809a2a8131'}
def sha(b): return hashlib.sha256(b).hexdigest()
def pin(repo, commit, path, expected=None, normalize=False):
    b = (repo/path).read_bytes()
    git = subprocess.check_output(['git','show',f'{commit}:{path.as_posix()}'],cwd=repo)
    assert (b.replace(b'\r\n',b'\n') if normalize else b) == git, str(path)
    if expected: assert sha(b)==expected, (str(path),sha(b))
    return b
def rows(path):
    op = gzip.open if str(path).endswith('.gz') else open
    with op(path,'rt',encoding='utf8') as f: return [json.loads(x) for x in f]
def index(records):
    d = {r['fixture_id']:r for r in records}
    assert len(d)==len(records)
    return d
@lru_cache(maxsize=None)
def raw(digest):
    b=(RAW/digest).read_bytes()
    assert sha(b)==digest
    return b
def ev(e):
    b=raw(e['sha256']); assert len(b)==e['bytes'] and e['http_status']==200
    return b
@lru_cache(maxsize=None)
def profile(digest):
    text=raw(digest).decode('utf8')
    names=re.findall(r'<span\b[^>]*\bid="[^"]*playerProfile_lblName"[^>]*>(.*?)</span>',text,re.S|re.I)
    assert len(names)==1
    return html.unescape(re.sub('<[^>]*>','',names[0])).strip()
def past_only(history, target):
    assert all(h['date'] < target for h in history)
def history_before(history, target):
    return [x for x in history if x['date'] < target][-10:]
def outs(v):
    m=re.fullmatch(r'(\d+)(?:\.(\d)|\s*([⅓⅔]))?',str(v).strip())
    if not m: return None
    n=m[2] or {'⅓':'1','⅔':'2'}.get(m[3],'0')
    return int(m[1])*3+int(n) if n in ('0','1','2') else None

pin(OFF,'b24739cf',SOURCE,'f82acede0093fa2a6a2176018a812ac9c279ad893d3ffb3bed9a16cb37b6c5d3')
for key,h in EXPECTED.items(): pin(DATA,'b2dbad4',P/f'official-full-2026-{key}.jsonl.gz',h)
for file in ['official-full-2026-cohorts.jsonl','official-full-2026-manifest.json','official-full-2026-input-audit.md']:
    pin(DATA,'b2dbad4',P/file,normalize=True)
manifest=json.loads((DATA/P/'official-full-2026-manifest.json').read_bytes())
for rel,meta in manifest['raw_git_blob_checks'].items():
    if rel=='full_official_source': continue
    path=Path(rel)
    commit='b2dbad4'
    pin(DATA,commit,path,meta['raw_sha256'],normalize=meta.get('byte_policy') is not None)
    blob=subprocess.check_output(['git','show',f'{commit}:{path.as_posix()}'],cwd=DATA)
    assert sha(blob)==meta['git_blob_sha256']
for commit,path in [('69694632','experiments/kbo-player-challenger-20261103/official-player-full-cohort-extension.json'),('e03addcb','experiments/kbo-player-challenger-20261103/official-player-factorial-contract.json')]:
    contract=json.loads(subprocess.check_output(['git','show',f'{commit}:{path}'],cwd=HERE))
    assert isinstance(contract,dict)

coh=json.loads((DATA/P/'official-full-2026-cohorts.jsonl').read_bytes())
official=index(rows(OFF/SOURCE)); source=index(rows(DATA/P/'rows.jsonl')); base=index(rows(DATA/P/'prior-projected-features.jsonl.gz'))
arms={k:index(rows(DATA/P/f'official-full-2026-{k}.jsonl.gz')) for k in EXPECTED}
old={k:index(rows(DATA/P/name)) for k,name in [('official_batting_only','official-lineup-overlay-2026.jsonl.gz'),('official_starter_only','official-starter-only-2026.jsonl.gz'),('official_batting_and_starter','official-batting-and-starter-2026.jsonl.gz')]}
games=rows(DATA/'data/model_candidates/kbo/archived-games.jsonl.gz'); bygame={g['source_fixture_id']:g for g in games}; assert len(bygame)==len(games)
bat=json.loads((DATA/'data/raw/detail/kbo_batters_indiv_2023_2026.json').read_bytes()); pit=json.loads((DATA/'data/raw/detail/kbo_baseball_2023_2026.json').read_bytes())
bh=defaultdict(list); ph=defaultdict(list); identities=defaultdict(set)
for g in sorted(games,key=lambda x:(x['date'],x['source_fixture_id'])):
    fid=g['source_fixture_id']
    for data, kind in [(bat,'bat'),(pit,'pit')]:
        box=data.get(fid)
        if not box: continue
        assert (box['date'],box['home'],box['away'])==(g['date'],g['home'],g['away'])
        for side in ('home','away'):
            for x in box['data'][side]:
                pid=x.get('code' if kind=='bat' else 'pcode')
                if not pid: continue
                if kind=='bat':
                    bh[str(pid)].append(dict(fixture_id=fid,date=g['date'],**{k:x.get(k) for k in ('ab','hit','bb','hr')}))
                else:
                    ph[str(pid)].append(dict(fixture_id=fid,date=g['date'],outs=outs(x.get('inn')),source_bf_raw=x.get('bf'),source_bf_units='unverified',**{k:x.get(k) for k in ('r','er','hit','bb','hr','kk')}))
for fid,box in bat.items():
    for side in ('home','away'):
        for x in box['data'][side]:
            if x.get('code'):
                identities[(box['date'][:4],box[side],x.get('name'),str(x['code']))].add((fid,side,x.get('order'),x.get('pos')))
identity_ids=defaultdict(set)
for season,team,name,pid in identities: identity_ids[(season,team,name)].add(pid)
assert len(official)==726 and len(coh['all'])==709 and len(coh['excluded'])==17
assert len({x['official_game_id'] for x in official.values()})==726
assert len(set(coh['all']))==709 and set(coh['all'])==set(arms[next(iter(arms))])
assert all(set(arm)==set(coh['all']) for arm in arms.values())
assert set(coh['original_193'])==set(old['official_batting_only']) and len(coh['original_193'])==193
assert all(set(arm)==set(coh['original_193']) for arm in old.values())
assert set(coh['new_complement'])==set(coh['all'])-set(coh['original_193']) and len(coh['new_complement'])==516
excluded={x['fixture_id']:x['reason'] for x in coh['excluded']}; assert len(excluded)==17 and set(excluded)==set(official)-set(coh['all']) and set(excluded.values())=={'lineup_player_ids_unavailable'}
counts=Counter(); gaps=defaultdict(list); missing=Counter()
for fid,o in official.items():
    s=source[fid]; gid=o['official_game_id']; day=o['game_date']; dt=bygame[fid]['date']
    assert day==dt.replace('-','') and o['season_id']==2026 and o['league_id']==1 and gid.startswith(day)
    assert o['historical_publication_time_proven'] is False and o['historical_receipt_proven'] is False and o['eligible_as_pregame_feature'] is False
    schedule=json.loads(ev(o['schedule_evidence'])); matches=[g for g in schedule['game'] if g['G_ID']==gid]; assert len(matches)==1; g=matches[0]
    for k,v in [('G_DT',day),('SEASON_ID',2026),('LE_ID',1),('SR_ID',o['series_id']),('HEADER_NO',o['header_no']),('HOME_ID',o['home_team_id']),('AWAY_ID',o['away_team_id'])]: assert str(g[k])==str(v),(fid,k)
    assert (g['HOME_NM'].strip(),g['AWAY_NM'].strip())==(s['home_id'],s['away_id']) and g['HOME_ID']!=g['AWAY_ID']
    if s['kickoff_at']: assert s['kickoff_at'][:10]==dt and s['kickoff_at'][11:16]==g['G_TM']
    if o['fixture_match_basis']=='unique_date_home_away_schedule_match':
        assert fid==f"score:{dt}:{s['home_id']}:{s['away_id']}"
        assert sum((x['G_DT'],x['HOME_ID'],x['AWAY_ID'])==(day,g['HOME_ID'],g['AWAY_ID']) for x in schedule['game'])==1
        counts['unique_date_team']+=1
    else: assert o['fixture_match_basis']=='exact_official_game_id' and fid==gid+'2026'
    assert o['lineup_evidence']['params']['gameId']==gid
    lineup=json.loads(ev(o['lineup_evidence'])); assert lineup[0][0]['LINEUP_CK'] is True and o['lineup_flag'] is True
    if fid in excluded:
        assert o['lineup_status']=='official_flag_true_player_ids_unavailable'
        unresolved=0
        for side,ti in [('home',3),('away',4)]:
            source_line=sorted((int(x['row'][0]['Text']),x['row'][2]['Text'].strip()) for x in json.loads(lineup[ti][0])['rows'])
            selected=sorted(o['lineups'][side],key=lambda x:x['order'])
            assert [(x['order'],x['name']) for x in selected]==source_line and len(selected)==9
            for player in selected:
                candidates=identity_ids[('2026',s[side+'_id'],player['name'])]
                if len(candidates)!=1: unresolved+=1
        assert unresolved>0
        counts['excluded_unresolved_slots']+=unresolved
        counts['excluded_unknown']+=1
        continue
    assert o['lineup_status']=='official_flag_true_profile_name_crossmatched'
    seen=set(); seen_pitchers=set()
    for side,mi,ti,prefix in [('home',1,3,'B'),('away',2,4,'T')]:
        meta=lineup[mi][0]; assert (str(meta['G_ID']),str(meta['T_ID']))==(gid,str(g[side.upper()+'_ID']))
        rawplayers=sorted((int(x['row'][0]['Text']),x['row'][2]['Text'].strip()) for x in json.loads(lineup[ti][0])['rows'])
        selected=sorted(o['lineups'][side],key=lambda x:x['order']); assert len(selected)==9 and [x[0] for x in rawplayers]==list(range(1,10))
        output=arms['official_batting_only'][fid]['features'][side+'_lineup_strength']['value']; assert len(output)==9
        for key in ('official_batting_only','official_batting_and_starter'):
            ctx=arms[key][fid]['official_lineup_context']; assert ctx['official_game_id']==gid and ctx['point_in_time_verified'] is False
            assert [(x['order'],x['name'],x['player_id']) for x in ctx['lineups'][side]]==[(x['order'],x['name'],x['player_id']) for x in selected]
        for x,(order,name),feature in zip(selected,rawplayers,output):
            pid=x['player_id']; assert (x['order'],x['name'])==(order,name) and pid.isdigit() and pid not in seen and feature['player_id']==pid; seen.add(pid)
            e=x['official_profile_evidence']; assert e['params']['playerId']==pid and profile(e['sha256'])==name; ev(e)
            matches=identities[('2026',s[side+'_id'],name,pid)]; assert matches,(fid,side,name,pid)
            if x['player_id_basis']=='postgame_same_game_side_order_and_official_profile_name': assert any(i==fid and si==side and n==order and pos!='교' for i,si,n,pos in matches),(fid,pid)
            else:
                assert x['player_id_basis']=='postgame_other_game_same_team_season_name_and_official_profile_name'
                assert identity_ids[('2026',s[side+'_id'],name)]=={pid}
            hist=feature['history'] or []; assert hist==history_before(bh[pid],dt); past_only(hist,dt)
            if hist: gaps['bat'].append((date.fromisoformat(dt)-date.fromisoformat(hist[-1]['date'])).days)
            else: missing['bat']+=1
            counts['batters']+=1
        po=o['starting_pitchers'][side]; pid=po['player_id']; assert str(g[prefix+'_PIT_P_ID'])==pid and g[prefix+'_PIT_P_NM'].strip()==po['name'].strip()
        assert pid.isdigit() and int(pid)>0 and pid not in seen_pitchers; seen_pitchers.add(pid)
        for key in ('official_starter_only','official_batting_and_starter'):
            ctx=arms[key][fid]['official_schedule_pitcher_context']; p=ctx['pitchers'][side]
            assert ctx['official_game_id']==gid and ctx['point_in_time_verified'] is False and (p['player_id'],p['name'],p['team_id'])==(pid,po['name'].strip(),o[side+'_team_id'])
        output=arms['official_starter_only'][fid]['features'][side+'_starter_strength']; value=output['value']; hist=(value or {}).get('appearances',[])
        assert hist==history_before(ph[pid],dt); past_only(hist,dt)
        if value: assert value['projected_player_id']==pid
        if hist: gaps['pit'].append((date.fromisoformat(dt)-date.fromisoformat(hist[-1]['date'])).days)
        else: missing['pit']+=1
        counts['pitchers']+=1
    original=base[fid]
    for side in ('home','away'):
        team=s[side+'_id']; projected=original['projection'][side+'_lineup']
        for ref in projected['source_fixture_ids']:
            assert bygame[ref]['date']<dt and team in (bygame[ref]['home'],bygame[ref]['away'])
        for candidate in original['projection'][side+'_starter']['rotation_candidates']:
            ref=candidate['fixture_id']; assert bygame[ref]['date']==candidate['date']<dt and team in (bygame[ref]['home'],bygame[ref]['away'])
        f=original['features'][side+'_lineup_strength']['value']
        if f:
            assert [x['player_id'] for x in f]==projected['player_ids']
            for x in f: assert (x['history'] or [])==history_before(bh[x['player_id']],dt)
        f=original['features'][side+'_starter_strength']['value']
        if f:
            pid=f['projected_player_id']; assert pid==original['projection'][side+'_starter']['player_id'] and f['appearances']==history_before(ph[pid],dt)
        f=original['features'][side+'_team_strength']['value'] or []
        for x in f:
            prior=bygame[x['fixture_id']]; pside='home' if prior['home']==team else 'away'
            assert prior[pside]==team and x['date']==prior['date']<dt
            assert (x['runs_for'],x['runs_against'])==((prior['hg'],prior['ag']) if pside=='home' else (prior['ag'],prior['hg']))
        f=original['features'][side+'_bullpen_load']['value']
        for entry in (f or {}).get('games',[]):
            ref=entry['id']; prior=bygame[ref]; assert entry['date']==prior['date']<dt
            pside='home' if prior['home']==team else 'away'; assert prior[pside]==team
            relief=pit[ref]['data'][pside][1:]
            expected=[{'player_id':str(x['pcode']),'outs':outs(x['inn']),'source_bf_raw':x.get('bf')} for x in relief]
            assert entry['relief']['pitchers']==expected and entry['relief']['appearances']==len(expected) and entry['relief']['outs']==sum(x['outs'] for x in expected)
    for key, arm in arms.items():
        a=arm[fid]; orig=base[fid]
        allowed={'scenario','warning','official_lineup_context','official_schedule_pitcher_context'}
        assert all(a.get(k)==v for k,v in orig.items() if k not in allowed and k!='features')
        assert set(a)-set(orig) <= allowed
        assert a['projection']==orig['projection'] and a['availability_tier']==orig['availability_tier'] and a['received_at']==orig['received_at'] and a['completed_at']==orig['completed_at']
        assert set(a['features'])==set(orig['features'])
        for feature,v in orig['features'].items():
            is_bat=feature in ('home_lineup_strength','away_lineup_strength'); is_pit=feature in ('home_starter_strength','away_starter_strength')
            if not ((is_bat and key!='official_starter_only') or (is_pit and key!='official_batting_only')): assert a['features'][feature]==v,(fid,key,feature)
            for prior in a['features'][feature]['source_fixture_ids']:
                assert prior in bygame and bygame[prior]['date']<dt,(fid,key,feature,prior)
        if fid in old[key]: assert a['features']==old[key][fid]['features']
    for side in ('home','away'):
        for kind in ('lineup','starter'):
            left='official_batting_only' if kind=='lineup' else 'official_starter_only'
            assert arms[left][fid]['features'][side+'_'+kind+'_strength']==arms['official_batting_and_starter'][fid]['features'][side+'_'+kind+'_strength']

assert counts['batters']==12762 and counts['pitchers']==1418 and counts['unique_date_team']==256 and counts['excluded_unknown']==17 and counts['excluded_unresolved_slots']==21
for kind in ('bat','pit'):
    assert missing[kind]==(17 if kind=='bat' else 58)
    assert statistics.median(gaps[kind])==(2 if kind=='bat' else 7)
    assert sorted(gaps[kind])[(len(gaps[kind])*9+9)//10-1]==(63 if kind=='bat' else 70)
assert max(x['date'] for x in bat.values())=='2026-07-26' and max(x['date'] for x in pit.values())=='2026-07-26'
assert sum(bygame[f]['date']>'2026-07-26' for f in coh['all'])==239
# Poison an actual player's history with target-day and future records: independent selector excludes them and guard rejects them.
fid=coh['all'][0]; dt=bygame[fid]['date']; player=arms['official_batting_only'][fid]['features']['home_lineup_strength']['value'][0]
observed=player['history'] or []; full_history=bh[player['player_id']]
assert history_before(full_history,dt)==observed
for d in ('2026-07-27','2026-08-01'):
    poison={'date':dt if d=='2026-07-27' else '9999-12-31','fixture_id':fid,'ab':999999,'hit':999999,'bb':999999,'hr':999999}
    assert history_before(full_history+[poison],dt)==observed
    try: past_only(observed+[poison],dt)
    except AssertionError: pass
    else: raise AssertionError('poison escaped guard')
print(json.dumps({'status':'PASS','counts':dict(counts),'missing':dict(missing),'median':{k:statistics.median(v) for k,v in gaps.items()},'after_last_source':239,'poison_cases':2},ensure_ascii=False))
