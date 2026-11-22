import json, re, hashlib
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def read_props(path):
    html=path.read_text(encoding='utf-8')
    m=re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',html)
    if not m:
        raise ValueError('missing official NEXT_DATA')
    return json.loads(m.group(1))['props']['pageProps']

def extract_postgame(path):
    g=read_props(path)['game']
    if g['gameStatus'] != 3 or not re.fullmatch(r'00225\d{5}',g['gameId']):
        raise ValueError('not final 2025-26 regular season')
    teams=[]
    for side in ('awayTeam','homeTeam'):
        t=g[side]
        starters=[{'person_id':p['personId'],'name':p['firstName']+' '+p['familyName'],'position':p['position']} for p in t['players'] if p.get('position')]
        if len(starters)!=5 or len({p['person_id'] for p in starters})!=5:
            raise ValueError('invalid postgame starter five')
        teams.append({'side':side,'team_id':t['teamId'],'tricode':t['teamTricode'],'postgame_starters':starters})
    return {'game_id':g['gameId'],'game_code':g['gameCode'],'tipoff_utc':g['gameTimeUTC'],'game_status':g['gameStatusText'],'period':g['period'],'teams':teams,'namespace':'postgame_only','source_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def main():
    paths=sorted((ROOT/'raw').glob('box-*.html'))
    rows=[extract_postgame(p) for p in paths]
    if len({r['game_id'] for r in rows})!=len(rows): raise ValueError('duplicate game id')
    (ROOT/'postgame-samples.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print('postgame games:',len(rows),'starter pairs:',len(rows)*2)

if __name__=='__main__': main()
