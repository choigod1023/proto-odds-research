"""Offline, one-game KBO evidence audit; never fetches or evaluates a model."""
import argparse
import csv
import hashlib
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path


def timestamp(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        raise ValueError('invalid timestamp')
    if dt.utcoffset() is None:
        raise ValueError('timezone required')
    return dt


def evidence_tier(received, cutoff, kickoff, published=None, modified=None):
    receipt, decision = timestamp(received), timestamp(cutoff)
    start = timestamp(kickoff)
    pub, mod = timestamp(published), timestamp(modified)
    if not receipt or not decision or not start:
        raise ValueError('receipt and cutoff required')
    if receipt <= decision and (not pub or pub <= receipt) and (not mod or mod <= receipt):
        return 'prospective_received_before'
    if pub and pub <= decision and pub <= receipt and (not mod or mod <= decision):
        return 'historical_claimed_before'
    return 'postgame_only' if receipt >= start else 'late_before_kickoff' if receipt > decision else 'unavailable'


def audit(raw_bytes, rows, spec):
    data = json.loads(raw_bytes)['data']
    game = data['gameScore']
    kick, decision = timestamp(spec['kickoff']), timestamp(spec['decision'])
    if kick.utcoffset() != timedelta(hours=9):
        raise ValueError('KBO kickoff must use KST')
    if decision > kick - timedelta(minutes=30):
        raise ValueError('decision after T-30')
    date = kick.strftime('%Y%m%d')
    if str(game['gameDate']) != date or game['gmKey'] != spec['game_id']:
        raise ValueError('game date or ID mismatch')
    if (game['homeKey'], game['visitKey']) != (spec['home_code'], spec['away_code']):
        raise ValueError('team identity mismatch')
    if game['gtime'] != kick.strftime('%H:%M'):
        raise ValueError('kickoff mismatch')
    # Explicit crosswalk supplied by researcher; scores never establish identity.
    key = (str(kick.year), spec['round'], spec['game_no'])
    matches = [r for r in rows if (r['year'], r['round'], r['game_no']) == key]
    if len(matches) != 1:
        raise ValueError('missing or ambiguous market row')
    row = matches[0]
    if not (row['league']=='KBO' and row['sport']=='bs' and row['market_family']=='승패' and row['market_tag']=='hm' and row['market_label']=='' and row['n_way']=='2' and row['date_text'][:5]==kick.strftime('%m.%d') and row['date_text'][-5:]==kick.strftime('%H:%M')):
        raise ValueError('different market or date')
    if not (re.fullmatch(re.escape(spec['home_name']) + r' \d+', row['home']) and re.fullmatch(r'\d+ ' + re.escape(spec['away_name']), row['away'])):
        raise ValueError('team crosswalk mismatch')
    prices = [float(x) for x in row['odds'].split(',')]
    if len(prices)!=2 or any(not math.isfinite(p) or p<=1 for p in prices):
        raise ValueError('incomplete prices')
    source_home = int(row['home'].rsplit(' ',1)[1]); source_away = int(row['away'].split(' ',1)[0])
    official_result_link = bool(str(game.get('endFlag'))=='3' and (game.get('hscore'),game.get('vscore'))==(source_home,source_away))
    tier = evidence_tier(spec['received_at'], spec['decision'], spec['kickoff'], spec.get('published_at'), spec.get('modified_at'))
    price_receipt = timestamp(spec.get('price_received_at'))
    before_price = bool(price_receipt and price_receipt <= decision)
    # No lineup confirmation can be inferred from retrospective response content.
    return {'game_id':game['gmKey'],'market_key':list(key),'league':'KBO','market':'full_game_two_way','odds':prices,'kickoff':spec['kickoff'],'hypothetical_cutoff':spec['decision'],'raw_sha256':hashlib.sha256(raw_bytes).hexdigest(),'received_at':spec['received_at'],'published_at':spec.get('published_at'),'modified_at':spec.get('modified_at'),'archive_witness':None,'evidence_tier':tier,'lineup_pregame_confirmed':False,'price_received_before':before_price,'team_official_result_linked':official_result_link,'eligible':False,'reasons':['lineup_predecision_not_verified'] + ([] if before_price else ['price_receipt_unverified']) + ([] if official_result_link else ['official_result_unmatched'])}


def main():
    p=argparse.ArgumentParser(); p.add_argument('--response',required=True); p.add_argument('--markets',required=True); p.add_argument('--spec',required=True); p.add_argument('--output',required=True); a=p.parse_args()
    spec=json.loads(Path(a.spec).read_text(encoding='utf-8-sig'))
    with open(a.markets,encoding='utf-8-sig',newline='') as f: result=audit(Path(a.response).read_bytes(),csv.DictReader(f),spec)
    Path(a.output).write_text(json.dumps(result,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))

if __name__ == '__main__': main()
