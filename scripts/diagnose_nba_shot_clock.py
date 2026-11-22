"""Describe zero-clock shots in team-games with an excess PBP 3PA."""
import argparse, collections, gzip, json, pathlib

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--archive',required=True); ap.add_argument('--audit',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    root=pathlib.Path(a.archive)/'summary'; counts=collections.Counter(); examples=[]
    with gzip.open(a.audit,'rt',encoding='utf8') as f:
        for line in f:
            row=json.loads(line); diff=row['mismatch'].get('threea',{})
            if diff.get('box') is None or diff['pbp']-diff['box']!=1: continue
            d=json.loads((root/(row['game_id']+'.json')).read_bytes()); shots=[x for x in d['plays'] if x.get('team',{}).get('id')==row['team_id'] and x.get('shootingPlay') and x.get('pointsAttempted')==3 and x.get('clock',{}).get('displayValue') in ('0:00','0.0','0:00.0')]
            counts['team_games_excess_one_3pa']+=1; counts['team_games_with_zero_clock_3pa']+=bool(shots); counts['zero_clock_3pa']+=len(shots); counts['zero_clock_makes']+=sum(x.get('scoringPlay') is True for x in shots)
            if shots and len(examples)<5: examples.append({'game_id':row['game_id'],'team':row['team'],'plays':[{k:x.get(k) for k in ('id','text','type','clock','scoreValue','scoringPlay','pointsAttempted','shortDescription')} for x in shots]})
    pathlib.Path(a.out).write_text(json.dumps({'counts':dict(counts),'examples':examples},indent=2)+'\n',encoding='utf8');print(dict(counts))
if __name__=='__main__': main()
