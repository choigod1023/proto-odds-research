"""Freeze committed fixture snapshots and obtain bounded public official rounds."""
import gzip,hashlib,json,re,shutil,time
from collections import Counter
from datetime import datetime,date,timezone,timedelta
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parent
KST=timezone(timedelta(hours=9))
BASE='https://www.betman.co.kr'
CACHES=[Path('C:/Users/user/Documents/Codex/klprice/experiments/kleague-official-prices-20261103/evidence'),Path('C:/Users/user/Documents/Codex/kbo-official-odds-20261103/experiments/kbo-official-odds-20261103/evidence')]
def sha(b):return hashlib.sha256(b).hexdigest()
def readgz(p):return gzip.decompress(p.read_bytes())
def write(p,d):p.write_bytes((json.dumps(d,ensure_ascii=False,indent=2)+'\n').encode())
def inputs():
    all={}
    for x in json.loads((ROOT/'inputs/sources.json').read_bytes()):
        raw=readgz(ROOT/x['file'])
        if sha(raw)!=x['raw_sha256']:raise ValueError('input sha mismatch')
        all[x['worker']+':'+Path(x['git_path']).name]=raw
    return all

def fixtures():
    data=inputs();out=[]
    census=json.loads(data['blu:census.json']);scores={x['gmkey']:x for x in json.loads(data['blu:postgame-schedule.body'])}
    invalid={x['gmkey'] for x in json.loads(data['blu:postgame-index.json'])['sample_invalid']}
    for f in census['regular_season_fixtures']:
        if f['gmkey'] not in census['sample_ids']:continue
        s=scores[f['gmkey']];stamp=datetime.strptime(s['gameDate']+s['gameStart'],'%Y%m%d%H%M').replace(tzinfo=KST)
        out.append({'league':'KBL','season':'2025-26','fixture_id':f['gmkey'],'kickoff':stamp.isoformat(),'home':str(s['tcodeH']),'away':str(s['tcodeA']),'home_name':s['tnameH'],'away_name':s['tnameA'],'score':[s['scoreH'],s['scoreA']],'state':'completed' if s.get('isEnded')==1 else 'not_finished','player_record_verified':f['gmkey'] not in invalid,'source_worker':'blu'})
    for f in json.loads(data['vmlu:schedule.body'])['payload']['content']:
        stamp=datetime.strptime(f['gdate']+' '+f['gstime'],'%Y-%m-%d %H:%M').replace(tzinfo=KST)
        out.append({'league':'VLEAGUE_M','season':'2025-26','fixture_id':f"001-022-201-{f['gnum']}",'kickoff':stamp.isoformat(),'home':str(f['hcode']),'away':str(f['acode']),'home_name':f['hname'],'away_name':f['aname'],'score':[f['hspoint'],f['aspoint']],'state':'completed' if max(f['hspoint'],f['aspoint'])==3 else 'not_finished','source_worker':'vmlu'})
    npb=[json.loads(x) for x in data['nlu:postgame-records.jsonl'].splitlines()];census=[json.loads(x) for x in data['nlu:fixture-census.jsonl'].splitlines()];pairs=Counter()
    for day in census:
        for path in day.get('fixture_paths',[]):
            m=re.fullmatch(r'/scores/2026/\d{4}/([a-z]+)-([a-z]+)-\d+/',path)
            if m:pairs[(day['date'],m[1],m[2])]+=1
    for f in npb:
        if f['state']=='cancelled':state='cancelled'
        elif f['state']!='completed':state='not_finished'
        elif f.get('completion_type')!='normal_or_extra_innings':state='called_game_requires_review'
        elif pairs[(f['date'],f['home_team_id'],f['away_team_id'])]!=1:state='doubleheader_ambiguous'
        else:state='completed'
        score=(f.get('postgame') or {}).get('official_final_score') or {}
        out.append({'league':'NPB','season':'2026','fixture_id':f['official_game_id'],'kickoff':f.get('actual_start_jst'),'home':f['home_team_id'],'away':f['away_team_id'],'score':[score.get('home'),score.get('away')],'state':state,'source_worker':'nlu'})
    lineups=[json.loads(x) for x in gzip.decompress(data['fblu:lineups.jsonl.gz']).splitlines()]; labels={x['fixture_id']:x for x in (json.loads(t) for t in gzip.decompress(data['fblu:postgame-labels.jsonl.gz']).splitlines())}
    for f in lineups:
        mat=re.search(r'(\d{4})/(\d{1,2})/(\d{1,2}).*?(\d{1,2}):(\d{2})',f['kickoff_display_actual'])
        stamp=datetime(*map(int,mat.groups()),tzinfo=KST).isoformat() if mat else None
        out.append({'league':'J1','season':'2025','fixture_id':f['fixture_id'],'kickoff':stamp,'home':f['clubs'][0]['id'],'away':f['clubs'][1]['id'],'score':labels[f['fixture_id']]['score_reported'],'state':'completed' if stamp and labels[f['fixture_id']]['score_reported'] else 'missing_identity_or_result','source_worker':'fblu'})
    ids=[(x['league'],x['fixture_id']) for x in out]
    if len(set(ids))!=len(ids):raise ValueError('duplicate official fixture')
    return out

def plan_rounds(fs,rounds):
    mapping={};plan={}
    for f in fs:
        if f['state']!='completed' or not f.get('kickoff'):continue
        dt=datetime.fromisoformat(f['kickoff']);day=dt.date()
        valid=[r for r in rounds if r.get('gmOsidTsYear')==day.year and r.get('saleStartDate') and r.get('saleEndDate') and datetime.fromtimestamp(r['saleStartDate']/1000,KST).date()<=day<=datetime.fromtimestamp(r['saleEndDate']/1000,KST).date()]
        mapping[f['fixture_id']]=[r['gmTs'] for r in valid]
        for r in valid:plan[r['gmTs']]=r
    return mapping,plan

def main():
    (ROOT/'evidence').mkdir(exist_ok=True);fs=fixtures();rootledger=ROOT/'evidence/attempts.json';ledger=json.loads(rootledger.read_bytes()) if rootledger.exists() else []
    if any(x.get('http_status') in (403,429) for x in ledger):raise RuntimeError('previous 403/429: stop')
    entries=[]
    for cache in CACHES:
        for rec in json.loads((cache/'attempts.json').read_bytes()):
            if rec.get('http_status')==200 and rec.get('body_file'):entries.append((cache,rec))
    closed=next((c,r) for c,r in entries if r['name']=='closed-list');raw=readgz(closed[0]/Path(closed[1]['body_file']).name)
    if sha(raw)!=closed[1]['body_sha256']:raise ValueError('closed list hash')
    mapping,plan=plan_rounds(fs,json.loads(raw)['schedules']['data']);print('fixtures',len(fs),'rounds',len(plan),flush=True)
    # Preserve the exact official closed list and receipt metadata.
    cp=ROOT/'evidence/closed-list.body.gz'
    if not cp.exists():shutil.copyfile(closed[0]/Path(closed[1]['body_file']).name,cp)
    if not any(x['name']=='closed-list' for x in ledger):ledger.append(dict(closed[1])|{'body_file':'evidence/closed-list.body.gz','origin':'reused_cache'});write(rootledger,ledger)
    done={r['name'] for r in ledger if r.get('http_status')==200};new=sum(x.get('origin')=='new_request' for x in ledger)
    with requests.Session() as sess:
        for ts,rd in sorted(plan.items()):
            name=f"detail-{rd['gmOsidTsYear']}-{rd['gmOsidTs']}"
            if name in done:continue
            hits=[(r['received_at'],r['body_sha256'],c,r) for c,r in entries if r.get('name')==name]
            dest=ROOT/'evidence'/f'{name}.body.gz'
            if hits:
                _,_,cache,old=max(hits,key=lambda x:(x[0],x[1]));body=readgz(cache/Path(old['body_file']).name)
                if sha(body)!=old['body_sha256']:raise ValueError('cache hash mismatch')
                shutil.copyfile(cache/Path(old['body_file']).name,dest);record=dict(old)|{'origin':'reused_latest_receipt','source_cache':str(cache),'body_file':'evidence/'+dest.name}
            else:
                if new>=200:raise RuntimeError('new request cap 200 reached')
                payload={'gmId':'G101','gmTs':str(ts),'gameYear':str(rd['gmOsidTsYear']),'_sbmInfo':{'debugMode':'false'}}
                record={'name':name,'origin':'new_request','method':'POST','url':BASE+'/buyPsblGame/gameInfoInq.do','request_json':payload,'timeout_seconds':15};new+=1
                try:
                    r=sess.post(record['url'],json=payload,timeout=15,headers={'User-Agent':'Mozilla/5.0','X-Requested-With':'XMLHttpRequest','Referer':BASE+'/main/mainPage/gamebuy/closedGameList.do'})
                    raw=r.content;dest.write_bytes(gzip.compress(raw,mtime=0));record.update(http_status=r.status_code,received_at=datetime.now(timezone.utc).isoformat(),body_sha256=sha(raw),body_file='evidence/'+dest.name)
                except requests.RequestException as e:record.update(http_status=None,failed_at=datetime.now(timezone.utc).isoformat(),error_type=type(e).__name__,error=str(e))
                ledger.append(record);write(rootledger,ledger);print(name,record.get('http_status'),flush=True)
                if record.get('http_status') in (403,429):break
                time.sleep(1);continue
            ledger.append(record);write(rootledger,ledger)
    print('responses',sum(x.get('http_status')==200 and x['name'].startswith('detail-') for x in ledger),'new requests',new,flush=True)

if __name__=='__main__':main()
