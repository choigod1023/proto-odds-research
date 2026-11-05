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
def fixture_index(forecasts):
    idx=collections.defaultdict(list)
    for fid in forecasts:
        if len(fid)==17 and fid[:8].isdigit() and fid[-4:]=='2026':
            idx[(fid[:8],fid[10:12],fid[8:10])].append(fid)
    return idx

def product_kind(row, strict):
    bet,typ=str(row.get('betId')),str(row.get('betTypId'))
    if bet=='79' and typ=='7' and row.get('handi')==9: return 'total'
    if bet=='7' and typ=='5' and row.get('handi')==23: return 'handicap'
    if not strict and row.get('betId') in (None,'') and row.get('betTypId') in (None,''):
        return {9:'total',2:'handicap'}.get(row.get('handi'))
    return None

def winner_kind(row, strict):
    if str(row.get('betId'))=='2' and str(row.get('betTypId'))=='2': return True
    return not strict and row.get('handi')==0 and row.get('betId') in (None,'') and row.get('betTypId') in (None,'')

def choose_winner(rounds):
    # Exactly one candidate per date/teams/round, including incomplete rows.
    unique=[]
    for rnd, entries in rounds.items():
        if len(entries)!=1: continue
        w,source=entries[0]
        parts=str(w.get('mchScore') or '').split(':')
        if str(w.get('protoStatus'))=='4' and w.get('gameReject')=='0' and len(parts)==2 and all(x.isdigit() for x in parts):
            unique.append((int(rnd),w,source))
    if not unique: raise ValueError('winner_missing_or_ambiguous')
    if len({w['mchScore'] for _,w,_ in unique})!=1: raise ValueError('conflicting_fixture_scores')
    return sorted(unique,key=lambda x:(x[0],int(x[1]['matchSeq'])))[0]

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
    idx=fixture_index(forecasts)
    counts=collections.Counter(); sources=[]
    counts['forecast_without_official_id']=len(forecasts)-sum(len(v) for v in idx.values())
    counts['forecast_official_id']=sum(len(v) for v in idx.values())
    raw={mode:collections.defaultdict(list) for mode in ('strict','sensitivity')}
    winners={mode:collections.defaultdict(lambda:collections.defaultdict(list)) for mode in raw}
    for src in manifest['sources']:
        if not src['round'].startswith('26'): continue
        path=Path(manifest['source_roots'][src['source_root_index']])/src['body_file']; b=gzip.decompress(path.read_bytes())
        if sha(b)!=src['body_sha256']: raise ValueError('hash mismatch '+str(path))
        source={'round':src['round'],'path':str(path),'sha256':sha(b)}; sources.append(source)
        data=json.loads(b)['compSchedules']; groups=collections.defaultdict(list)
        for vals in data['datas']:
            r=dict(zip(data['keys'],vals))
            if r.get('leagueCode')=='BS001': groups[(r['gameDate'],r['homeId'],r['awayId'])].append(r)
        for (ts,home,away),items in groups.items():
            date=datetime.datetime.fromtimestamp(ts/1000,datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y%m%d'); ids=idx.get((date,home,away),[])
            for mode in raw:
                strict=mode=='strict'
                if len(ids)==1:
                    winners[mode][ids[0]][src['round']].extend((r,source) for r in items if winner_kind(r,strict))
                for row in items:
                    market=product_kind(row,strict)
                    if not market: continue
                    counts[mode+'_'+market+'_raw_rows']+=1
                    if len(ids)!=1:
                        counts[mode+'_'+market+('_no_forecast' if not ids else '_ambiguous_forecast')]+=1; continue
                    raw[mode][(ids[0],market)].append((int(src['round']),int(row['matchSeq']),row,source))
    for mode in raw:
        counts[mode+'_ambiguous_general_fixture_rounds']=sum(len(entries)>1 for rounds in winners[mode].values() for entries in rounds.values())
    cohorts={mode:[] for mode in raw}; exclusions=[]
    for mode in raw:
      for (fid,market),products in sorted(raw[mode].items()):
        products.sort(key=lambda x:(x[0],x[1])); rnd,seq,row,source=products[0]
        prefix=mode+'_'+market
        counts[prefix+'_fixture_candidates']+=1; counts[prefix+'_noncanonical_products']+=len(products)-1
        try:
            if len(products)>1 and products[0][:2]==products[1][:2]: raise ValueError('duplicate_product_key')
            wrnd,winner,wsource=choose_winner(winners[mode][fid])
            result=calc(market,row,winner,forecasts[fid]['m0_actual'])
            result.update(fixture_id=fid,date=fid[:4]+'-'+fid[4:6]+'-'+fid[6:8],market=market,round=str(rnd),game_no=seq,body_sha256=source['sha256'],betId=row.get('betId'),betTypId=row.get('betTypId'),general_round=str(wrnd),general_game_no=winner['matchSeq'],general_body_sha256=wsource['sha256'],general_body_path=wsource['path'],general_betId=winner.get('betId'),general_betTypId=winner.get('betTypId'))
            cohorts[mode].append(result);counts[prefix+'_eligible']+=1
        except (KeyError,TypeError,ValueError,ArithmeticError) as e:
            reason=str(e);counts[prefix+'_excluded_'+reason]+=1; exclusions.append(dict(cohort=mode,fixture_id=fid,market=market,round=rnd,game_no=seq,reason=reason))
      for market in ('total','handicap'):
        counts[mode+'_'+market+'_forecast_fixtures_without_candidate']=709-sum(k[1]==market for k in raw[mode])
    def hc_distribution(rows):
        out=collections.Counter()
        for r in rows:
            if r['market']!='handicap': continue
            out['line_'+str(r['line'])]+=1
            out['pick_'+r['model_selection']]+=1
            picked_line=r['line'] if r['model_selection']=='home' else -r['line']
            out['selected_line_'+str(picked_line)]+=1
            out['selected_market_favorite' if r['model_selection']==r['market_selection'] else 'selected_market_underdog']+=1
        return dict(out)
    old_hc=read(OUT/'old44-handicap/cohort.json')
    old_total=[dict(r,model_selection=r['selection'],market_selection=r['baseline'],date=r['fixture_id'][:4]+'-'+r['fixture_id'][4:6]+'-'+r['fixture_id'][6:8]) for r in old]
    old_hc=[dict(r,date=r['date_kst'][:4]+'-'+r['date_kst'][4:6]+'-'+r['date_kst'][6:8]) for r in old_hc]
    result={'input_hashes':{str(p):sha(p.read_bytes()) for p in (mp,fp,cp,rp)},'sources':sources,'counts':dict(counts),
            'canonical':{mode:{m:summary([r for r in rows if r['market']==m]) for m in ('total','handicap')} for mode,rows in cohorts.items()},
            'date_ranges':{mode:{m:[min((r['date'] for r in rows if r['market']==m),default=None),max((r['date'] for r in rows if r['market']==m),default=None)] for m in ('total','handicap')} for mode,rows in cohorts.items()},
            'handicap_distribution':{mode:hc_distribution(rows) for mode,rows in cohorts.items()},
            'old44':{'total':summary(old_total),'handicap':summary(old_hc)},'exclusions':exclusions}
    for name,obj in [('audit.json',result),('canonical.json',cohorts)]: (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'counts':dict(counts),'date_ranges':result['date_ranges'],'distribution':result['handicap_distribution'],'canonical':{mode:{k:v['all'] for k,v in groups.items()} for mode,groups in result['canonical'].items()}},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
