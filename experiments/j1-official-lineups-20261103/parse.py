from __future__ import annotations
import gzip, hashlib, json, re
from collections import defaultdict
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parent

def parse(raw,code,receipt,sha):
    soup=BeautifulSoup(raw,'html.parser')
    headers=[x for x in soup.select('.o-page-header__content') if x.select_one('a.o-page-header__club--home')]
    if len(headers)!=1: raise ValueError('header ambiguity')
    h=headers[0]; clubs=[]
    for side in ('home','away'):
        a=h.select_one('a.o-page-header__club--'+side)
        clubs.append({'id':a['href'].strip('/').split('/')[-1],'name':a.select_one('.o-page-header__club-name').get_text(' ',strip=True)})
    serialized=raw.decode('utf-8').replace('\\"','"')
    pattern=r'\{"name":"([^"]+)","position":"([^"]+)","image":"[^"]*","legacyPlayerPhotoLookup":\{"seasonYear":2025,"teamNameKey":"([^"]+)","playerId":"(\d+)"'
    lookups=defaultdict(set)
    for name,position,team,player_id in re.findall(pattern,serialized):
        lookups[(name,position,team)].add(player_id)
    date=h.select_one('p.o-page-header__date').get_text(' ',strip=True)
    scores=[int(x.get_text(strip=True)) for x in h.select('p.o-page-header__match-score') if x.get_text(strip=True).isdigit()]
    result={}
    for section in ('starting-members','reserve-members'):
        s=soup.select_one('section.p-game-details-lineup-tab__'+section)
        m=s.select_one('.m-lineup-list__members') if s else None
        if not m: raise ValueError('missing '+section)
        children=m.find_all(recursive=False)
        if len(children)%2 or section=='starting-members' and len(children)!=22: raise ValueError('invalid roster grid '+section)
        roster=[[],[]]
        for i,child in enumerate(children):
            item=child.select_one('.m-lineup-list-item')
            if item is None and 'm-lineup-list-item' in child.get('class',[]):item=child
            if item is None:continue
            name=item.select_one('.m-lineup-list-item__name');pos=item.select_one('.m-lineup-list-item__position')
            if not name or not pos:raise ValueError('invalid player')
            href=child.get('href','') if child.name=='a' else ''
            match=re.fullmatch(r'/player/(\d+)/',href)
            name=name.get_text(' ',strip=True);position=pos.get_text(' ',strip=True)
            possible=lookups[(name,position,clubs[i%2]['id'])]
            linked=match.group(1) if match else None
            if len(possible)>1 or (linked and possible and linked not in possible):
                raise ValueError('ambiguous official player identity')
            player_id=linked or (next(iter(possible)) if len(possible)==1 else None)
            roster[i%2].append({'name':name,'position_number':position,'official_player_id':player_id,'identity_source':'profile_link' if linked else 'serialized_roster_lookup' if player_id else 'unavailable'})
        result[section]=roster
    if [len(x) for x in result['starting-members']]!=[11,11]:raise ValueError('incomplete XI')
    out={'fixture_id':'jleague:j1:2025:'+code,'league':'J1','official_url_id':code,'clubs':clubs,'kickoff_display_actual':date,
      'starting_xi':result['starting-members'],'bench':result['reserve-members'],'formation_pregame':None,
      'official_document_sha256':sha,'received_at_utc':receipt,'availability':'retrospective_current_receipt_no_pregame_witness','strict_pit':False}
    label={'fixture_id':out['fixture_id'],'score_reported':scores if len(scores)==2 else None,'scope':'regular-season full match; verify 90-minute settlement before join','source_sha256':sha,'received_at_utc':receipt}
    return out,label

def main():
    selections=json.loads((ROOT/'selection.json').read_text(encoding='utf-8'))['selected_ids']
    logs=[json.loads(x) for x in (ROOT/'evidence/requests.jsonl').read_text(encoding='utf-8').splitlines()]
    rows=[];labels=[];failures=[]
    for code in selections:
        rec=next((x for x in reversed(logs) if x['kind']=='lineup' and x['key']==code and x.get('status')==200),None)
        if not rec:failures.append({'id':code,'reason':'no successful response'});continue
        raw=gzip.decompress((ROOT/'evidence'/rec['body_file']).read_bytes())
        if hashlib.sha256(raw).hexdigest()!=rec['body_sha256']:raise ValueError('source hash mismatch '+code)
        try:
            a,b=parse(raw,code,rec['received_at_utc'],rec['body_sha256']);rows.append(a);labels.append(b)
        except (ValueError,AttributeError,TypeError) as e: failures.append({'id':code,'reason':str(e)})
    for filename,records in [('lineups.jsonl.gz',rows),('postgame-labels.jsonl.gz',labels)]:
        payload=''.join(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n' for x in records).encode()
        (ROOT/filename).write_bytes(gzip.compress(payload,mtime=0))
    (ROOT/'parse-failures.json').write_text(json.dumps(failures,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print('selected',len(selections),'parsed',len(rows),'failed',len(failures),'starters missing id',sum(p['official_player_id'] is None for r in rows for team in r['starting_xi'] for p in team))
if __name__=='__main__':main()
