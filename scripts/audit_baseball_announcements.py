"""Join timestamped announcements; record missing evidence rather than fill with finals."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
from collections import Counter,defaultdict
import argparse
import csv
import hashlib
import json

KST=timezone(timedelta(hours=9))


def before_game(observed,start):
    a=datetime.fromisoformat(observed.replace('Z','+00:00'))
    b=datetime.fromisoformat(start.replace('Z','+00:00'))
    if a.tzinfo is None:raise ValueError('observation requires timezone')
    if b.tzinfo is None:b=b.replace(tzinfo=KST)
    return a<b


def eligible_news(published,modified,start,lineup):
    if len(lineup)!=9 or len(set(lineup))!=9:return False,'incomplete_or_duplicate_lineup'
    if not before_game(published,start):return False,'published_after_start'
    if modified and not before_game(modified,start):return False,'modified_after_start'
    return True,'timestamped_report_not_immutable_capture'


def run(raw,prior,folder):
    target=[json.loads(s) for s in (prior/'targets.jsonl').read_text(encoding='utf8').splitlines()]
    announcement=raw/'info_watch/starter_announcements.csv'
    rows=list(csv.DictReader(announcement.open(encoding='utf-8-sig')))
    changes=[]
    changefile=raw/'info_watch/starter_changes.jsonl'
    if changefile.exists():changes=[json.loads(s) for s in changefile.read_text(encoding='utf8').splitlines()]
    groups=defaultdict(list);excluded=Counter()
    for r in rows:
        if not before_game(r['observed_at'],r['game_datetime']):excluded['not_observed_before_start']+=1;continue
        groups[(r['league'],r['game_datetime'][:10],r['home'],r['away'])].append(r)
    matched=[]
    for t in target:
        observations=groups[(t['league'],t['date'],t['home'],t['away'])]
        if not observations:continue
        latest={}
        for r in sorted(observations,key=lambda r:r['observed_at']):latest[r['field']]=r
        relevant=[r for r in changes if r.get('gameId') in {v['gameId'] for v in observations}
            and before_game(r['observed_at'],r.get('game_datetime') or observations[0]['game_datetime'])]
        for c in sorted(relevant,key=lambda r:r['observed_at']):
            latest[c['field']]=dict(c,value=c.get('value') or c.get('new_value'))
        matched.append(dict(target=t,observations=observations,changes=relevant,latest=latest,
            both_starters=all(k in latest and latest[k].get('value') for k in ['homeStarterName','awayStarterName']),
            lineup_evidence=False))
    report=dict(log_rows=len(rows),source_sha256=hashlib.sha256(announcement.read_bytes()).hexdigest(),
        changes_rows=len(changes),excluded=dict(excluded),matched_fixtures=dict(Counter(r['target']['league'] for r in matched)),
        both_starters=dict(Counter(r['target']['league'] for r in matched if r['both_starters'])),
        complete_lineup_and_starter_by_log=dict(KBO=0,NPB=0,MLB=0),
        reason='Starter log contains no batting-order observations. Do not attach postgame orders as pregame evidence.')
    (folder/'announcement-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    (folder/'matched-starter-observations.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in matched),encoding='utf8',newline='\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ['raw','prior','folder']:p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();run(a.raw,a.prior,a.folder)
