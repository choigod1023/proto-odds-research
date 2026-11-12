"""Parse captured official match-centre pages; fail closed on ambiguous identity."""
import hashlib,json,re,gzip
from pathlib import Path
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[1]
SAMPLES=[(2024,2,1),(2025,2,1),(2025,2,253),(2026,2,1),(2025,1,1)]

def parse(year,league,game):
    stem=f'{year}-{league}-{game}'
    bodies={}
    for kind in ('match','info'):
        body=(ROOT/'raw'/f'{stem}-{kind}.bin').read_bytes()
        meta=json.loads((ROOT/'raw'/f'{stem}-{kind}.json').read_text(encoding='utf8'))
        if hashlib.sha256(body).hexdigest()!=meta['sha256']: raise ValueError('hash mismatch')
        expected={'year':str(year),'meetSeq':str(league),'gameId':str(game)}
        if any(meta['params'].get(k)!=v for k,v in expected.items()): raise ValueError('request mismatch')
        bodies[kind]=body
    soup=BeautifulSoup(bodies['match'].decode('utf8'),'html.parser')
    for selector,expected in (('#year',str(year)),('#meetSeq',str(league)),('#gameId',str(game))):
        chosen=soup.select(f'{selector} option[selected]')
        if len(chosen)!=1 or chosen[0].get('value')!=expected: raise ValueError('page identity mismatch '+selector)
    d=json.loads(bodies['info'])
    if str(d.get('resultCode'))!='200' or d['data'].get('gameStatus')!='FE': raise ValueError('not finished')
    info=d['data']; teams=soup.select('.game-info > .team')
    if len(teams)!=2: raise ValueError('bad team count')
    team_ids=[]; team_names=[]
    for t in teams:
        ids=[c for c in t.get('class',[]) if re.fullmatch(r'K\d+',c)]
        if len(ids)!=1: raise ValueError('bad team ID')
        team_ids.append(ids[0]); team_names.append(t.select_one('p').get_text(strip=True))
    block=soup.select_one('.match-lineup-player .player')
    sides=block.find_all('div',recursive=False) if block else []
    if len(sides)!=2: raise ValueError('bad lineup sides')
    result=[]
    for index,side in enumerate(sides):
        boxes=side.find_all('div',class_='box',recursive=False)
        if len(boxes)!=2: raise ValueError('bad lineup boxes')
        groups=[]
        for box in boxes:
            people=[]
            for tr in box.select('tr[onclick]'):
                match=re.fullmatch(r"playerDetailPop\('([^']+)',\s*'([^']+)'\)",tr.get('onclick','').strip())
                if not match: continue
                team,pid=match.groups()
                if team!=team_ids[index] or not re.fullmatch(r'\d{8}',pid): raise ValueError('player identity mismatch')
                cells=tr.find_all('td',recursive=False)
                if len(cells)<3: raise ValueError('missing player cells')
                minute=tr.select_one('.changeM_'+pid); other=tr.select_one('.changeP_'+pid)
                people.append({'player_id':pid,'number':cells[0].get_text(strip=True),'position':cells[1].get_text(strip=True),'name':cells[2].get_text(strip=True),'substitution_minute_text':minute.get_text(strip=True) if minute else '', 'substitution_partner_name':other.get_text(strip=True) if other else ''})
            groups.append(people)
        ids=[p['player_id'] for g in groups for p in g]
        if len(groups[0])!=11 or len(ids)!=len(set(ids)): raise ValueError('incomplete or duplicate players')
        all_people=groups[0]+groups[1]
        for person in all_people:
            partner=person['substitution_partner_name']
            candidates=[p['player_id'] for p in all_people if p['name'].replace('(c)','').strip()==partner] if partner else []
            person['substitution_partner_id']=candidates[0] if len(candidates)==1 else None
        result.append({'team_id':team_ids[index],'team_name':team_names[index],'starters':groups[0],'bench':groups[1]})
    dt=soup.select_one('.team-versus .versus p')
    return {'official_game_id':f'kleague:{year}:{league}:{game}','kickoff_display':dt.get_text(' ',strip=True) if dt else None,'score':[info['homeGoal'],info['awayGoal']],'sides':result,'formation':None,'announcement_time':None,'evidence':'retrospective official match-centre page; receipt does not prove historical pregame availability'}

def main():
    games=[parse(*s) for s in SAMPLES]
    official=json.loads((REPO/'experiments/accuracy-connection-20261030/official-2025.json').read_text(encoding='utf8'))
    byid={r['official_game_id']:r for r in official}; matches=[]
    for g in games:
        r=byid.get(g['official_game_id'])
        if r:
            team_ok=[s['team_id'] for s in g['sides']]==[r['home_team_id'],r['away_team_id']]
            date_ok=r['kickoff_at'][:10].replace('-','/') in g['kickoff_display']
            matches.append({'game':g['official_game_id'],'team_ids_agree':team_ok,'kickoff_date_agree':date_ok,'score_agrees':g['score']==r['score']})
    schedule_matches=[]
    for year,month in ((2024,'03'),(2026,'02')):
        stem=f'{year}-2-schedule-{month}'
        body=(ROOT/'raw'/f'{stem}.bin').read_bytes()
        meta=json.loads((ROOT/'raw'/f'{stem}.json').read_text(encoding='utf8'))
        if hashlib.sha256(body).hexdigest()!=meta['sha256']: raise ValueError('schedule hash mismatch')
        rows=json.loads(body)['data']['scheduleList']
        rows=[r for r in rows if r['year']==year and r['leagueId']==2 and r['meetSeq']==2 and r['gameId']==1]
        if len(rows)!=1: raise ValueError('schedule identity ambiguous')
        r=rows[0]; g=next(g for g in games if g['official_game_id']==f'kleague:{year}:2:1')
        schedule_matches.append({'game':g['official_game_id'],'team_ids_agree':[s['team_id'] for s in g['sides']]==[r['homeTeam'],r['awayTeam']],'kickoff_date_agree':r['gameDate'].replace('.','/') in g['kickoff_display'],'score_agrees':g['score']==[r['homeGoal'],r['awayGoal']],'finished':r['endYn']=='Y' and r['gameStatus']=='FE'})
    target=REPO/'experiments/accuracy-review-20261030/inputs/targets.jsonl.gz'
    with gzip.open(target,'rt',encoding='utf8') as fh: targets=[json.loads(line) for line in fh]
    target_ids={r.get('event_id') for r in targets}
    summary={'samples':len(games),'k2_samples':sum(':2:' in g['official_game_id'] for g in games),'k1_control':sum(':1:' in g['official_game_id'] for g in games),'lineup_complete_both_sides':len(games),'source_player_ids':sum(len(s['starters'])+len(s['bench']) for g in games for s in g['sides']),'starting_players':sum(len(s['starters']) for g in games for s in g['sides']),'bench_players':sum(len(s['bench']) for g in games for s in g['sides']),'substitution_rows_with_partner_and_time':sum(bool(p['substitution_minute_text'] and p['substitution_partner_name']) for g in games for s in g['sides'] for p in s['starters']+s['bench']),'publication_timestamps':0,'formation_labels':0,'official_2025_result_matches':matches,'review_targets_matching_event_id':sum(g['official_game_id'] in target_ids for g in games)}
    summary['official_other_season_schedule_matches']=schedule_matches
    subs=[p for g in games for s in g['sides'] for p in s['starters']+s['bench'] if p['substitution_minute_text'] and p['substitution_partner_name']]
    summary['substitution_rows_partner_id_unique']=sum(bool(p['substitution_partner_id']) for p in subs)
    summary['substitution_rows_partner_id_unresolved']=sum(not p['substitution_partner_id'] for p in subs)
    summary['per_game']=[{'game':g['official_game_id'],'teams':[s['team_id'] for s in g['sides']],'starter_counts':[len(s['starters']) for s in g['sides']],'bench_counts':[len(s['bench']) for s in g['sides']],'substitution_rows':[sum(bool(p['substitution_minute_text'] and p['substitution_partner_name']) for p in s['starters']+s['bench']) for s in g['sides']]} for g in games]
    (ROOT/'parsed.json').write_text(json.dumps(games,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(summary,ensure_ascii=True,indent=2))
if __name__=='__main__': main()
