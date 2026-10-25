"""Read-only official result reconciliation; never changes frozen ticket choices."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import requests
from compare_matched_tipsters import compare
from matched_tipster_source import stamp

API = 'https://statsapi.mlb.com/api/v1'


def reconcile(rows, teams, schedule):
    abbreviations = {t['id']: ('ARI' if t['abbreviation']=='AZ' else t['abbreviation']) for t in teams['teams']}
    index = {}
    for day in schedule['dates']:
        for game in day['games']:
            key = (game.get('officialDate',day['date']), abbreviations[game['teams']['home']['team']['id']],
                   abbreviations[game['teams']['away']['team']['id']])
            index.setdefault(key,[]).append(game)
    output, audit = [], Counter()
    for original in rows:
        row = dict(original)
        candidates = index.get((row['date'],row['home'],row['away']),[])
        if len(candidates)>1:
            candidates = [g for g in candidates if abs((stamp(g['gameDate'])-stamp(row['kickoff'])).total_seconds())<=600]
        if len(candidates)!=1:
            raise ValueError(f"Official event missing/ambiguous: {row['id']}")
        game = candidates[0]
        home, away = game['teams']['home'].get('score'), game['teams']['away'].get('score')
        if game['status']['abstractGameState']!='Final' or type(home) is not int or type(away) is not int or home==away:
            raise ValueError(f"Official event not decisively final: {row['id']}")
        y = 0 if home>away else 1
        audit['newly_resolved' if row['y'] is None else 'agreed' if row['y']==y else 'conflicted'] += 1
        row['publisher_y'] = row['y'];row['y'] = y
        row['official'] = dict(game_pk=game['gamePk'],home_score=home,away_score=away,
              game_date=game['gameDate'],state=game['status']['abstractGameState'],
              kickoff_delta_seconds=(stamp(game['gameDate'])-stamp(row['kickoff'])).total_seconds(),
              source_url=f"{API}/schedule?gamePk={game['gamePk']}")
        row['result_source']='mlb_official_schedule'
        # The lookup proves final outcome, NOT historical availability/settlement time.
        output.append(row)
    return output,dict(audit)


def final_tickets(previous, rows):
    byid = {r['id']:r for r in rows}
    output = {}
    for weight,arm in previous.items():
        output[weight]={}
        for policy,p in arm['policies'].items():
            output[weight][policy]={}
            for weekly,simulation in p['paper'].items():
                tickets=[];monthly=Counter()
                for old in simulation['ticket_log']:
                    if len(old['ids'])!=2 or len(set(old['ids']))!=2:
                        raise ValueError('Invalid frozen pair')
                    won=all(byid[i]['y']==s for i,s in zip(old['ids'],old['sides']))
                    profit=int(Decimal(str(old['price']))*10000)-10000 if won else -10000
                    monthly[old['month']]+=profit
                    tickets.append(dict(at=old['at'],ids=old['ids'],sides=old['sides'],price=old['price'],
                                        won=won,net_krw=profit))
                net=sum(t['net_krw'] for t in tickets)
                output[weight][policy][weekly]=dict(tickets=len(tickets),wins=sum(t['won'] for t in tickets),
                    net_krw=net,roi_pct=100*net/(10000*len(tickets)) if tickets else None,
                    monthly_final_net=dict(monthly),ticket_log=tickets,
                    note='Final official settlement of unchanged previously selected tickets; not a replay with known historical settlement times.')
    return output


def run(source, destination):
    report=json.loads(source.read_text(encoding='utf8'));rows=report['opportunity_ledger']
    session=requests.Session(); responses={}
    for name,url,params in [('teams',API+'/teams',{'sportId':1}),
            ('schedule',API+'/schedule',dict(sportId=1,startDate=min(r['date'] for r in rows),endDate=max(r['date'] for r in rows)))]:
        response=session.get(url,params=params,timeout=30);response.raise_for_status()
        responses[name]=dict(url=response.url,sha256=hashlib.sha256(response.content).hexdigest(),data=response.json())
    official,audit=reconcile(rows,responses['teams']['data'],responses['schedule']['data'])
    result=dict(status='official_result_reconciliation_not_model_promotion',
       observed_at_utc=datetime.now(timezone.utc).isoformat(),input_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
       code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),sources=responses,audit=audit,
       mlb_one_author=compare(official),mlb_three_authors=compare([r for r in official if r['authors']>=3]),
       frozen_tickets_one_author=final_tickets(report['mlb_one_author'],official),
       frozen_tickets_three_authors=final_tickets(report['mlb_three_authors'],official),opportunity_ledger=official,
       limitations=['Official outcome verification does not validate model probabilities, quoted odds availability, or vote calibration.',
          'Six weights on already explored five dates; no held-out optimization or reliable monthly income estimate.',
          'Ticket choices and monthly-loss gate remain frozen from original replay; final settlement is not historical settlement timing.',
          'Domestic backfill articles remain outside this official MLB reconciliation.'])
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(audit))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--input',type=Path,required=True);ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args();run(a.input,a.output)
