from collections import Counter
from pathlib import Path
import hashlib, json

ROOT=Path(__file__).resolve().parent
E=ROOT/'evidence'

def checked(label):
    body=(E/(label+'.body')).read_bytes()
    m=json.loads((E/(label+'.json')).read_text(encoding='utf-8'))
    if m['status']!=200 or hashlib.sha256(body).hexdigest()!=m['sha256']: raise ValueError('bad source hash or status')
    return json.loads(body),m

def validate(f,head,players):
    g=head['game']
    for k in ('gmkey','glkey','gameDate','gameStart','tcodeH','tcodeA','seasonCode','gameCode'):
        if str(g[k])!=str(f[k]): raise ValueError('fixture mismatch: '+k)
    if g['seasonName']!='2025-2026': raise ValueError('season mismatch')
    teams={f['tcodeH'],f['tcodeA']}; counts=Counter(); starters=Counter(); seen=set(); dnp=0; played=0; points=Counter()
    for r in players:
        player=r['player']; code=player['tcode']; pid=player['pcode']; rec=r['records']
        if code not in teams or not pid or pid in seen: raise ValueError('invalid player identity')
        seen.add(pid); counts[code]+=1
        if r['startFlag'] not in (0,1): raise ValueError('invalid starter flag')
        starters[code]+=r['startFlag']
        secs=int(rec['playMin'])*60+int(rec['playSec']); points[code]+=int(rec['score'])
        dnp+=secs==0; played+=secs>0
        if r['startFlag'] and secs==0: raise ValueError('starter without playing time')
    if any(starters[t]!=5 for t in teams): raise ValueError('not five starters per side')
    return {'players':len(players),'starters':sum(starters.values()),'bench_listed':len(players)-sum(starters.values()),'played':played,'dnp_zero_time':dnp,'points_by_team':dict(points)}

def main():
    c=json.loads((ROOT/'census.json').read_text(encoding='utf8'))
    fixtures={x['gmkey']:x for x in c['regular_season_fixtures']}
    orig,_=checked('postgame-schedule'); source={x['gmkey']:x for x in orig if x.get('glkey')=='S47G01'}
    out=[];missing=[]; invalid=[]
    for g in c['sample_ids']:
        labels=['postgame-'+g+'-header','postgame-'+g+'-player-stat']
        if not all((E/(label+'.body')).exists() and (E/(label+'.json')).exists() for label in labels): missing.append(g);continue
        try:
            h,hm=checked(labels[0]); p,pm=checked(labels[1]); stats=validate(fixtures[g],h,p)
            a=source[g]
            if stats['points_by_team']!={a['tcodeH']:a['scoreH'],a['tcodeA']:a['scoreA']}: raise ValueError('sum player points differs from schedule score')
            out.append({'gmkey':g,'date':a['gameDate'],'home':a['tcodeH'],'away':a['tcodeA'],'stats':stats,'header_sha256':hm['sha256'],'player_sha256':pm['sha256'],'header_received_at':hm['received_at'],'player_received_at':pm['received_at'],'availability':'postgame_retrospective_only'})
        except (ValueError,KeyError,TypeError) as exc: invalid.append({'gmkey':g,'reason':str(exc)})
    result={'scheduled_regular_season':len(fixtures),'sample_selected':len(c['sample_ids']),'sample_validated':len(out),'sample_missing':missing,'sample_invalid':invalid,'unselected_fixture_count':len(fixtures)-len(c['sample_ids']),'player_rows':sum(x['stats']['players'] for x in out),'starter_rows':sum(x['stats']['starters'] for x in out),'bench_listed_rows':sum(x['stats']['bench_listed'] for x in out),'played_rows':sum(x['stats']['played'] for x in out),'dnp_zero_time_rows':sum(x['stats']['dnp_zero_time'] for x in out),'strict_predecision_receipts':0,'games':out}
    (ROOT/'postgame-index.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps({k:v for k,v in result.items() if k!='games'},ensure_ascii=False))
if __name__=='__main__':main()
