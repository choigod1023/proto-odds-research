import argparse, collections, datetime, gzip, hashlib, json, math
from decimal import Decimal
from pathlib import Path
from scipy.stats import poisson, skellam

OUT = Path(__file__).resolve().parent
SRC = Path('C:/Users/user/Documents/Codex/market-outcomes-validation/experiments')

def read(path): return json.loads(path.read_text(encoding='utf8'))
def sha(data): return hashlib.sha256(data).hexdigest()
def half(x):
    return x.is_finite() and x*2 == (x*2).to_integral_value() and x != x.to_integral_value()
def calc(market, row, winner, rates):
    if str(row['protoStatus']) != '4' or row['gameReject'] != '0' or row['gameSubject'] not in (None, '') or float(row['drawAllot']) != 0: raise ValueError('status_or_scope')
    if str(winner['protoStatus']) != '4' or winner['gameReject'] != '0': raise ValueError('winner_status')
    score = str(winner['mchScore']).split(':')
    if len(score)!=2 or not all(x.isdigit() for x in score): raise ValueError('winner_score')
    h,a=map(int,score)
    actual='home' if h>a else 'away' if h<a else 'draw'
    if str(winner['gameResult']) != {'home':'0','away':'2','draw':'4'}[actual]: raise ValueError('winner_grade')
    line=Decimal(str(row['winHandi'])); other=Decimal(str(row['loseHandi']))
    if not half(line) or other != (line if market=='total' else -line): raise ValueError('line')
    sides=('under','over') if market=='total' else ('home','away')
    prices={sides[0]:float(row['winAllot']),sides[1]:float(row['loseAllot'])}
    if not all(math.isfinite(x) and x>1 for x in prices.values()): raise ValueError('prices')
    if prices[sides[0]]==prices[sides[1]]: raise ValueError('market_tie')
    hr,ar=float(rates['home_rate']),float(rates['away_rate'])
    if not all(math.isfinite(x) and x>0 for x in (hr,ar)): raise ValueError('rates')
    parts=str(row['mchScore']).split(':')
    if len(parts)!=2: raise ValueError('product_score')
    vals=[Decimal(x) for x in parts]
    if market=='total':
        if vals != [Decimal(h+a),Decimal(0)]: raise ValueError('product_score')
        outcome='under' if h+a<line else 'over'
        prob=float(poisson.cdf(math.floor(float(line)),hr+ar))
    else:
        if vals != [Decimal(h)+line,Decimal(a)]: raise ValueError('product_score')
        outcome='home' if Decimal(h)+line>a else 'away'
        prob=float(skellam.sf(math.floor(-float(line)),hr,ar))
    if str(row['gameResult']) != ('0' if outcome==sides[0] else '2'): raise ValueError('product_grade')
    if prob == .5: raise ValueError('model_tie')
    return dict(score=[h,a],line=float(line),outcome=outcome,prices=prices,model_selection=sides[0] if prob>.5 else sides[1],market_selection=min(prices,key=prices.get),model_first_side_probability=prob, home_rate=hr,away_rate=ar)

def metric(rows,field):
    net=peak=dd=0; hits=0
    for r in rows:
        hit=r[field]==r['outcome'];hits+=hit
        net += r['prices'][r[field]]-1 if hit else -1
        peak=max(peak,net);dd=max(dd,peak-net)
    n=len(rows)
    return dict(n=n,hits=hits,refunds=0,hit_rate=hits/n if n else None,net_units=round(net,6),roi=net/n if n else None,max_drawdown_units=round(dd,6))
def summary(rows):
    rows=sorted(rows,key=lambda r:(r['date'],r['fixture_id'],int(r['round']),int(r['game_no'])))
    groups={'all':rows}
    for r in rows: groups.setdefault(r['date'][:7],[]).append(r)
    return {k:{'model':metric(v,'model_selection'),'market':metric(v,'market_selection'),'disagreements':sum(x['model_selection']!=x['market_selection'] for x in v),'model_hits_on_disagreements':sum(x['model_selection']!=x['market_selection'] and x['model_selection']==x['outcome'] for x in v),'market_hits_on_disagreements':sum(x['model_selection']!=x['market_selection'] and x['market_selection']==x['outcome'] for x in v)} for k,v in groups.items()}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--manifest',type=Path,default=OUT/'source-manifest.json'); args=parser.parse_args()
    mp=args.manifest; fp=SRC/'m1-service-20261104/replay/replayed-709.json.gz'; cp=SRC/'m0-multimarket-20261105/cohort.json'; rp=SRC/'m1-service-20261104/replay/service-replay.json'
    manifest=read(mp); forecasts=json.loads(gzip.decompress(fp.read_bytes())); old=read(cp); replay=read(rp)
    if len(forecasts)!=709 or len(old)!=44 or len(replay['rows'])!=667: raise ValueError('unexpected input cohort size')
    idx=collections.defaultdict(list)
    for fid in forecasts:
        if len(fid)!=17 or not fid[:8].isdigit() or fid[-4:]!='2026': continue
        idx[(fid[:8],fid[10:12],fid[8:10])].append(fid)
    winners_by_fixture=collections.defaultdict(list); raw=collections.defaultdict(list); counts=collections.Counter(); sources=[]; counts['forecast_without_official_id']=len(forecasts)-sum(len(v) for v in idx.values()); counts['forecast_official_id']=sum(len(v) for v in idx.values())
    for src in manifest['sources']:
        if not src['round'].startswith('26'): continue
        path=Path(manifest['source_roots'][src['source_root_index']])/src['body_file']; b=gzip.decompress(path.read_bytes())
        if sha(b)!=src['body_sha256']: raise ValueError('hash mismatch '+str(path))
        sources.append({'round':src['round'],'path':str(path),'sha256':sha(b)})
        data=json.loads(b)['compSchedules']; groups=collections.defaultdict(list)
        for vals in data['datas']:
            r=dict(zip(data['keys'],vals))
            if r.get('leagueCode')=='BS001': groups[(r['gameDate'],r['homeId'],r['awayId'])].append(r)
        for (ts,home,away),items in groups.items():
            date=datetime.datetime.fromtimestamp(ts/1000,datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y%m%d'); ids=idx.get((date,home,away),[])
            if len(ids)==1:
                winners_by_fixture[ids[0]].extend(r for r in items if ((r.get('handi')==0 and r.get('betId') in (None,'')) or str(r.get('betId'))=='2') and str(r.get('protoStatus'))=='4' and r.get('gameReject')=='0' and len(str(r.get('mchScore') or '').split(':'))==2 and all(x.isdigit() for x in str(r.get('mchScore') or '').split(':')))
            for product in [r for r in items if (r.get('handi')==9 and r.get('betId') in (None,'','79')) or (r.get('handi')==2 and r.get('betId') in (None,'')) or (r.get('handi')==23 and str(r.get('betId'))=='7')]:
                market='total' if product['handi']==9 else 'handicap';counts[market+'_raw_rows']+=1
                if len(ids)!=1: counts[market+('_no_forecast' if not ids else '_ambiguous_forecast')]+=1;continue
                winners=[r for r in items if r.get('handi')==0]
                raw[(ids[0],market)].append((int(src['round']),int(product['matchSeq']),product,winners,src))
    eligible=[]; exclusions=[]
    for (fid,market),products in sorted(raw.items()):
        products.sort(key=lambda x:(x[0],x[1])); chosen=products[0]; counts[market+'_fixture_candidates']+=1; counts[market+'_noncanonical_products']+=len(products)-1
        rnd,seq,row,winners,src=chosen
        try:
            if len(products)>1 and products[0][:2]==products[1][:2]: raise ValueError('duplicate_product_key')
            all_winners=winners_by_fixture[fid]
            if not all_winners: raise ValueError('winner_missing')
            official_scores={w['mchScore'] for w in all_winners}
            if len(official_scores)!=1: raise ValueError('conflicting_fixture_scores')
            result=calc(market,row,all_winners[0],forecasts[fid]['m0_actual'])
            # Other completed unadjusted scores for the same fixture must agree.
            scores=set()
            for _,_,_,ws,_ in products:
                if len(ws)==1 and str(ws[0].get('protoStatus'))=='4' and ws[0].get('gameReject')=='0':
                    s=str(ws[0].get('mchScore') or '').split(':')
                    if len(s)==2 and all(x.isdigit() for x in s): scores.add(tuple(map(int,s)))
            if len(scores)>1: raise ValueError('conflicting_fixture_scores')
            result.update(fixture_id=fid,date=fid[:4]+'-'+fid[4:6]+'-'+fid[6:8],market=market,round=str(rnd),game_no=seq,body_sha256=src['body_sha256'])
            eligible.append(result);counts[market+'_eligible']+=1
        except (KeyError,TypeError,ValueError,ArithmeticError) as e:
            reason=str(e);counts[market+'_excluded_'+reason]+=1; exclusions.append(dict(fixture_id=fid,market=market,round=rnd,game_no=seq,reason=reason))
    for market in ('total','handicap'):
        counts[market+'_forecast_fixtures_without_candidate']=709-sum(k[1]==market for k in raw)
    old_hc=read(OUT/'old44-handicap/cohort.json')
    old_total=[dict(r,model_selection=r['selection'],market_selection=r['baseline'],date=r['fixture_id'][:4]+'-'+r['fixture_id'][4:6]+'-'+r['fixture_id'][6:8]) for r in old]
    old_hc=[dict(r,date=r['date_kst'][:4]+'-'+r['date_kst'][4:6]+'-'+r['date_kst'][6:8]) for r in old_hc]
    result={'input_hashes':{str(p):sha(p.read_bytes()) for p in (mp,fp,cp,rp)},'sources':sources,'counts':dict(counts),'canonical':{m:summary([r for r in eligible if r['market']==m]) for m in ('total','handicap')},'old44':{'total':summary(old_total),'handicap':summary(old_hc)},'exclusions':exclusions}
    for name,obj in [('audit.json',result),('canonical.json',eligible)]: (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'counts':dict(counts),'canonical':{k:v['all'] for k,v in result['canonical'].items()},'old44':{k:v['all'] for k,v in result['old44'].items()}},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
