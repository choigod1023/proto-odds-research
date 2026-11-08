"""Verify one official archive and one independently identified MyKBO box."""
import base64,gzip,hashlib,json,re,sys
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from research.m1_service.observer import _verify_archives
from research.kbo_forward import official

EVIDENCE=Path('C:/Users/user/Documents/Codex/kbo-source-evidence-20261108d');STATE=Path('C:/Users/user/Documents/ChatGPT/sportstoto-research-state/kbo-m0-forward-v1');HERE=Path(__file__).resolve().parent

def sha(raw):return hashlib.sha256(raw).hexdigest()
def parse_player(raw):
    soup=BeautifulSoup(raw,'html.parser');links=[a.get('href','') for a in soup.select('a[href]') if 'koreabaseball.com/Record/Player/PitcherDetail/Basic.aspx?playerId=' in a.get('href','')];ids={re.search(r'playerId=(\d+)',u).group(1) for u in links if re.search(r'playerId=(\d+)',u)}
    if len(ids)!=1:raise ValueError('official player link missing or ambiguous')
    game_links=[a for a in soup.select('a[href="/games/14037"]') if a.get_text(strip=True)=='2026-10-12']
    if len(game_links)!=1:raise ValueError('player game-date link missing or ambiguous')
    seasons=[]
    for table in soup.select('table'):
        headers=[x.get_text(' ',strip=True) for x in table.select('thead th')]
        if headers and headers[0]=='Year':
            for row in table.select('tbody tr'):
                vals=[x.get_text(' ',strip=True) for x in row.select('td')]
                if vals and vals[0] in ('2025','2026'):seasons.append(vals[0])
    if set(seasons)!={'2025','2026'}:raise ValueError('season rows missing')
    return {'official_player_id':next(iter(ids)),'site_player_id':'1990','game_log_link_date':'2026-10-12','season_rows':seasons}

def parse_game(raw):
    soup=BeautifulSoup(raw,'html.parser');title=soup.title.get_text(' ',strip=True) if soup.title else ''
    if not all(t in title for t in ('KT Wiz','Samsung Lions','October 12, 2026')):raise ValueError('game identity mismatch')
    matched=[];table_counts=[]
    for table in soup.select('table'):
        headers=[x.get_text(' ',strip=True) for x in table.select('thead th')]
        if 'NP' not in headers or 'IP' not in headers:continue
        table_counts.append(len(table.select('tbody tr')))
        for row in table.select('tbody tr'):
            links=row.select('a[data-id="1990"]');vals=[x.get_text(' ',strip=True) for x in row.select('td')]
            if links:
                if len(links)!=1 or not vals[headers.index('NP')].isdigit():raise ValueError('ambiguous or missing pitch count')
                matched.append({'team':headers[0],'innings':vals[headers.index('IP')],'pitch_count':int(vals[headers.index('NP')]),'earned_runs':int(vals[headers.index('ER')]),'strikeouts':int(vals[headers.index('SO')])})
    if len(matched)!=1:raise ValueError('site pitcher row missing or duplicate')
    return {'site_game_id':'14037','pitching_table_row_counts':table_counts,'pitcher':matched[0]}

def read_verified(file,record):
    b=file.read_bytes()
    if sha(b)!=record['sha256'] or len(b)!=record['bytes'] or record['status']!=200:raise ValueError('response integrity failed')
    return b

def main():
    receipt=json.loads((EVIDENCE/'receipts.json').read_text(encoding='utf8'));idx={r['url']:r for r in receipt['receipts']};purl='https://mykbostats.com/players/1990';gurl='https://mykbostats.com/games/14037';p=parse_player(read_verified(EVIDENCE/'player.bin',idx[purl]));g=parse_game(read_verified(EVIDENCE/'game.bin',idx[gurl]));records=[json.loads(s) for s in (STATE/'paired-ledger.jsonl').read_text(encoding='utf8').splitlines()];capture=next(r for r in records if r.get('type')=='capture' and r['input']['event']['official_game_id']=='20261106KTSS0');_verify_archives(capture['input'],STATE/'official',datetime.fromisoformat(capture['frozen_at']));ref=capture['input']['evidence']['history']['body'];bundle=json.loads(gzip.decompress((STATE/'official'/ref['relative_file']).read_bytes())); game=next(x for x in bundle['normalized_history']['games'] if x.get('official_fixture_id')=='20261012KTSS02026'); assert game['date']=='2026-10-12' and set(game['teams'])=={'KT','SS'};pitcher=game['pitchers'][p['official_player_id']];responses=[x for x in bundle['responses'] if x['receipt']['sha256']==game['source']['box_sha256']];assert len(responses)==1
    raw=base64.b64decode(responses[0]['body_base64'],validate=True);box=json.loads(raw);table=box['tables'][3];heads=[v['Text'] for v in table['headers'][0]['row']];rows=[]
    for r in table['rows']:
        cells=[x.get('Text','').strip() for x in r['row']]
        if cells[heads.index('선수명')]=='소형준': rows.append(cells)
    if len(rows)!=1:raise ValueError('official source pitcher name not unique')
    cells=rows[0];pitch_count=int(cells[heads.index('투구수')]);assert p['official_player_id']=='50030' and g['pitcher']['team']=='KT' and pitch_count==g['pitcher']['pitch_count'] and pitcher['outs']==18 and pitcher['kk']==g['pitcher']['strikeouts']==8
    yearurl='https://mykbostats.com/players/1990?year=2025';yearrec=idx.get(yearurl) or json.loads((EVIDENCE/'player-2025-receipt.json').read_text(encoding='utf8'));yearbody=read_verified(EVIDENCE/'player-2025.bin',yearrec);selected=BeautifulSoup(yearbody,'html.parser').select('select[name=year] option[selected]');assert len(selected)==1 and selected[0].get('value')=='2026'
    summary={'source_observations':receipt['receipts']+([] if yearurl in idx else [yearrec]),'official_source':{'event_id':'20261012KTSS02026','box_sha256':sha(raw),'box_received_at':responses[0]['receipt']['finished_at'],'history_bundle_sha256':ref['archived_canonical_bundle_sha256'],'archive_verification':'_verify_archives reconstructs source identity, roster and history; local clock only'},'cross_source_link':{'site_game_id':g['site_game_id'],'official_fixture_id':'20261012KTSS02026','link_basis':'same date, KT away, Samsung home, unique player official profile link and matching official pitching row; not proof of identity across other dates','official_player_id':p['official_player_id'],'site_player_id':p['site_player_id'],'pitch_count_official':pitch_count,'pitch_count_site':g['pitcher']['pitch_count'],'official_outs':pitcher['outs'],'official_strikeouts':pitcher['kk']},'site':{'season_rows':p['season_rows'],'pitching_table_row_counts':g['pitching_table_row_counts'],'game_log_2025_query':'?year=2025 returned selected year 2026; not verified'},'model':{'status':'not_fit','reason':'no verified dated roster availability and prior bullpen completeness joined to matching 2025/2026 odds and M0','prospective_training_eligible_joined_games':0,'scope':'this one investigated historical example only, not total existing population'},'limitations':['captured November 2026 after target game; not pregame observation','season totals are current snapshots not historical as-of records','no source availability or entitlement authorization inferred from status 200','not independent outcomes or ROI']};(HERE/'source-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps(summary['cross_source_link'],ensure_ascii=False))
if __name__=='__main__':main()
