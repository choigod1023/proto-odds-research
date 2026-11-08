"""Reproduce retrospective Betman market favorite census from hash-verified archives."""
import argparse, collections, datetime as dt, gzip, hashlib, json, math
from pathlib import Path

OUT = Path(__file__).resolve().parent
MANIFEST = Path('C:/Users/user/Documents/Codex/market-outcomes-validation/experiments/pregame-proto-prices-20261104/manifest-v2.json')
# Explicit IDs: full-game general, total and handicap; never infer scope from handi alone.
GENERAL = {('SC','1','1'), ('BS','2','2'), ('BK','3','2')}
PRODUCTS = {('SC','78','7'):'total', ('BS','79','7'):'total', ('BK','80','7'):'total', ('SC','5','4'):'handicap', ('BS','7','5'):'handicap', ('BK','6','5'):'handicap'}
KST = dt.timezone(dt.timedelta(hours=9))

def ident(r):
    return (r.get('itemCode'), str(r.get('betId')), str(r.get('betTypId')))

def fixture(r):
    fields = [r.get(k) for k in ('itemCode','leagueCode','gameDate','homeId','awayId')]
    return tuple(map(str, fields)) if all(x is not None and x != '' for x in fields) else None

def number(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError): return None

def metrics(rows):
    rows = sorted(rows, key=lambda r:(r['kickoff'],r['fixture'],r['round'],r['matchSeq']))
    n=len(rows); wins=sum(r['win'] for r in rows); profit=0.; peak=0.; dd=0.
    for r in rows:
        profit += r['price']-1 if r['win'] else -1
        peak=max(peak,profit); dd=max(dd,peak-profit)
    return dict(n=n,wins=wins,refunds=0,hit_rate=wins/n if n else None,net_units=round(profit,4),roi=profit/n if n else None,max_drawdown=round(dd,4),fixtures=len(set(r['fixture'] for r in rows)),kickoff_min=rows[0]['kickoff'] if n else None,kickoff_max=rows[-1]['kickoff'] if n else None)

def evaluate(r, general, kind):
    if len(general)!=1: return None,'general_not_unique'
    g=general[0]
    if str(r.get('protoStatus'))!='4' or str(g.get('protoStatus'))!='4' or r.get('gameReject')!='0' or g.get('gameReject')!='0': return None,'not_final_or_rejected'
    if r.get('gameSubject') not in (None,''): return None,'conditional_subject'
    try:
        score=str(g['mchScore']).split(':'); assert len(score)==2 and all(x.isdigit() for x in score)
        h,a=map(int,score)
    except (KeyError,AssertionError): return None,'general_score_invalid'
    if h == a and r['itemCode'] == 'BK': return None,'basketball_general_draw'
    general_result = 0 if h > a else 2 if h < a else 4 if r['itemCode'] == 'BS' else 1
    if str(g.get('gameResult')) != str(general_result): return None,'general_result_code_mismatch'
    line=number(r.get('winHandi')); other=number(r.get('loseHandi'))
    if line is None or other is None or line*2 != int(line*2): return None,'invalid_or_quarter_line'
    draw=number(r.get('drawAllot'))
    if kind=='total':
        if line < 0 or line == int(line) or line != other or draw != 0: return None,'unsupported_total_terms'
        result=0 if h+a<line else 2
        labels=[0,2]
    else:
        if line != -other: return None,'handicap_line_mismatch'
        is_three=draw is not None and draw>1
        if is_three and line != int(line): return None,'unsupported_threeway_line'
        if not is_three and (r['itemCode'] not in ('BS','BK') or line==int(line) or draw != 0): return None,'unsupported_twoway_line'
        val=h-a+line
        result=0 if val>0 else 2 if val<0 else 1
        labels=[0,1,2] if is_three else [0,2]
    if str(r.get('gameResult')) != str(result): return None,'result_code_mismatch'
    prices=[number(r.get(x)) for x in ('winAllot','drawAllot','loseAllot')]
    if any(prices[i] is None or prices[i]<=1 for i in labels): return None,'invalid_prices'
    minprice=min(prices[i] for i in labels)
    if sum(prices[i]==minprice for i in labels)!=1: return None,'favorite_tie'
    pick=next(i for i in labels if prices[i]==minprice)
    return dict(line=line,score=[h,a],result=result,pick=pick,price=prices[pick],prices=prices,win=pick==result, general_matchSeq=g['matchSeq'],general_result=g.get('gameResult'),general_status=g.get('protoStatus'),product_status=r.get('protoStatus'),product_score=r.get('mchScore'),betId=r.get('betId'),betTypId=r.get('betTypId'),handi=r.get('handi')),None

def conflicting_sequences(rows):
    by_seq=collections.defaultdict(set)
    for row in rows:
        by_seq[str(row.get('matchSeq'))].add(json.dumps(row,sort_keys=True,ensure_ascii=False))
    return {seq for seq, variants in by_seq.items() if len(variants)>1}

def main(manifest_path=MANIFEST):
    manifest_path = Path(manifest_path)
    manifest=json.loads(manifest_path.read_text(encoding='utf8')); counts=collections.Counter(); candidates=collections.defaultdict(list); sourceproof=[]
    for source in manifest['sources']:
        path=Path(manifest['source_roots'][source['source_root_index']])/source['body_file']; raw=gzip.decompress(path.read_bytes()); sha=hashlib.sha256(raw).hexdigest()
        if sha!=source['body_sha256']: raise ValueError('hash mismatch '+str(path))
        sourceproof.append(dict(round=source['round'],path=str(path),sha256=sha))
        d=json.loads(raw)['compSchedules']; rows=[dict(zip(d['keys'],v)) for v in d['datas']]; generals=collections.defaultdict(list)
        # A round + matchSeq must identify one physical product globally.
        conflicts=conflicting_sequences(rows)
        seen=set(); unique=[]
        for r in rows:
            fingerprint=json.dumps(r,sort_keys=True,ensure_ascii=False)
            if fingerprint in seen:
                counts['exact_duplicate_raw_rows']+=1
                continue
            seen.add(fingerprint); unique.append(r)
        rows=unique
        for r in rows:
            if ident(r) in GENERAL and str(r.get('matchSeq')) not in conflicts: generals[fixture(r)].append(r)
        for r in rows:
            sport=r.get('itemCode'); legacy=not r.get('betId') or not r.get('betTypId'); era='legacy' if legacy else 'modern'
            counts[f'{era}|{sport}|all_raw_rows']+=1
            if legacy:
                if r.get('handi') in (2,9,12,18,19,23): counts[f'{era}|{sport}|potential_market_scope_unproven']+=1
                continue
            kind=PRODUCTS.get(ident(r))
            if not kind:
                counts[f'{era}|{sport}|other_or_partial_product']+=1; continue
            counts[f'{era}|{sport}|{kind}|candidate']+=1
            key=fixture(r)
            if not key: counts[f'{era}|{sport}|{kind}|missing_fixture_identity']+=1; continue
            candidates[(key,kind)].append((int(source['round']),int(r['matchSeq']),r,generals.get(key,[]),source,str(r.get('matchSeq')) in conflicts))
    primary=[]; allrows=[]
    conflicts=set()
    for (key,kind), products in candidates.items():
        scores={str(g[0].get('mchScore')) for _,_,_,g,_,_ in products if len(g)==1 and str(g[0].get('protoStatus'))=='4' and g[0].get('gameReject')=='0' and g[0].get('mchScore')}
        if len(scores)>1: conflicts.add(key)
    for (key,kind), products in sorted(candidates.items()):
        products.sort(key=lambda x:(x[0],x[1]))
        for idx,(rnd,seq,r,g,s,duplicate_conflict) in enumerate(products):
            values,reason=(None,'conflicting_product_identity') if duplicate_conflict else (None,'conflicting_general_scores') if key in conflicts else evaluate(r,g,kind)
            prefix=f"modern|{r['itemCode']}|{kind}|"
            counts[prefix+('excluded_'+reason if reason else 'eligible_product')]+=1
            if idx==0: counts[prefix+('representative_excluded_'+reason if reason else 'representative_eligible')]+=1
            if values is None: continue
            date=dt.datetime.fromtimestamp(r['gameDate']/1000,KST).isoformat()
            row=dict(fixture='|'.join(key),sport=r['itemCode'],league=r.get('leagueName') or r.get('leagueCode'),leagueCode=r.get('leagueCode'),kickoff=date,market=kind,period='regulation' if r['itemCode']=='SC' else 'full_including_extra',round=rnd,matchSeq=seq,source_sha256=s['body_sha256'],**values)
            allrows.append(row)
            if idx==0: primary.append(row)
    def breakdown(rows):
        groups=collections.defaultdict(list)
        for r in rows:
            for name,key in [('overall','all'),('sport_market',r['sport']+'|'+r['market']),('league_market',r['sport']+'|'+r['leagueCode']+'|'+r['league']+'|'+r['market']),('month_market',r['sport']+'|'+r['market']+'|'+r['kickoff'][:7]),('year_market',r['sport']+'|'+r['market']+'|'+r['kickoff'][:4])]: groups[(name,key)].append(r)
        return {name:{k:metrics(v) for (n,k),v in sorted(groups.items()) if n==name} for name in ('overall','sport_market','league_market','month_market','year_market')}
    result=dict(manifest=str(manifest_path),manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),sources=sourceproof,counts=dict(sorted(counts.items())),primary=breakdown(primary),all_products_nonindependent=breakdown(allrows))
    (OUT/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    for name,rows in [('primary.jsonl',primary),('all-products.jsonl',allrows)]:
        (OUT/name).write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf8')
    print(json.dumps(dict(sources=len(sourceproof),primary=result['primary']['sport_market'],all_products=result['all_products_nonindependent']['sport_market']),ensure_ascii=False))
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=MANIFEST)
    main(parser.parse_args().manifest)
