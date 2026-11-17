"""Fetch public MLB schedule facts, excluding spring/exhibition and unfinished games.

Writes local research files only. No production connections or environment output.
"""
from collections import Counter
from pathlib import Path
import argparse
import json

import requests

TYPES = {'R','F','D','L','W'}


def official_fact(game, url):
    if game.get('gameType') not in TYPES or game.get('status',{}).get('abstractGameState') != 'Final':
        return None
    sides=game.get('teams',{})
    if any(type(sides.get(s,{}).get('score')) is not int for s in ['home','away']):
        return None
    return dict(league='MLB',gamePk=game['gamePk'],game_type=game['gameType'],start_at=game['gameDate'],
        home=sides['home']['team']['name'],away=sides['away']['team']['name'],
        home_score=sides['home']['score'],away_score=sides['away']['score'],official_url=url)


def fetch(output, years):
    session=requests.Session()
    games={}; ambiguous={}; skipped=Counter()
    for year in years:
        response=session.get('https://statsapi.mlb.com/api/v1/schedule',params=dict(sportId=1,
            startDate=f'{year}-01-01',endDate=f'{year}-12-31',gameTypes='R,F,D,L,W'),timeout=60)
        response.raise_for_status()
        for day in response.json()['dates']:
            for game in day['games']:
                fact=official_fact(game,response.url)
                if fact is None:
                    skipped[game.get('status',{}).get('detailedState','unknown')]+=1
                    continue
                old=games.get(fact['gamePk'])
                if old and old != fact:
                    ambiguous.setdefault(fact['gamePk'],[old]).append(fact)
                games[fact['gamePk']]=fact
    # Suspended games appear on original AND resumed dates with final scores.
    # Neither attach final scores to the original date nor double-count them.
    for game_id in ambiguous:del games[game_id]
    skipped['ambiguous_original_resumption_gamePk']=len(ambiguous)
    output.mkdir(parents=True,exist_ok=True)
    (output/'mlb-official-final.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n'
        for r in sorted(games.values(),key=lambda r:(r['start_at'],r['gamePk']))),encoding='utf8',newline='\n')
    (output/'mlb-fetch-audit.json').write_text(json.dumps(dict(skipped_missing_score=dict(skipped)),indent=2)+'\n',encoding='utf8',newline='\n')
    (output/'mlb-ambiguous-suspended.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n'
        for group in ambiguous.values() for r in group),encoding='utf8',newline='\n')
    print(json.dumps(dict(unique_finals=len(games),excluded=dict(skipped))))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--years',type=int,nargs='+',default=[2023,2024,2025,2026])
    args=p.parse_args();fetch(args.output,args.years)
