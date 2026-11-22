"""Frozen retrospective official-lineup-only overlay, not a PIT input."""
import bisect,copy,gzip,hashlib,json,os,re,subprocess
from collections import Counter,defaultdict
from datetime import datetime
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]; HERE=Path(__file__).resolve().parent
CAPTURE=Path(r'C:\Users\user\Documents\Codex\klu\.research-state\kbo-lineups-v4')
EXPECTED={'rows.jsonl':'66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c',
 'labels.jsonl':'ef32ffe806b41521f5bd53c12d0eea1362bd44293181fc3ebb7c24c6fcb249ca',
 'prior-projected-features.jsonl.gz':'eca78a16ca59ccc1de6eca47713acded513541c59cddbbb4d8dd193e7c5b27ae',
 'batters':'24bdea9094046bdab4967d314c043d548a252599b652009d5ffdabd341cc84e8',
 'official':'92015aa1af2e23813b0b4d8c3c5bd182d1c0ee7b2226920af3445e86569ae67c',
 'official_manifest':'e3d0c225ddb754d894bca28b57e5ab07918244aed0883cb1490b2d05e7484c9e',
 'official_audit':'442a457025c33b4e8e010a7f3a01c37554d1a2c912b0b87c52e60f5c3179fbaa'}
SCENARIO='retrospective_official_confirmed_lineup_assumed_available'
BASE_COMMIT='b0833a69814b3fadf1eb91b8205abc35ffee4d55'
PROTOCOL_COMMIT='82018ecccb23fdcfebc00c362d90848b4a884063'
BAT=ROOT/'data/raw/detail/kbo_batters_indiv_2023_2026.json'
GAMES=ROOT/'data/model_candidates/kbo/archived-games.jsonl.gz'


def sha(raw):return hashlib.sha256(raw).hexdigest()
def bytes_sha(path):return sha(Path(path).read_bytes())
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')

def unique_index(values):
    result={}
    for row in values:
        ident=row.get('fixture_id')
        if not isinstance(ident,str) or not ident or ident in result:raise ValueError('duplicate or invalid fixture')
        result[ident]=row
    return result


def verify_base():
    result={}
    for relative,want in [('experiments/kbo-player-data-20261103/rows.jsonl',EXPECTED['rows.jsonl']),('experiments/kbo-player-data-20261103/labels.jsonl',EXPECTED['labels.jsonl']),('experiments/kbo-player-data-20261103/prior-projected-features.jsonl.gz',EXPECTED['prior-projected-features.jsonl.gz']),('data/raw/detail/kbo_batters_indiv_2023_2026.json',EXPECTED['batters'])]:
        work=(ROOT/relative).read_bytes();blob=subprocess.check_output(['git','cat-file','blob',BASE_COMMIT+':'+relative],cwd=ROOT)
        if sha(work)!=want or sha(blob)!=want or work!=blob:raise ValueError('raw worktree/git blob mismatch: '+relative)
        result[relative]={'raw_sha256':sha(work),'git_blob_sha256':sha(blob),'exact_bytes_equal':True}
    return result


def aware(value):
    try:dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    except (TypeError,ValueError,AttributeError):raise ValueError('missing actual receipt timestamp')
    if dt.utcoffset() is None:raise ValueError('naive receipt timestamp')
    return value


@lru_cache(maxsize=8192)
def source_bytes(digest):
    if not re.fullmatch(r'[0-9a-f]{64}',str(digest)):raise ValueError('invalid source hash')
    raw=(CAPTURE/'raw'/digest).read_bytes()
    if sha(raw)!=digest:raise ValueError('official response bytes mismatch')
    return raw


def check_evidence(e):
    if e.get('http_status')!=200 or not e.get('url'):raise ValueError('invalid evidence')
    aware(e.get('received_at'));raw=source_bytes(e.get('sha256'))
    if e.get('bytes')!=len(raw):raise ValueError('source size mismatch')
    return raw


class Profile(HTMLParser):
    def __init__(self):super().__init__();self.inside=False;self.names=[]
    def handle_starttag(self,tag,attrs):
        if tag=='span' and dict(attrs).get('id','').endswith('playerProfile_lblName'):self.inside=True
    def handle_data(self,data):
        if self.inside:self.names.append(data.strip())
    def handle_endtag(self,tag):
        if tag=='span' and self.inside:self.inside=False


def profile_name(raw):
    parser=Profile();parser.feed(raw.decode('utf8'))
    if len(parser.names)!=1 or not parser.names[0]:raise ValueError('ambiguous profile name')
    return parser.names[0]


def validate(official,source):
    fid=source['fixture_id']; gid=official.get('official_game_id');day=official.get('game_date')
    if official.get('fixture_id')!=fid or official.get('season_id')!=2026 or official.get('league_id')!=1:raise ValueError('fixture/league/season mismatch')
    if official.get('fixture_match_basis')!='exact_official_game_id' or fid!=str(gid)+'2026':raise ValueError('official doubleheader/game ID mismatch')
    if not re.fullmatch(r'2026\d{4}',str(day)) or not str(gid).startswith(day) or fid[:8]!=day:raise ValueError('date mismatch')
    if source['season']!=2026 or (source['kickoff_at'] and source['kickoff_at'][:10]!=f'{day[:4]}-{day[4:6]}-{day[6:]}'):raise ValueError('kickoff date mismatch')
    if official.get('lineup_flag') is not True or official.get('lineup_status')!='official_flag_true_profile_name_crossmatched':raise ValueError('unconfirmed lineup')
    if official.get('eligible_as_pregame_feature') is not False or official.get('historical_receipt_proven') is not False or official.get('historical_publication_time_proven') is not False:raise ValueError('PIT status invalid')
    schedule=json.loads(check_evidence(official['schedule_evidence']))
    matches=[g for g in schedule.get('game',[]) if g.get('G_ID')==gid]
    if len(matches)!=1:raise ValueError('ambiguous schedule fixture')
    game=matches[0]
    for key,expected in [('G_ID',gid),('G_DT',day),('LE_ID',1),('SEASON_ID',2026),('SR_ID',official['series_id']),('HEADER_NO',official['header_no']),('HOME_ID',official['home_team_id']),('AWAY_ID',official['away_team_id'])]:
        if str(game.get(key))!=str(expected):raise ValueError('schedule identity mismatch: '+key)
    if game.get('HOME_ID')==game.get('AWAY_ID'):raise ValueError('duplicate official teams')
    if (str(game.get('HOME_NM','')).strip(),str(game.get('AWAY_NM','')).strip())!=(source['home_id'],source['away_id']):raise ValueError('team mapping mismatch')
    if source['kickoff_at'] and source['kickoff_at'][11:16]!=str(game.get('G_TM')):raise ValueError('kickoff time mismatch')
    if official['lineup_evidence'].get('params',{}).get('gameId')!=gid:raise ValueError('wrong lineup request')
    body=json.loads(check_evidence(official['lineup_evidence']))
    if not isinstance(body,list) or len(body)!=5 or not body[0] or body[0][0].get('LINEUP_CK') is not True:raise ValueError('raw lineup unconfirmed')
    output={};seen=set()
    for side,meta_i,table_i in [('home',1,3),('away',2,4)]:
        meta=body[meta_i][0]
        for key,v in [('G_ID',gid),('T_ID',game[side.upper()+'_ID']),('LE_ID',1),('SEASON_ID',2026),('SR_ID',official['series_id'])]:
            if str(meta.get(key))!=str(v):raise ValueError('lineup identity mismatch '+key)
        raw_players=json.loads(body[table_i][0])['rows']
        parsed=[(int(item['row'][0]['Text']),item['row'][2]['Text'].strip()) for item in raw_players]
        players=official.get('lineups',{}).get(side,[])
        if len(players)!=9 or sorted(p.get('order') for p in players)!=list(range(1,10)) or len(parsed)!=9:raise ValueError('not exactly nine ordered players')
        parsed=sorted(parsed)
        if [x[0] for x in parsed]!=list(range(1,10)):raise ValueError('duplicate raw order')
        selected=[]
        for item,(order,name) in zip(sorted(players,key=lambda p:p['order']),parsed):
            player=item.get('player_id'); ev=item.get('official_profile_evidence') or {}
            if item['order']!=order or item.get('name')!=name or not isinstance(player,str) or not player.isdigit() or player in seen:raise ValueError('invalid duplicate player or name')
            if item.get('player_id_basis')!='postgame_same_game_side_order_and_official_profile_name':raise ValueError('player mapping basis not same game')
            if str((ev.get('params') or {}).get('playerId'))!=player or profile_name(check_evidence(ev))!=name:raise ValueError('profile mismatch')
            seen.add(player);selected.append({'order':order,'player_id':player,'name':name,'profile_evidence':ev})
        output[side]=selected
    return output


def index_history(games,batters):
    histories=defaultdict(list)
    for g in sorted(games,key=lambda x:(x['date'],x['source_fixture_id'])):
        fid=g['source_fixture_id'];box=batters.get(fid)
        if box is None:continue
        # Provenance/identity only; deliberately never inspect target scores.
        if (box.get('date'),box.get('home'),box.get('away'))!=(g['date'],g['home'],g['away']):raise ValueError('prior box identity mismatch')
        for side in ('home','away'):
            for item in (box.get('data') or {}).get(side,[]):
                if item.get('code'):
                    histories[str(item['code'])].append({'fixture_id':fid,'date':g['date'],**{key:item.get(key) for key in ('ab','hit','bb','hr')}})
    return histories


def replace_lineups(base,selected,history,target_date,official):
    result=copy.deepcopy(base); original=base['features']; nonlineup={k:v for k,v in original.items() if k not in ('home_lineup_strength','away_lineup_strength')}
    for side in ('home','away'):
        profiles=[];ids=set();dates=[]
        for player in selected[side]:
            pid=player['player_id'];all_history=history.get(pid,[])
            # Strict target-date comparison, independent of target player stats or game outcome.
            prior=[x for x in all_history if x['date']<target_date][-10:]
            if any(x['date']>=target_date or x['fixture_id']==base['fixture_id'] for x in prior):raise ValueError('temporal leak')
            profiles.append({'player_id':pid,'history':prior or None})
            ids.update(x['fixture_id'] for x in prior);dates.extend(x['date'] for x in prior)
        result['features'][side+'_lineup_strength']={'value':profiles,'source_fixture_ids':sorted(ids),'latest_source_date':max(dates,default=None),'completed_at':None,'received_at':None,'availability_basis':'prior_calendar_day_assumed_completed_not_received'}
    if canonical(nonlineup)!=canonical({k:v for k,v in result['features'].items() if k not in ('home_lineup_strength','away_lineup_strength')}):raise AssertionError('non-lineup changed')
    result['scenario']=SCENARIO
    result['official_lineup_context']={'official_game_id':official['official_game_id'],'official_game_date':official['game_date'],'home_team_id':official['home_team_id'],'away_team_id':official['away_team_id'], 'lineups':selected,'schedule_evidence':official['schedule_evidence'],'lineup_evidence':official['lineup_evidence'],'historical_publication_time':official.get('historical_publication_time'),'historical_receipt_proven':False,'eligible_as_pregame_feature':False,'point_in_time_verified':False,'basis':'retrospective_official_confirmed_lineup_assumed_available','baseline_projection_metadata_not_official_selector':True}
    result['warning']='Official current lineup is retrospectively assumed available; actual historical publication and receipt not proven. Never strict PIT.'
    return result


def main():
    base_checks=verify_base(); official_path=CAPTURE/'official-lineups.jsonl'
    if bytes_sha(official_path)!=EXPECTED['official']:raise ValueError('v4 official source changed')
    if bytes_sha(CAPTURE/'manifest.json')!=EXPECTED['official_manifest'] or bytes_sha(CAPTURE/'audit.json')!=EXPECTED['official_audit']:raise ValueError('v4 manifest/audit changed')
    old_inv=json.loads((HERE/'inventory.json').read_text(encoding='utf8'))
    if bytes_sha(GAMES)!=old_inv['source_hashes']['games']:raise ValueError('game archive changed')
    with (HERE/'rows.jsonl').open(encoding='utf8') as f:rows=unique_index(map(json.loads,f))
    with gzip.open(HERE/'prior-projected-features.jsonl.gz','rt',encoding='utf8') as f:projs=unique_index(map(json.loads,f))
    with gzip.open(GAMES,'rt',encoding='utf8') as f:games=list(map(json.loads,f))
    if len(rows)!=2913 or len(projs)!=2913 or set(rows)!=set(projs):raise ValueError('base coverage or duplicate identity')
    dates={g['source_fixture_id']:g['date'] for g in games};histories=index_history(games,json.loads(BAT.read_text(encoding='utf8')))
    official=[json.loads(s) for s in official_path.read_text(encoding='utf8').splitlines()];ids=[x['fixture_id'] for x in official]
    if len(official)!=533 or len(set(ids))!=533:raise ValueError('v4 duplicate/incomplete')
    selected=[x for x in official if x.get('season_id')==2026]
    if len(selected)!=193:raise ValueError('unexpected selected cohort')
    output=[];counts=Counter()
    for item in selected:
        fid=item['fixture_id']
        if fid not in rows or fid not in dates:raise ValueError('unknown fixture')
        chosen=validate(item,rows[fid]);target_date=dates[fid]
        if target_date!=f"{item['game_date'][:4]}-{item['game_date'][4:6]}-{item['game_date'][6:]}":raise ValueError('source game date mismatch')
        overlay=replace_lineups(projs[fid],chosen,histories,target_date,item)
        counts['market_present']+=int(rows[fid]['market'] is not None)
        counts['market_missing']+=int(rows[fid]['market'] is None)
        for side in ('home','away'):
            profiles=overlay['features'][side+'_lineup_strength']['value']
            counts['lineups_with_missing_history']+=int(any(p['history'] is None for p in profiles))
            for p in profiles:
                counts['players']+=1;counts['history_missing']+=int(p['history'] is None)
                counts['history_present']+=int(p['history'] is not None)
                for h in p['history'] or []:
                    if h['fixture_id'] not in dates or dates[h['fixture_id']]!=h['date'] or h['date']>=target_date:raise ValueError('prior source date mismatch')
        output.append(overlay)
    output.sort(key=lambda x:(dates[x['fixture_id']],x['fixture_id']))
    before_hash=hashlib.sha256();after_hash=hashlib.sha256()
    for item in output:
        fid=item['fixture_id']
        def nonlineup(r):return {'fixture_id':fid,'features':{k:v for k,v in r['features'].items() if k not in ('home_lineup_strength','away_lineup_strength')}}
        before_hash.update(canonical(nonlineup(projs[fid]))+b'\n');after_hash.update(canonical(nonlineup(item))+b'\n')
        if item['projection']!=projs[fid]['projection']:raise ValueError('baseline projection metadata changed')
    if before_hash.digest()!=after_hash.digest():raise ValueError('non-lineup feature hash mismatch')
    path=HERE/'official-lineup-overlay-2026.jsonl.gz';tmp=HERE/'official-lineup-overlay-2026.jsonl.gz.tmp'
    with tmp.open('wb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as f:
            for item in output:f.write((json.dumps(item,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf8'))
    os.replace(tmp,path)
    m={'schema_version':'kbo-official-lineup-overlay-v1','scenario':SCENARIO,'status':'retrospective_assumption_only_no_performance','protocol_commit':PROTOCOL_COMMIT,'base_commit':BASE_COMMIT,'source_official_commit':'fdcad1305bc2ce0652c011ee858056d9b2686cda','source_official_path':str(official_path),'source_official_sha256':EXPECTED['official'],'source_manifest_sha256':bytes_sha(CAPTURE/'manifest.json'),'source_audit_sha256':bytes_sha(CAPTURE/'audit.json'),'exact_raw_git_blob_checks':base_checks,'games_sha256':bytes_sha(GAMES),'cohort_2026_denominator':726,'official_2026_collected':193,'official_2026_unprocessed':533,'overlay_rows':len(output),'official_rejected':0,'archived_market_present':counts['market_present'],'archived_market_missing':counts['market_missing'],'lineups_with_missing_history':counts['lineups_with_missing_history'],'non_lineup_features_sha256_before':before_hash.hexdigest(),'non_lineup_features_sha256_after':after_hash.hexdigest(),'players_total':counts['players'],'player_history_present':counts['history_present'],'player_history_missing':counts['history_missing'],'point_in_time_verified':0,'output_sha256':bytes_sha(path),'source_bytes_policy':'Exact SHA256 raw checkout bytes must equal pinned Git blob bytes; no CRLF/LF normalization; official responses exact raw bytes, no git witness claimed.','limitations':['Current official lineup is assumed available retrospectively; historical publication, receipt and change history unproven.','Official player ID derives from same-game postgame order plus current official profile names, not independently conclusive identity.','Remaining 533 2026 games not processed, not proven missing.','Only lineup histories changed; prior baseline projection metadata kept as provenance, never official selector. Existing adapter.scalar supports the history objects, but adapter.build_features rejects the new scenario until a separate explicit adapter is implemented.','Prior date completion is an assumption and actual game completion/receipt times remain null.','Non-lineup input unchanged; no model fitting, evaluation, ROI or operating action.']}
    manifest=HERE/'official-lineup-overlay-2026-manifest.json';tmp=HERE/'official-lineup-overlay-2026-manifest.json.tmp';tmp.write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n',encoding='utf8');os.replace(tmp,manifest)
    print(json.dumps({k:m[k] for k in ('overlay_rows','players_total','player_history_present','player_history_missing','point_in_time_verified','output_sha256')},ensure_ascii=False))

if __name__=='__main__':main()
