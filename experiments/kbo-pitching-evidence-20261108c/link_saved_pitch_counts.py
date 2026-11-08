"""Read-only pitch-count sidecar for existing, fully verified forward archives.

Does not change normalized inputs, frozen predictions, collectors, or ledgers.
"""
import base64,gzip,hashlib,json,sys
from collections import Counter
from datetime import datetime,timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from research.kbo_forward import official
from research.m1_service.observer import _verify_archives

STATE=Path(r'C:/Users/user/Documents/ChatGPT/sportstoto-research-state/kbo-m0-forward-v1')
HERE=Path(__file__).resolve().parent

def rows_from_box(box, source_game, normalized, box_receipt, resolver, receipts):
    """Replay existing source-identity mapping; emit rows only for verified IDs."""
    out=[]; reasons=Counter(); seen=set()
    for side,table_index in (('away',3),('home',4)):
        team_id=str(source_game[side.upper()+'_ID']);team_name=str(source_game[side.upper()+'_NM']).strip()
        if team_id not in normalized['covered_player_teams']:continue
        table=box['tables'][table_index]; headers=[str(c.get('Text') or '').strip() for c in table['headers'][0]['row']]
        labels=('선수명','이닝','피안타','홈런','삼진','실점','자책','투구수')
        if any(headers.count(label)!=1 for label in labels):raise ValueError('pitching_columns_missing_or_ambiguous')
        for index,row in enumerate(table['rows']):
            cells=[str(c.get('Text') or '').strip() for c in row['row']];name=cells[headers.index('선수명')]
            try:
                if name.isdigit():pid=name
                else:pid,_=resolver(name,team_name,'pitcher',source_game['SEASON_ID'],identity_receipts=receipts)
            except official.SourceError as exc:
                if str(exc) not in official.IDENTITY_AMBIGUITY:raise
                reasons['unresolved_identity']+=1;continue
            if not pid.isdecimal() or int(pid)<=0 or pid in seen:raise ValueError('invalid_or_duplicate_player_id')
            seen.add(pid); saved=normalized['pitchers'].get(pid)
            if saved is None:raise ValueError('resolved_player_missing_from_verified_history')
            actual={'outs':official.innings_outs(cells[headers.index('이닝')])}
            for k,label in (('hit','피안타'),('hr','홈런'),('kk','삼진'),('r','실점'),('er','자책')):actual[k]=int(cells[headers.index(label)])
            if any(saved.get(k)!=v for k,v in actual.items()):raise ValueError('resolved_pitcher_stats_mismatch')
            value=cells[headers.index('투구수')]
            if not value.isdecimal():reasons['missing_pitch_count']+=1;continue
            out.append({'player_id':pid,'team_id':team_id,'fixture_id':normalized.get('official_fixture_id',normalized['fixture_id']),'pitch_count':int(value),'box_sha256':box_receipt['sha256'],'box_received_at':box_receipt['finished_at'],'table_index':table_index,'row_index':index})
    return out,dict(reasons)

def extract_capture(c,state=STATE):
    payload=c['input'];frozen=datetime.fromisoformat(c['frozen_at']);kickoff=datetime.fromisoformat(payload['event']['kickoff_at']);
    if frozen>kickoff-timedelta(minutes=30):raise ValueError('capture_after_t30')
    root=state/'official';_verify_archives(payload,root,frozen)
    ref=payload['evidence']['history']['body'];bundle=json.loads(gzip.decompress((root/ref['relative_file']).read_bytes())); responses=bundle['responses'];history=bundle['normalized_history'];receipts=[r['receipt'] for r in responses]
    client=official.OfficialClient.__new__(official.OfficialClient);client.root=root
    def no_network(*args,**kwargs):raise ValueError('offline_only')
    client.fetch=no_network
    sources={}
    for response in responses:
        rec=response['receipt']
        if rec['url']!=official.SCHEDULE:continue
        for game in json.loads(base64.b64decode(response['body_base64'])).get('game',[]):
            gid=game.get('G_ID')
            if gid:
                if gid in sources and sources[gid]!=game:raise ValueError('conflicting_official_schedule')
                sources[gid]=game
    box_responses={}
    for response in responses:
        rec=response['receipt']
        if rec['url']==official.BOX:
            sha=rec['sha256']
            if sha in box_responses and box_responses[sha]!=response:raise ValueError('conflicting_box_receipts')
            box_responses[sha]=response
    result=[]; excluded=Counter()
    for game in history['games']:
        sha=(game.get('source') or {}).get('box_sha256')
        if not sha:excluded['no_box_reference']+=1;continue
        response=box_responses.get(sha)
        if response is None:raise ValueError('box_body_missing')
        rec=response['receipt'];gid=str(rec['params']['gameId']); source=sources.get(gid)
        if not source or game.get('official_fixture_id',game['fixture_id'])!=gid+str(source['SEASON_ID']):raise ValueError('fixture_mismatch')
        parsed=json.loads(base64.b64decode(response['body_base64'],validate=True));rows,why=rows_from_box(parsed,source,game,rec,client.resolve_identity,receipts)
        result.extend(rows);excluded.update(why)
    starters={payload['announcement']['roster'][side]['starter'] for side in ('home','away')}
    selected=[row for row in result if row['player_id'] in starters]
    return {'fixture_id':payload['event']['official_game_id'],'frozen_at':c['frozen_at'],'history_bundle_sha256':ref['archived_canonical_bundle_sha256'],'verified_pitcher_rows_total':len(result),'rows':selected,'excluded':dict(excluded)}

def main():
    path=STATE/'paired-ledger.jsonl';raw=path.read_bytes();records=[json.loads(l) for l in raw.splitlines()]; captures=[r for r in records if r.get('type')=='capture'];out={'source_sha256':hashlib.sha256(raw).hexdigest(),'sidecar_only':True,'captures':[extract_capture(c) for c in captures], 'limitations':['No new prediction or normalized input generated','Verification replays archived local evidence and timestamps; no external clock authentication','History source receipts are pre-existing; not proof of actual bet execution']};(HERE/'verified-pitch-counts.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps([{'fixture_id':c['fixture_id'],'rows':len(c['rows']),'verified_pitcher_rows_total':c['verified_pitcher_rows_total'],'excluded':c['excluded']} for c in out['captures']],ensure_ascii=False))
if __name__=='__main__':main()
