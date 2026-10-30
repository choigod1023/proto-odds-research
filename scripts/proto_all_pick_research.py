"""Offline uncapped singles over every quoted Proto selection, never a wallet.

Uses archived market-specific settlement LABELS, not inferred full-game scores.
No claim that archive quotes were buyable at a proven pregame timestamp.
Identical reissues collapse; any price/result/state conflict excludes the whole
physical market. Half-game families stay distinct. No overround quality gate.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
from decimal import Decimal
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from bets import SEL_NAMES
from matches import clean_team
from wisetoto import GameRow, VOID_RESULTS

STAKE = 10_000
LABELS = {
    ('승패', 2): {'홈승':0, '홈패':1},
    ('승무패', 3): {'홈승':0, '무승부':1, '홈패':2},
    ('핸디캡', 2): {'핸디승':0, '핸디패':1},
    ('핸디캡', 3): {'핸디승':0, '핸디무':1, '핸디패':2},
    ('언더오버', 2): {'언더':0, '오버':1},
    ('홀짝', 2): {'홀':0, '짝':1},
    ('승①패', 3): {'홈승':0, '①':1, '홈패':2},
    ('승⑤패', 3): {'홈승':0, '⑤':1, '홈패':2},
}
DATE = re.compile(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})')
LINE = re.compile(r'[-+]?\d+(?:\.\d+)?')


def stable_id(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',',':')).encode()).hexdigest()[:24]


def boolean(value):
    if value in (True, 1, 'True', 'true', '1'):
        return True
    if value in (False, 0, 'False', 'false', '0'):
        return False
    raise ValueError('invalid_void_flag')


def refund_label(family,n,sport,result):
    # Operator rule: baseball/basketball ordinary two-way draws pay 1.00.
    # Never turn a 3-way draw or handicap draw into a refund.
    return result in VOID_RESULTS or (family.removeprefix('전반')=='승패' and n==2
                                      and sport in ('bs','bk') and result=='무승부')


def parse_row(raw, source):
    """Parse shape before settlement; unconfirmed rows still invalidate reissues."""
    try:
        season, rnd = int(raw['year']), int(raw['round'])
        match = DATE.search(raw['date_text'])
        if not match:
            raise ValueError('invalid_date')
        month, day, hour, minute = map(int, match.groups())
        year = season - int(rnd == 1 and month == 12)
        kickoff = datetime(year,month,day,hour,minute).isoformat()
        n = int(raw['n_way'])
        if n not in (2,3):
            raise ValueError('invalid_selection_count')
        try:
            odds = tuple(float(x) for x in (str(raw['odds']).split(',') if not isinstance(raw['odds'],list) else raw['odds']))
        except (ValueError,TypeError):
            raise ValueError('invalid_odds')
        if len(odds) != n:
            raise ValueError('invalid_selection_count')
        if not all(math.isfinite(x) and x > 0 for x in odds):
            raise ValueError('invalid_odds')
        result = str(raw.get('result') or '').strip()
        void_flag = boolean(raw['is_void'])
        game = GameRow(season,rnd,str(raw['game_no']),raw['date_text'],raw['sport'],raw['league'],
                       str(raw.get('market_tag') or ''),str(raw.get('market_label') or ''),
                       raw['home'],raw['away'],'',list(odds),result,void_flag)
        family = game.market_family
        base = family.removeprefix('전반')
        if (base,n) not in LABELS or (family,n) not in SEL_NAMES:
            raise ValueError('unsupported_market')
        line = None
        if base in ('핸디캡','언더오버'):
            parsed_line = LINE.search(game.market_label)
            if not parsed_line:
                raise ValueError('missing_line')
            line = str(Decimal(parsed_line.group()).normalize())
        home, away = clean_team(raw['home']), clean_team(raw['away'])
        if not home or not away or home == away or not raw['league'] or not raw['sport']:
            raise ValueError('invalid_identity')
        physical = (kickoff,raw['sport'],raw['league'],home,away)
        key = (*physical,family,line,n)
        # A price of 1.00 may be a cancellation sentinel, never a buyable quote.
        refunded=refund_label(family,n,raw['sport'],result)
        if not all(x > 1 for x in odds):
            status = 'unpriced_void' if refunded else 'invalid_quoted_price'
        elif refunded:
            status = 'refund'
        elif void_flag:
            status = 'void_flag_without_cancellation_label'
        elif result in LABELS[(base,n)]:
            status = 'settled'
        else:
            status = 'unrecognized_or_unsettled_result'
        winner = LABELS[(base,n)].get(result) if status == 'settled' else None
        return dict(market_id=stable_id(key), event_id=stable_id(physical), key=list(key),
                    date=kickoff[:10], kickoff=kickoff, year=year, season=season, round=rnd,
                    game_no=str(raw['game_no']), sport=raw['sport'], league=raw['league'],
                    home=home, away=away, family=family, line=line, n_way=n,
                    odds=list(odds), result=result, winner=winner, status=status,
                    overround=math.fsum(1/o for o in odds),
                    names=list(SEL_NAMES[(family,n)]), source=source,
                    original_family=raw.get('market_family'),
                    offer_alias=[season,rnd,str(raw['game_no'])])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        reason = str(exc)
        raise ValueError(reason if reason in {'invalid_date','invalid_selection_count','invalid_odds',
            'unsupported_market','missing_line','invalid_identity','invalid_void_flag'} else 'invalid_row') from exc


def normalize(sources):
    groups, audit, raw_ids = {}, Counter(), set()
    rejected = []
    for source, raw in sources:
        audit['input_rows'] += 1
        try:
            row = parse_row(raw,source)
        except ValueError as exc:
            reason = str(exc)
            audit['shape_excluded:'+reason] += 1
            rejected.append(dict(source=source, season=raw.get('year'), round=raw.get('round'),
                                 game_no=raw.get('game_no'), reason=reason))
            continue
        audit['parsed_rows'] += 1
        audit['raw_status:'+row['status']] += 1
        if row['family'] != row['original_family']:
            audit['reclassified_family_rows'] += 1
        key = row['market_id']
        previous = groups.get(key)
        signature = (tuple(row['odds']),row['status'],row['winner'])
        if previous is None:
            row['aliases'] = [row.pop('offer_alias')]
            row['sources'] = [source]
            row['signature'] = signature
            row['conflict'] = False
            row['reissue_rows'] = 1
            groups[key] = row
        else:
            previous['reissue_rows'] += 1
            if row['offer_alias'] not in previous['aliases']:
                previous['aliases'].append(row['offer_alias'])
            if source not in previous['sources']:
                previous['sources'].append(source)
            if previous['signature'] != signature:
                previous['conflict'] = True
    offers = []
    for key, row in sorted(groups.items()):
        if row.pop('conflict'):
            reason = 'conflicting_price_result_or_state_reissues'
        elif row['status'] not in ('settled','refund'):
            reason = row['status']
        else:
            reason = None
        row.pop('signature')
        if reason:
            audit['market_excluded:'+reason] += 1
            rejected.append(dict(market_id=key,event_id=row['event_id'],aliases=row['aliases'],
                                 family=row['family'],reason=reason))
        else:
            offers.append(row)
    audit['physical_market_groups'] = len(groups)
    audit['usable_markets'] = len(offers)
    audit['distinct_physical_events'] = len({r['event_id'] for r in offers})
    audit['quoted_selections'] = sum(r['n_way'] for r in offers)
    audit['identical_reissue_rows_collapsed'] = sum(r['reissue_rows']-1 for r in offers)
    return offers, dict(sorted(audit.items())), rejected


def choices(offer):
    return [dict(market_id=offer['market_id'], event_id=offer['event_id'], date=offer['date'],
                 kickoff=offer['kickoff'],home=offer['home'],away=offer['away'],
                 issued_aliases=offer.get('aliases',[[offer['season'],offer['round'],offer['game_no']]]),
                 year=offer['year'], sport=offer['sport'],league=offer['league'], family=offer['family'],
                 line=offer['line'],sel_index=i,selection=name,odds=price,
                 result=offer['result'],refund=offer['status']=='refund',
                 hit=None if offer['status']=='refund' else i==offer['winner'],
                 net_won=0 if offer['status']=='refund' else int(Decimal(str(price))*STAKE)-STAKE if i==offer['winner'] else -STAKE)
            for i,(name,price) in enumerate(zip(offer['names'],offer['odds']))]


def read_source(path):
    if '.jsonl' in path.suffixes:
        with (gzip.open(path,'rt',encoding='utf8') if path.suffix=='.gz' else path.open(encoding='utf8')) as f:
            yield from (json.loads(line) for line in f if line.strip())
    else:
        with path.open(encoding='utf-8-sig',newline='') as f:
            yield from csv.DictReader(f)


def summarize(rows):
    daily, monthly = defaultdict(int), defaultdict(int)
    n = net = hits = refunds = 0
    dates = []
    for r in rows:
        n += 1; net += r['net_won']; hits += r.get('hit') is True; refunds += r.get('refund') is True
        daily[r['date']] += r['net_won']; monthly[r['date'][:7]] += r['net_won']; dates.append(r['date'])
    equity = peak = drawdown = 0
    for day in sorted(daily):
        equity += daily[day]; peak = max(peak,equity); drawdown = max(drawdown,peak-equity)
    return dict(n=n,net_won=net,stake_won=n*STAKE,roi=net/(n*STAKE) if n else None,
                hits=hits,refunds=refunds,hit_rate=hits/(n-refunds) if n>refunds else None,
                first=min(dates) if dates else None,last=max(dates) if dates else None,
                daily_close_drawdown_won=drawdown,monthly_net_won=dict(sorted(monthly.items())))


def price_band(price):
    for low, high in [(1,1.3),(1.3,1.5),(1.5,1.8),(1.8,2.2),(2.2,3),(3,5),(5,10),(10,math.inf)]:
        if low <= price < high:
            return f'[{low},{high})'
    raise ValueError('invalid price band')


def fixed_policy(offer, policy, options=None):
    options = choices(offer) if options is None else options
    if policy == 'all_choices':
        return options
    if policy == 'lowest_price':
        return [min(options,key=lambda r:(r['odds'],r['sel_index']))]
    if policy == 'highest_price':
        return [min(options,key=lambda r:(-r['odds'],r['sel_index']))]
    if policy == 'middle_3way':
        return [options[1]] if len(options)==3 else []
    raise ValueError('unknown policy')


def historical_summary(offers):
    groups, sides, cells = defaultdict(list), defaultdict(list), defaultdict(list)
    for offer in offers:
        options = choices(offer)
        family = f"{offer['family']}|{offer['n_way']}"
        for policy in ('all_choices','lowest_price','highest_price','middle_3way'):
            selected = fixed_policy(offer,policy,options)
            for scope in (f"year/{offer['year']}/{family}/{policy}",
                          f"league/{offer['league']}/{family}/{policy}",
                          f"league_year/{offer['league']}/{offer['year']}/{family}/{policy}"):
                groups[scope].extend(selected)
        for r in options:
            sides[(offer['year'],offer['family'],offer['n_way'],r['selection'])].append(r)
            cells[(r['league'],r['family'],offer['n_way'],r['selection'],price_band(r['odds']))].append(r)
    # Fixed retrospective screening rule: choose cells from 2024 ONLY, then
    # evaluate 2025 and later with at most one selection per product. No model p.
    approved = {}
    tested_cells = 0
    for key, rows in cells.items():
        train = summarize(r for r in rows if r['year']==2024)
        if train['n'] >= 100:
            tested_cells += 1
            if train['net_won'] > 0:approved[key] = train
    screened = defaultdict(list);screened_reference=defaultdict(list)
    for offer in offers:
        if offer['year'] < 2024:continue
        candidates=[]
        for r in choices(offer):
            key=(r['league'],r['family'],offer['n_way'],r['selection'],price_band(r['odds']))
            if key in approved:
                candidates.append((approved[key]['roi'],r))
        if candidates:
            chosen=min(candidates,key=lambda x:(-x[0],x[1]['sel_index']))[1]
            screened[str(chosen['year'])].append(chosen)
            screened_reference[str(chosen['year'])].extend(fixed_policy(offer,'lowest_price'))
    return dict(
        scope='archived product labels; all-choice totals are coverage diagnostics, not a recommended simultaneous portfolio',
        dates=[min(r['date'] for r in offers),max(r['date'] for r in offers)] if offers else [],
        groups={key:summarize(rows) for key,rows in sorted(groups.items()) if rows},
        selection_groups={'/'.join(map(str,key)):summarize(rows) for key,rows in sorted(sides.items())},
        train_2024_screen=dict(rule='league/family/nway/side/fixed_odds_bin: n>=100 and net>0 in 2024; at most one per product; previously seen archive, no multiplicity-corrected significance claim',
            training_cells_with_minimum_count=tested_cells,
            approved_cells=[dict(league=k[0],family=k[1],n_way=k[2],selection=k[3],odds_band=k[4],training=v) for k,v in sorted(approved.items())],
            by_year={key:summarize(rows) for key,rows in sorted(screened.items())},
            lowest_price_same_products_by_year={key:summarize(rows) for key,rows in sorted(screened_reference.items())}),
        operating_model_replayed=False,monthly_cap_applied=False,source_proven_buyability=False,
        periods={'training_reference':2023,'selection':2024,'reused_evaluation':2025,'later_partial_descriptive':2026})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',nargs='+',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    sources = ((str(path),row) for path in args.sources for row in read_source(path))
    offers,audit,rejected = normalize(sources)
    args.output.mkdir(parents=True,exist_ok=True)
    for name, rows in [('offers',offers),('excluded',rejected)]:
        payload=''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in rows).encode()
        (args.output/(name+'.jsonl.gz')).write_bytes(gzip.compress(payload,mtime=0))
    with (args.output/'historical-choices.jsonl.gz').open('wb') as binary:
        with gzip.GzipFile(fileobj=binary,mode='wb',mtime=0) as compressed:
            with io.TextIOWrapper(compressed,encoding='utf8') as stream:
                for offer in offers:
                    for choice in choices(offer):
                        stream.write(json.dumps(choice,ensure_ascii=False,separators=(',',':'))+'\n')
    report = dict(protocol='proto-all-pick-v1', monthly_cap_applied=False,stake_won=STAKE,
                  source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in args.sources},
                  audit=audit, source_proven_purchase_timestamps=False,
                  independently_officially_verified_results=False,
                  result_basis='archived product-specific settlement labels; independent official cross-check separate')
    (args.output/'normalization.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (args.output/'historical-summary.json').write_text(json.dumps(historical_summary(offers),ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(audit,ensure_ascii=False))


if __name__=='__main__':
    main()
