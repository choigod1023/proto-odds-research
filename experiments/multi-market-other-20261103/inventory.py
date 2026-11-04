from pathlib import Path
import csv, gzip, hashlib, json, math, re
from collections import Counter, defaultdict
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RAW = ROOT / 'data/processed/games.csv.tmp'
DOM = ROOT / 'experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz'
FAMILIES = {'승패': 'win_loss', '승①패': 'baseball_win_one_loss', '핸디캡': 'handicap', '언더오버': 'total'}

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def date_of(r):
    m = re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})', r['date_text'])
    if not m: return None
    mo, day, hour, minute = map(int, m.groups())
    try: return datetime(int(r['year']) - (int(r['round']) == 1 and mo == 12), mo, day, hour, minute).isoformat()
    except ValueError: return None

def summarize(rows, volleyball=False):
    labels, results, odds, dates = Counter(), Counter(), [], []
    valid_odds = valid_line = valid_result = void = 0
    identities = Counter()
    for r in rows:
        line = r['market_label'].strip()
        labels[line or '(missing)'] += 1
        results[r['result'].strip() or '(missing)'] += 1
        if re.fullmatch(r'[HU] [+-]?\d+(?:\.\d+)?', line): valid_line += 1
        try:
            prices = [float(v) for v in r['odds'].split(',')]
            ok = len(prices) == int(r['n_way']) and all(math.isfinite(v) and v > 1 for v in prices)
        except (ValueError, TypeError): ok = False
        if ok: valid_odds += 1; odds.extend(prices)
        is_void = r['is_void'].lower() in ('true','1')
        void += is_void
        if not is_void and r['result'].strip() not in ('', '취소', '적중특례'): valid_result += 1
        date = date_of(r)
        if date: dates.append(date)
        identities[(r['year'],r['round'],r['game_no'])] += 1
    return {'rows':len(rows), 'unique_round_game_keys':len(identities), 'duplicate_key_rows':sum(n-1 for n in identities.values()),
        'date_range':[min(dates), max(dates)] if dates else None, 'date_parse_missing':len(rows)-len(dates),
        'line_present_numeric':valid_line, 'line_labels':dict(sorted(labels.items())),
        'valid_price_vector':valid_odds, 'price_range':[min(odds), max(odds)] if odds else None,
        'result_nonvoid_nonempty':valid_result, 'void_flag':void, 'results':dict(sorted(results.items())),
        'pregame_price_receipt_evidence':0, 'pregame_receipt_missing':len(rows),
        'volleyball_unit': 'unverified; no set/point classification inferred' if volleyball else None}

def run():
    groups=defaultdict(list)
    with RAW.open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            if r['sport'] not in ('bs','bk','vl') or r['market_family'] not in FAMILIES: continue
            if r['sport']=='bs' and r['league'] not in ('NPB','MLB'): continue
            if r['sport']=='bk' and r['league'] not in ('KBL','NBA'): continue
            groups[(r['sport'], r['league'], FAMILIES[r['market_family']])].append(r)
    raw={'/'.join(k):summarize(v,k[0]=='vl' and k[2] in ('handicap','total')) for k,v in sorted(groups.items())}
    domestic=defaultdict(list)
    with gzip.open(DOM,'rt',encoding='utf-8') as f:
        for line in f:
            r=json.loads(line); s=r['sport']; league=r['league']
            if s=='vl' or (s=='bs' and league in ('NPB','MLB')) or (s=='bk' and league in ('KBL','NBA')):
                domestic[(s,league)].append(r)
    domestic_out={}
    for key, rows in sorted(domestic.items()):
        domestic_out['/'.join(key)]={'rows':len(rows),'date_range':[min(r['day'] for r in rows),max(r['day'] for r in rows)],
            'prices_complete':sum(len(r['odds'])==2 and all(isinstance(x,(int,float)) and x>1 for x in r['odds']) for r in rows),
            'settlement_label_present':sum(r.get('y') in (0,1) for r in rows),'pregame_receipt_missing':len(rows)}
    out={'inputs':{'raw':{'path':str(RAW.relative_to(ROOT)).replace('\\','/'),'sha256':digest(RAW)},'domestic':{'path':str(DOM.relative_to(ROOT)).replace('\\','/'),'sha256':digest(DOM)}},'raw':raw,'domestic_primary_only':domestic_out,
         'notes':['raw rows are offers, not deduplicated fixtures; unique key counts are diagnostic only', 'source receipt timing and market unit are not certified', 'domestic is already primary-only and cannot inventory non-primary markets']}
    (HERE/'results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf8')
    print('raw groups',len(raw),'raw rows',sum(x['rows'] for x in raw.values()),'domestic rows',sum(x['rows'] for x in domestic_out.values()))
if __name__=='__main__': run()
