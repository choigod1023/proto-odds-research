"""Independent audit of pinned 2023 KBO source; no model fitting or network."""
import base64, collections, gzip, hashlib, json, re, subprocess
from html import unescape
from pathlib import Path
from official_parse import confirmed_lineups

DATA=Path(r'C:\Users\user\Documents\Codex\kbtdata')
BASE=Path(r'C:\Users\user\Documents\Codex\kbodata')
COMMIT='38463a3f2eb9ac2b00c4ddf9edd6ebacf8a99b55'
BASE_COMMIT='13291d23e855e0d500327cb39ac64a480e395837'
PREFIX='experiments/kbo-actual-training-data-20261103/'
EXPECTED={'actual-rosters-2023.jsonl.gz':'53ea290c9bfe40dd792ddfd6b952faae4ee2d151176e0729a00abd8d216c9ff4','actual-rosters-2023-raw.jsonl.gz':'f8d07586349e907d5acaa16103b5648489d452f1e533652715108e9b40b4ef15'}
def blob(repo,commit,path):return subprocess.check_output(['git','-C',str(repo),'show',commit+':'+path])
def digest(b):return hashlib.sha256(b).hexdigest()
def records(b):return [json.loads(x) for x in gzip.decompress(b).splitlines()]
def ensure(ok,why):
    if not ok:raise ValueError(why)
def textrow(r):return [str(x.get('Text') or '').strip() for x in r['row']]
def profile(b):
    text=b.decode('utf-8')
    found=re.findall(r'<span\b[^>]*\bid=["\'][^"\']*playerProfile_lblName["\'][^>]*>(.*?)</span>',text,re.I|re.S)
    ensure(len(found)==1,'profile label ambiguous')
    return unescape(re.sub('<[^>]*>','',found[0])).strip()

def run():
    payload={k:blob(DATA,COMMIT,PREFIX+k) for k in EXPECTED}
    for k,sha in EXPECTED.items():ensure(digest(payload[k])==sha,'pinned digest '+k)
    manifest=json.loads(blob(DATA,COMMIT,PREFIX+'actual-rosters-2023-manifest.json'))
    rows=records(payload['actual-rosters-2023.jsonl.gz']); rawrows=records(payload['actual-rosters-2023-raw.jsonl.gz'])
    raws={}
    for rec in rawrows:
        raw=base64.b64decode(rec['raw_base64'],validate=True)
        ensure(len(raw)==rec['bytes'] and digest(raw)==rec['sha256'] and rec['sha256'] not in raws,'raw bundle corruption or duplicate')
        raws[rec['sha256']]=raw
    def evidence(meta, endpoint, method):
        ensure(meta['url']=='https://www.koreabaseball.com/'+endpoint and meta['method']==method and meta['http_status']==200 and bool(meta['received_at']),'request metadata '+endpoint)
        b=raws[meta['sha256']];ensure(len(b)==meta['bytes'],'response bytes mismatch')
        return b
    population=[json.loads(x) for x in blob(BASE,BASE_COMMIT,'experiments/kbo-player-data-20261103/rows.jsonl').splitlines()]
    pop={x['fixture_id']:x for x in population if x['season']==2023}
    ensure(len(pop)==723 and len(rows)==723 and len({x['fixture_id'] for x in rows})==723,'population size or duplicates')
    ensure(set(pop)=={x['fixture_id'] for x in rows},'population set mismatch')
    ensure(len({x['official_game_id'] for x in rows})==len(rows),'official game ID maps to multiple fixtures')
    ensure(manifest['source_rows_commit']==BASE_COMMIT and manifest['rows_sha256']==EXPECTED['actual-rosters-2023.jsonl.gz'] and manifest['raw_evidence_bundle_sha256']==EXPECTED['actual-rosters-2023-raw.jsonl.gz'],'manifest provenance mismatch')
    bat=json.loads(blob(BASE,BASE_COMMIT,'data/raw/detail/kbo_batters_indiv_2023_2026.json'))
    pit=json.loads(blob(BASE,BASE_COMMIT,'data/raw/detail/kbo_baseball_2023_2026.json'))
    counts=collections.Counter(); used=set();conflicts=[]
    for r in rows:
        fid=r['fixture_id'];gameid=r['official_game_id'];original=pop[fid]; date=r['date'].replace('-',''); ev=r['source_evidence']
        ensure(r['status']=='complete' and r['exclusions']==[] and r['historical_receipt_proven'] is False and r['historical_publication_time_proven'] is False and r['target_player_stats_used_as_features'] is False,'status/availability '+fid)
        sm=ev['schedule'];schedule=json.loads(evidence(sm,'ws/Main.asmx/GetKboGameList','POST'));used.add(sm['sha256'])
        ensure(str(sm['params']['date'])==date,'schedule request date '+fid)
        matches=[x for x in schedule['game'] if x['G_ID']==gameid];ensure(len(matches)==1,'schedule game '+fid);game=matches[0]
        ensure(str(game['G_DT'])==date and int(game['SEASON_ID'])==2023 and int(game['LE_ID'])==1 and str(game['GAME_STATE_SC'])=='3' and str(game['GAME_RESULT_CK'])=='1','schedule game metadata '+fid)
        ensure(original['kickoff_at'][:10]==r['date'] and all(original[s+'_id']==r['teams'][s]==game[s.upper()+'_NM'].strip() for s in ('home','away')),'team/date mismatch '+fid)
        if fid==gameid+'2023':counts['exact_game_id']+=1
        else:
            ensure(r['fixture_match_basis']=='encoded_year_alias_exact_official_schedule_match' and fid[4:13]==gameid[4:] and fid[-4:]=='2023','alias '+fid);counts['year_alias']+=1
        counts['kickoff_conflicts']+=bool(r['kickoff_conflict'])
        lm=ev['lineup'];bm=ev['actual_pitcher_record'];
        for m,endpoint in ((lm,'ws/Schedule.asmx/GetLineUpAnalysis'),(bm,'ws/Schedule.asmx/GetBoxScore')):
            ensure(str(m['params']['gameId'])==gameid and int(m['params']['leId'])==1 and int(m['params']['seasonId'])==2023 and str(m['params']['srId'])==str(game['SR_ID']),'request game metadata '+fid)
            used.add(m['sha256'])
        lineup=evidence(lm,'ws/Schedule.asmx/GetLineUpAnalysis','POST')
        parsed=confirmed_lineups(lineup,gameid,game['HOME_ID'],game['AWAY_ID'],2023,int(game['SR_ID']))['confirmed_lineups']
        box=json.loads(evidence(bm,'ws/Schedule.asmx/GetBoxScore','POST'));ensure(str(box.get('code'))=='100' and len(box['tables'])==5,'box envelope '+fid)
        allids=[];starterids=[]
        for side,idx,prefix in (('away',3,'T'),('home',4,'B')):
            team=game[side.upper()+'_ID'];players=r['hitters'][side]
            ensure([(x['slot'],x['name']) for x in players]==parsed[team],'official confirmed batting order '+fid)
            for x in players:
                pid=x['player_id'];pe=x['evidence']['profile'];allids.append(pid)
                ensure(pid.isdigit() and x['evidence']['archive_identity_only'] is True and x['evidence']['archive_fixture_id']==fid and x['evidence']['lineup']==lm,'hitter identity metadata '+fid)
                ensure(pe['params']['playerId']==pid and profile(evidence(pe,'Record/Player/HitterDetail/Basic.aspx','GET'))==x['name'],'hitter official profile '+fid);used.add(pe['sha256'])
                candidates=[z for z in bat[fid]['data'][side] if str(z.get('code'))==pid and z.get('order')==x['slot']]
                ensure(bool(candidates),'hitter archive identity '+fid);counts['hitter_slots']+=1
            table=box['tables'][idx];headers=textrow(table['headers'][0]);ensure(headers.count('선수명')==headers.count('등판')==1,'pitcher headers '+fid)
            ni=headers.index('선수명');ai=headers.index('등판');pitchrows=[textrow(z) for z in table['rows']]
            starts=[z for z in pitchrows if z[ai]=='선발'];ensure(len(starts)==1 and pitchrows[0][ai]=='선발','pitcher flag '+fid)
            p=r['starters'][side];pid=p['player_id'];pe=p['evidence'];starterids.append(pid);claim=starts[0][ni]
            ensure(p['actual_record_verified'] is True and pid.isdigit() and pe['box_row_player_field']==claim and pe['box_table_index']==idx and pe['schedule_team_id']==team and pe['official_record']==bm and pe['schedule']==sm,'starter metadata '+fid)
            sched_id=str(game[prefix+'_PIT_P_ID']);sched_name=str(game[prefix+'_PIT_P_NM']).strip()
            if pe['schedule_matches_actual']:
                ensure(pid==sched_id and p['name']==sched_name and claim in (pid,sched_name),'starter schedule/box mismatch '+fid)
            else:
                profilemeta=pe['profile'];ensure(profilemeta['params']['playerId']==pid and profile(evidence(profilemeta,'Record/Player/PitcherDetail/Basic.aspx','GET'))==p['name'],'starter profile '+fid);used.add(profilemeta['sha256'])
                ensure(claim in (pid,p['name']),'starter box/profile mismatch '+fid);counts['starter_schedule_mismatch']+=1
            archive=[z for z in (pit.get(fid) or {}).get('data',{}).get(side,[]) if str(z.get('pcode'))==pid]
            ensure(bool(archive),'starter same game player ID '+fid)
            counts['starters']+=1
        ensure(len(set(allids))==18 and len(set(starterids))==2,'duplicate player IDs '+fid)
        counts['fixtures']+=1
    ensure(len(raws)==manifest['official_raw_response_count']==1902,'raw count mismatch')
    print(json.dumps({'commit':COMMIT,'source_commit':BASE_COMMIT,'counts':counts,'bundle_raw_count':len(raws),'raw_referenced_checked':len(used),'raw_digest_verified':len(raws),'excluded_population':0,'unexpected_population':0,'historical_publication_proven':False,'historical_receipt_proven':False,'interpretation':'confirmed lineup is retrospective; it is not proof all nine played or were received before the game'},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':run()
