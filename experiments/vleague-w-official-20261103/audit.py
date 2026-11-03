"""Audit saved KOVO bytes without using target result or later-game data as features."""
from collections import Counter
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parent))
import collect
ROOT=Path(__file__).resolve().parent

def lineup_rows(payload, fixture):
    lineup=payload['lineup']
    assert lineup['rset']==1 and lineup['gpart']=='201'
    out=[]
    for prefix,side in [('hp','home'),('ap','away')]:
        team=fixture[side+'Team']['value']
        for n in range(1,9):
            name=lineup.get(prefix+str(n))
            if not name: continue
            candidates=[p for p in payload.get('player',[]) if p.get('season')=='022' and p.get('gpart')=='201' and str(p.get('gnum'))==str(fixture['number']) and p.get('tcode')==team and p.get('pname')==name and p.get('pcode')]
            unique={(p['pcode'],p.get('position')) for p in candidates}
            player_id,position=next(iter(unique)) if len(unique)==1 else (None,None)
            out.append({'side':side,'slot':n,'slot_kind':'court_six' if n<=6 else 'source_libero_slot', 'name':name,'player_id':player_id,'position':position,'identity_match_count':len(unique)})
    return out

def run():
    games=collect.census();events=collect.load(collect.MANIFEST)['games'];stat=collect.load(collect.EVIDENCE/'stat_schedule.httpbody')['payload']['content']; stats={int(x['gnum']):x for x in stat}; assert len(stats)==126
    counts=Counter();out=[];issues=[]
    for g in games:
        id=str(g['number']);counts['scheduled']+=1
        if id not in events:issues.append({'game_id':id,'reason':'not_captured'});continue
        e=events[id];body=(ROOT/e['file']).read_bytes();assert collect.sha(body)==e['sha256'];p=collect.validate(body,g); rows=lineup_rows(p,g);s=stats[int(id)];
        assert s['gender']=='2' and s['gpart']=='201' and s['hcode']==g['homeTeam']['value'] and s['acode']==g['awayTeam']['value'] and s['gdate']==g['gameDate']
        pairs=[[s[f'hs{n}point'],s[f'as{n}point']] for n in range(1,6)]; played=[pair for pair in pairs if pair!=[0,0]]
        issues_here=[]
        if len(played) not in (3,4,5):issues_here.append('invalid_set_count')
        if sum(a>b for a,b in played)!=g['homeTeam']['score'] or sum(a<b for a,b in played)!=g['awayTeam']['score']:issues_here.append('set_result_mismatch')
        for n,(a,b) in enumerate(played,1):
            if max(a,b)<(15 if n==5 else 25) or abs(a-b)!=2 and min(a,b)>=(14 if n==5 else 24) or abs(a-b)<2: issues_here.append('invalid_set_score')
        for side in ('home','away'):
            court=[r for r in rows if r['side']==side and r['slot_kind']=='court_six'];lib=[r for r in rows if r['side']==side and r['slot_kind']=='source_libero_slot']
            if len(court)!=6:issues_here.append(side+'_court_count')
            if len({r['player_id'] for r in court if r['player_id']})!=6:issues_here.append(side+'_court_identity')
            if sum(r['position']=='S' for r in court)!=1:issues_here.append(side+'_setter')
            if not lib or any(not r['player_id'] or r['position']!='L' for r in lib):issues_here.append(side+'_libero_identity')
        counts['captured']+=1;counts['twelve_ids']+=int(all(not e.endswith(('_court_count','_court_identity')) for e in issues_here));counts['two_setters']+=int(all(not e.endswith('_setter') for e in issues_here));counts['two_libero_sides']+=int(all(not e.endswith('_libero_identity') for e in issues_here));counts['set_scores_valid']+=int(not any(x in issues_here for x in ('invalid_set_count','invalid_set_score','set_result_mismatch')))
        if any(p.get('gpart')!='201' for p in p.get('player',[])):counts['foreign_gpart_player_in_raw']+=1
        if any(p.get('gpart')!='201' for p in p.get('team',[])):counts['foreign_gpart_team_in_raw']+=1
        if any((r.get('gdate') or '')>g['gameDate'] for r in p.get('recentGames',[])):counts['future_recentGames_in_raw']+=1
        for i in issues_here:issues.append({'game_id':id,'reason':i})
        out.append({'namespace':'postgame_not_pit','match':'001','season':'022','league':'201','gender':'FEMALE','game_id':id,'date':g['gameDate'],'home_id':g['homeTeam']['value'],'away_id':g['awayTeam']['value'],'official_first_set_lineup':rows,'postgame_sets':played,'postgame_set_result':[g['homeTeam']['score'],g['awayTeam']['score']],'source_sha256':e['sha256'],'received_at':e['received_at'],'strict_pit':False,'issues':issues_here})
    (ROOT/'postgame').mkdir(exist_ok=True)
    (ROOT/'postgame'/'games.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in out),encoding='utf8',newline='\n')
    summary={'counts':dict(counts),'uncaptured':len(games)-counts['captured'],'strict_pit_authenticated':0,'issues':issues}
    (ROOT/'audit-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({'counts':dict(counts),'uncaptured':summary['uncaptured'],'issue_count':len(issues)},ensure_ascii=False))
if __name__=='__main__':run()
