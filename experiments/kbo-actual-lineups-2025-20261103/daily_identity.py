"""Disambiguate same-name pitchers from public official daily role evidence."""
import gzip,json
from pathlib import Path
from bs4 import BeautifulSoup
from collect import HERE,STATE,sha
from lineup_reference import profile_name

IDS=('51454','60146')
PITCHER_ARCHIVE=Path('C:/Users/user/Documents/Codex/kbodata/data/raw/detail/kbo_baseball_2023_2026.json')
PITCHER_ARCHIVE_SHA='c87faa6a43140dad067860086b00140fe8f05883a9e42121fd2e8f7233775a66'
def load_request(records,method,url_suffix,pid):
    item=next((x for x in reversed(records) if x.get('method')==method and x.get('url','').endswith(url_suffix) and str((x.get('params') or {}).get('playerId'))==pid and x.get('http_status')==200),None)
    if not item:raise ValueError('official_daily_or_profile_missing:'+pid)
    raw=gzip.decompress((STATE/item['raw_file']).read_bytes())
    if sha(raw)!=item['sha256']:raise ValueError('official_daily_sha_mismatch')
    return item,raw

def selected(doc,suffix):
    node=doc.select_one(f'select[id$={suffix}] option[selected]')
    return node.get('value') if node else None

def daily_roles(raw):
    doc=BeautifulSoup(raw.decode('utf8'),'html.parser')
    if (selected(doc,'ddlYear'),selected(doc,'ddlSeries'))!=('2025','0'):raise ValueError('daily_season_or_series_mismatch')
    result=[]
    for table in doc.find_all('table'):
        heads=[x.get_text(' ',strip=True) for x in table.select('thead th')]
        if heads.count('상대')!=1 or heads.count('구분')!=1:continue
        for tr in table.select('tbody tr'):
            cells=[x.get_text(' ',strip=True) for x in tr.find_all(['td','th'])]
            if len(cells)!=len(heads):continue
            if len(cells[0])==5 and cells[0][2]=='.':result.append({'month_day':cells[0],'opponent':cells[heads.index('상대')],'role':cells[heads.index('구분')]})
    return result

def derive():
    records=[json.loads(x) for x in (STATE/'requests.jsonl').read_bytes().splitlines()]
    archive_raw=PITCHER_ARCHIVE.read_bytes()
    if sha(archive_raw)!=PITCHER_ARCHIVE_SHA:raise ValueError('pitcher_identity_archive_sha_mismatch')
    archive=json.loads(archive_raw)
    daily={};profiles={}
    for pid in IDS:
        meta,raw=load_request(records,'POST','PitcherDetail/Daily.aspx',pid);daily[pid]=(meta,daily_roles(raw))
        pmeta,praw=load_request(records,'GET','HitterDetail/Basic.aspx',pid)
        if profile_name(praw)!='이승현':raise ValueError('profile_name_mismatch')
        text=BeautifulSoup(praw.decode('utf8'),'html.parser').get_text(' ',strip=True)
        hand='left' if '투수(좌투좌타)' in text else 'right' if '투수(우투우타)' in text else None
        if hand is None:raise ValueError('profile_throwing_hand_missing')
        profiles[pid]={'player_id':pid,'name':'이승현','throwing_hand':hand,'sha256':pmeta['sha256'],'received_at':pmeta['received_at']}
    if (profiles[IDS[0]]['throwing_hand'],profiles[IDS[1]]['throwing_hand'])!=('left','right'):raise ValueError('profile_handedness_not_unique')
    result={}
    for path in sorted((STATE/'stage1').glob('*.json')):
        one=json.loads(path.read_bytes());gid=one.get('official_game_id')
        if not gid:continue
        stage2=STATE/'stage2'/path.name
        if not stage2.exists():continue
        two=json.loads(stage2.read_bytes())
        if str(one['schedule_game'].get('T_PIT_P_ID'))!='51454':continue
        if not two.get('box_evidence'):continue
        pitcher_game=archive.get(one['fixture_id']) or archive.get(gid+'2025')
        expected_date=one['date'][:4]+'-'+one['date'][4:6]+'-'+one['date'][6:]
        if not pitcher_game or (pitcher_game.get('date'),pitcher_game.get('home'),pitcher_game.get('away'))!=(expected_date,one['home_team']['name'],one['away_team']['name']):continue
        ids={str(x.get('pcode')) for x in pitcher_game['data']['away'] if str(x.get('name') or '').strip()=='이승현'}
        if ids!=set(IDS):continue
        sched_raw=gzip.decompress((STATE/one['schedule_evidence']['raw_file']).read_bytes());sched=json.loads(sched_raw)
        same=[g for g in sched['game'] if (g.get('G_DT'),g.get('HOME_ID'),g.get('AWAY_ID'))==(one['date'],one['home_team']['id'],one['away_team']['id'])]
        if len(same)!=1 or same[0].get('G_ID')!=gid:continue
        box_raw=gzip.decompress((STATE/two['box_evidence']['raw_file']).read_bytes());box=json.loads(box_raw);table=box['tables'][3]
        headers=[str(x.get('Text') or '').strip() for x in table['headers'][0]['row']];values=[[str(c.get('Text') or '').strip() for c in row['row']] for row in table['rows']]
        same_name=[r for r in values if r[headers.index('선수명')]=='이승현']
        if len(same_name)!=2 or sum(r[headers.index('등판')]=='선발' for r in same_name)!=1:continue
        month_day=one['date'][4:6]+'.'+one['date'][6:8];opponent=one['home_team']['name'];checks=[]
        for pid in IDS:
            meta,rows=daily[pid];matches=[r for r in rows if r['month_day']==month_day and r['opponent']==opponent]
            if len(matches)!=1:break
            checks.append({'player_id':pid,'date':one['date'],'opponent':opponent,'role':matches[0]['role'],'daily_sha256':meta['sha256'],'daily_received_at':meta['received_at'],'profile':profiles[pid]})
        if len(checks)!=2 or [(x['player_id'],x['role']) for x in checks]!=[('51454','선발'),('60146','구원')]:continue
        result[one['fixture_id']]={'official_game_id':gid,'player_id':'51454','name':'이승현','actual_record_verified':True,'actual_record_starter_cell':'이승현','schedule_agrees':True,'identity_basis':'unique_same_day_official_pitcher_daily_roles_plus_schedule_and_box','daily_identity_checks':checks,
          'identity_candidate_ids':sorted(ids),'pitcher_identity_archive_sha256':PITCHER_ARCHIVE_SHA,'schedule_sha256':one['schedule_evidence']['sha256'],'box_sha256':two['box_evidence']['sha256']}
    if len(result)>5:raise ValueError('unexpected_daily_identity_scope')
    return result

def main():
    data=derive();p=HERE/'pitcher-daily-identity.json';p.write_bytes((json.dumps(data,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode());print(json.dumps({'resolved':len(data),'fixture_ids':sorted(data)},ensure_ascii=False))
if __name__=='__main__':main()
