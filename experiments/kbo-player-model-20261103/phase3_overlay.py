"""Offline, preregistered KBO price overlay for frozen phase2 predictions."""
import argparse,collections,gzip,hashlib,json,math,subprocess
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parent
P=ROOT/'phase2'
DATA=Path('C:/Users/user/Documents/Codex/kbo-official-odds-20261103')
S=DATA/'experiments/kbo-official-odds-20261103'
ORIGINAL=Path('C:/Users/user/Documents/Codex/kbodata/experiments/kbo-player-data-20261103')
COMMIT='3167b4b19c2f5877af754d3a1a41ee2a8761a3f2'
EXPECTED={'predictions.json':'d841cd9549a9dab4f3c9612e2fbdea84b9f914b390da4f9c80c0d9e2b3e5d317','features.jsonl.gz':'346edd84d4388f99cc9ad23f7ad530560445b4bc06b6f841b745dabffa1a8b42','test-labels.jsonl.gz':'885b5dc0d182fa91d29ea4d74a078fb8835acf817095604106564864d0e19dc9','results.json':'8c8776c43dc0013056026c83c3b5afaf2d462a7de3e346438b2e71e536408562'}
SOURCE_HASH='d9fc2f04fabec1fa41d29521aeafdb96e676747e59723396f36ba5c88a9b8117'
RAW_HASH={'rows.jsonl':'66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c','labels.jsonl':'ef32ffe806b41521f5bd53c12d0eea1362bd44293181fc3ebb7c24c6fcb249ca'}
TEAMS={'한화':'HH','KT':'KT','삼성':'SS','두산':'OB','LG':'LG','키움':'WO','KIA':'HT','롯데':'LT','NC':'NC','SSG':'SK'}
KST=timezone(timedelta(hours=9));MODES=('team','starter','lineup','bullpen','starter_lineup','player')


def sha(data):return hashlib.sha256(data).hexdigest()
def checked(path,expected):
    raw=Path(path).read_bytes()
    if sha(raw)!=expected:raise ValueError('SHA mismatch: '+str(path))
    return raw

def load_gzip(path,expected):return [json.loads(x) for x in gzip.decompress(checked(path,expected)).splitlines() if x.strip()]

def exact_committed_price(contents,blob):
    if contents!=blob:raise ValueError('official price bytes differ from committed blob')

def verify_sources(price_path=None):
    for name,expected in EXPECTED.items():checked(P/name,expected)
    for name,expected in RAW_HASH.items():checked(ORIGINAL/name,expected)
    manifest=json.loads((S/'manifest.json').read_text(encoding='utf8'))
    if manifest['status']!='ready_for_join' or manifest['output_sha256']!=SOURCE_HASH or any(manifest['input_'+name+'_sha256']!=RAW_HASH[name+'.jsonl'] for name in ('rows','labels')):raise ValueError('manifest mismatch')
    raw=checked(price_path or S/'restored-prices.jsonl',SOURCE_HASH)
    for name,contents in [('manifest.json',(S/'manifest.json').read_bytes()),('restored-prices.jsonl',raw)]:
        blob=subprocess.check_output(['git','-C',str(DATA),'show',COMMIT+':experiments/kbo-official-odds-20261103/'+name])
        if name=='restored-prices.jsonl':exact_committed_price(contents,blob)
        elif json.loads(contents)!=json.loads(blob):raise ValueError('worker manifest differs from commit')
    restored=[json.loads(x) for x in raw.splitlines()]
    if len(restored)!=373 or len({r['fixture_id'] for r in restored})!=373 or len({r['market_id'] for r in restored})!=373:raise ValueError('restored duplicate/count')
    return manifest,restored


def valid_price(value):
    try:d=Decimal(str(value))
    except (InvalidOperation,ValueError):return False
    return d.is_finite() and Decimal(1)<d<Decimal(1000)

def shin(prices):
    pi=[1/float(x) for x in prices];total=sum(pi)
    if total<=1:return [x/total for x in pi]
    def values(z):
        if z<=1e-12:return [x/total for x in pi]
        return [(math.sqrt(z*z+4*(1-z)*x*x/total)-z)/(2*(1-z)) for x in pi]
    lo=0.;hi=.9
    for _ in range(200):
        mid=(lo+hi)/2;su=sum(values(mid))
        if abs(su-1)<1e-12:break
        if su>1:lo=mid
        else:hi=mid
    out=values((lo+hi)/2);return [x/sum(out) for x in out]

def epoch_date(ms):return datetime.fromtimestamp(ms/1000,KST)

def candidate_matches(candidate,row,label):
    modern=candidate.get('betId')=='2' and candidate.get('betTypId')=='2'
    legacy=candidate.get('betId') is None and candidate.get('betTypId') is None and candidate.get('handi')==0
    if candidate.get('leagueCode')!='BS001' or not (modern or legacy):return False
    if candidate.get('homeId')!=TEAMS.get(row['home_id']) or candidate.get('awayId')!=TEAMS.get(row['away_id']):return False
    if candidate.get('drawAllot') not in (0,0.0) or candidate.get('gameSubject') not in (None,''):return False
    if candidate.get('gameReject')!='0' or candidate.get('buyReject')!='0':return False
    if candidate.get('mchScore')!=f"{label['home_score']}:{label['away_score']}":return False
    try:start=epoch_date(candidate['gameDate'])
    except (KeyError,TypeError,ValueError,OverflowError):return False
    if start.date()!=date.fromisoformat(row['event_date']) or not all(valid_price(candidate.get(x)) for x in ('winAllot','loseAllot')):return False
    if row['kickoff_at'] and datetime.fromisoformat(row['kickoff_at'])!=start:return False
    return True


def load_evidence():
    attempts_raw=(S/'evidence/attempts.json').read_bytes()
    blob=subprocess.check_output(['git','-C',str(DATA),'show',COMMIT+':experiments/kbo-official-odds-20261103/evidence/attempts.json'])
    if json.loads(attempts_raw)!=json.loads(blob):raise ValueError('worker evidence manifest differs from commit')
    attempts=json.loads(attempts_raw)
    by_file={a['body_file']:a for a in attempts if a.get('http_status')==200 and a.get('body_file')}
    listing_raw=gzip.decompress((S/'evidence/closed-list.body.gz').read_bytes())
    if 'evidence/closed-list.body.gz' not in by_file or sha(listing_raw)!=by_file['evidence/closed-list.body.gz']['body_sha256']:raise ValueError('official closed-list SHA mismatch')
    listing=json.loads(listing_raw)['schedules']['data']
    official={}
    for body,attempt in by_file.items():
        if not body.startswith('evidence/detail-') or not body.endswith('.body.gz'):continue
        raw=gzip.decompress((S/body).read_bytes())
        if sha(raw)!=attempt['body_sha256']:raise ValueError('official evidence SHA mismatch')
        data=json.loads(raw);ts=str(data['gmTs'])
        if ts!=str(attempt['request_json']['gmTs']) or ts in official:raise ValueError('round identity duplicate')
        table=data['compSchedules'];keys=table['keys'];official[ts]={'body':body,'attempt':attempt,'rows':[dict(zip(keys,v)) for v in table['datas']]}
    if len(official)!=102:raise ValueError('incomplete official evidence')
    return listing,official


def unique_match(row,label,listing,official):
    candidates=[];day=date.fromisoformat(row['event_date'])
    for schedule in listing:
        if schedule.get('gmOsidTsYear')!=int(row['season']) or not schedule.get('saleStartDate') or not schedule.get('saleEndDate'):continue
        if not epoch_date(schedule['saleStartDate']).date()<=day<=epoch_date(schedule['saleEndDate']).date():continue
        ts=str(schedule['gmTs']);detail=official.get(ts)
        if detail is None:raise ValueError('missing required fetched round')
        for c in detail['rows']:
            if candidate_matches(c,row,label):candidates.append((ts,detail,c))
    return candidates


def classify(row,label,rec,listing,official,physical_counts):
    """Return admissible source and evidence or a specific exclusion reason."""
    if label['status']!='final':return None,'nonfinal_label'
    old=row['market']
    if old is not None:
        if not isinstance(old.get('market_id'),str) or not old['market_id'].strip():return None,'original_market_id_missing'
        if old.get('kind')!='full_game_2way' or old.get('settlement_rule')!='draw_void_refund' or set(old.get('selections',{}))!={'home','away'}:return None,'original_product_mismatch'
        if not all(valid_price(v) for v in old['selections'].values()):return None,'original_price_invalid'
        if row['kickoff_at'] is None:return None,'original_kickoff_missing'
        return {'origin':'original_archived','prices':old['selections'],'market_id':old['market_id'],'kickoff_matched':True,'official_evidence':None},None
    if rec is None:return None,'no_restored_match'
    if row['kickoff_at'] is None and physical_counts[(row['event_date'],row['home_id'],row['away_id'])]!=1:return None,'date_only_doubleheader_ambiguous'
    candidates=unique_match(row,label,listing,official)
    if len(candidates)!=1:return None,'official_none_or_ambiguous'
    ts,detail,v=candidates[0];a=detail['attempt']
    expected_id=f"proto:{row['season']}:{int(ts[2:])}:{v['matchSeq']}:full_game_2way"
    if (str(rec['official_round_id'])!=ts or str(rec['match_seq'])!=str(v['matchSeq']) or rec['market_id']!=expected_id or rec['response_file']!=detail['body'] or rec['response_sha256']!=a['body_sha256'] or rec['received_at']!=a['received_at']):return None,'official_evidence_mismatch'
    if rec['score']!=v['mchScore'] or rec['official_home_id']!=v['homeId'] or rec['official_away_id']!=v['awayId'] or rec['prices']!={'home':v['winAllot'],'away':v['loseAllot']} or datetime.fromisoformat(rec['game_at'])!=epoch_date(v['gameDate']):return None,'official_payload_mismatch'
    if rec['source_observed_at'] is not None or rec['execution_price_verified'] is not False or rec['source_kickoff_matched']!=bool(row['kickoff_at']):return None,'availability_claim_mismatch'
    return {'origin':'official_restored','prices':rec['prices'],'market_id':rec['market_id'],'kickoff_matched':bool(row['kickoff_at']),'official_evidence':{'round_id':rec['official_round_id'],'match_seq':rec['match_seq'],'response_file':rec['response_file'],'response_sha256':rec['response_sha256'],'received_at':rec['received_at'],'source_observed_at':None,'execution_price_verified':False,'game_at':rec['game_at']}},None


def metrics(ledger,name):
    hits=voids=0;net=Decimal(0);daily=collections.defaultdict(Decimal);monthly=collections.defaultdict(Decimal);loss=[];brier=[]
    for r in ledger:
        f=r['forecasts'][name];v=Decimal(f['net']);net+=v;daily[r['date']]+=v;monthly[r['date'][:7]]+=v;hits+=f['hit'];voids+=f['void']
        if not f['void']:
            y=0 if r['winner']=='home' else 1;p=f['probabilities'];loss.append(-math.log(max(p[y],1e-15)));brier.append(sum((x-int(i==y))**2 for i,x in enumerate(p)))
    balance=peak=dd=Decimal(0)
    for day in sorted(daily):balance+=daily[day];peak=max(peak,balance);dd=max(dd,peak-balance)
    n=len(ledger);return {'bets':n,'hits':hits,'voids':voids,'decisive':n-voids,'accuracy_decisive':hits/(n-voids) if n>voids else None,'net_units':str(net),'roi_including_refunds':float(net/n) if n else None,'logloss_decisive':float(np.mean(loss)) if loss else None,'brier_decisive':float(np.mean(brier)) if brier else None,'monthly_units':{k:str(v) for k,v in sorted(monthly.items())},'max_daily_close_drawdown_units':str(dd) if n else None}

def paired(ledger,name,baseline):
    groups=collections.defaultdict(lambda:np.zeros(4));changed=0
    for r in ledger:
        a=r['forecasts'][name];b=r['forecasts'][baseline];w=date.fromisoformat(r['date']).isocalendar()[:2];x=groups[w]
        if not a['void']:x[0]+=1;x[1]+=int(a['hit'])-int(b['hit'])
        x[2]+=1;x[3]+=float(Decimal(a['net'])-Decimal(b['net']));changed+=a['pick']!=b['pick']
    ans={'weeks':len(groups),'changed':changed,'accuracy_delta_ci95':None,'roi_delta_ci95':None}
    if len(groups)>=2:
        arr=np.array([groups[k] for k in sorted(groups)]);rng=np.random.default_rng(20261103);draw=arr[rng.integers(0,len(arr),size=(5000,len(arr)))].sum(axis=1)
        if np.all(draw[:,0]>0):ans['accuracy_delta_ci95']=np.quantile(draw[:,1]/draw[:,0],[.025,.975]).tolist()
        ans['roi_delta_ci95']=np.quantile(draw[:,3]/draw[:,2],[.025,.975]).tolist()
    return ans


def evaluate(fixtures,labels,pred,restored,listing,official,worker_excluded):
    if len(fixtures)!=726 or set(fixtures)!=set(labels) or set(pred)!=set(MODES):raise ValueError('frozen target cohort mismatch')
    forecast={}
    for name,items in pred.items():
        indexed={p['fixture_id']:p for p in items}
        if len(items)!=726 or set(indexed)!=set(fixtures):raise ValueError('prediction identity mismatch')
        forecast[name]=indexed
    recs={r['fixture_id']:r for r in restored};physical=collections.Counter((r['event_date'],r['home_id'],r['away_id']) for r in fixtures.values());entries=[];excluded=collections.Counter()
    for fid,row in sorted(fixtures.items(),key=lambda kv:(kv[1]['event_date'],kv[0])):
        label=labels[fid]; info,reason=classify(row,label,recs.get(fid),listing,official,physical)
        if reason:excluded[reason]+=1;continue
        p=info['prices'];winner='home' if label['home_score']>label['away_score'] else 'away' if label['away_score']>label['home_score'] else 'draw'
        forecasts={};market=shin([p['home'],p['away']])
        for name in ('market',)+MODES:
            if name=='market':probs=market
            else:
                v=forecast[name][fid]['probabilities_2way'];probs=[v['home'],v['away']]
                if any(not math.isfinite(x) or x<0 for x in probs) or not math.isclose(sum(probs),1,abs_tol=1e-9):raise ValueError('invalid frozen probability')
            pick=('home','away')[max(range(2),key=lambda j:probs[j])];void=winner=='draw';hit=pick==winner and not void
            net=Decimal(0) if void else Decimal(str(p[pick]))-1 if hit else Decimal(-1)
            forecasts[name]={'probabilities':probs,'pick':pick,'hit':hit,'void':void,'net':str(net)}
        entries.append({'fixture_id':fid,'date':row['event_date'],'home_id':row['home_id'],'away_id':row['away_id'],'winner':winner,'price_source':info,'forecasts':forecasts})
    # If a market ID appears twice, neither row can be assigned safely.
    count=collections.Counter(x['price_source']['market_id'] for x in entries);dupes=[x for x in entries if count[x['price_source']['market_id']]>1]
    excluded['duplicate_market_id']+=len(dupes);entries=[x for x in entries if count[x['price_source']['market_id']]==1]
    strict=[x for x in entries if x['price_source']['kickoff_matched']];groups={'strict_original_time_match':strict,'extended_day_team_source_confirmed':entries}
    out={'opportunities':len(fixtures),'excluded':dict(excluded),'worker_exclusions_all_2023_2026':worker_excluded,'verified_execution_price_count':0,'verified_execution_roi':None,'phase2_results_sha256':EXPECTED['results.json'],'price_scenario':'retrospective; official fields are not confirmed T-30 observations','cohorts':{}}
    for key,ls in groups.items():
        out['cohorts'][key]={'n':len(ls),'coverage':len(ls)/len(fixtures),'source_counts':dict(collections.Counter(x['price_source']['origin'] for x in ls)),
          'draw_refunds':sum(x['winner']=='draw' for x in ls),'date_only_restored':sum(not x['price_source']['kickoff_matched'] for x in ls),
          'date_range':[min((x['date'] for x in ls),default=None),max((x['date'] for x in ls),default=None)],
          'models':{name:metrics(ls,name) for name in ('market',)+MODES},
          'player_vs_market':paired(ls,'player','market'),'player_vs_team':paired(ls,'player','team')}
    return out,groups


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--restored-prices-path',help='exact raw blob, defaults to worker file');args=parser.parse_args();manifest,restored=verify_sources(args.restored_prices_path)
    features=load_gzip(P/'features.jsonl.gz',EXPECTED['features.jsonl.gz']);labels=load_gzip(P/'test-labels.jsonl.gz',EXPECTED['test-labels.jsonl.gz']);pred=json.loads(checked(P/'predictions.json',EXPECTED['predictions.json']))
    fixtures={r['fixture_id']:r for r in features if r['season']==2026};ys={r['fixture_id']:r for r in labels}
    listing,official=load_evidence();out,groups=evaluate(fixtures,ys,pred['predictions'],restored,listing,official,manifest['excluded'])
    out['sha256']={'predictions':EXPECTED['predictions.json'],'features':EXPECTED['features.jsonl.gz'],'labels':EXPECTED['test-labels.jsonl.gz'],'official_overlay':SOURCE_HASH};out['phase2_comparison_note_erratum']='The phase2 archived-price-scenario comparison_note incorrectly describes prices as independently evidenced historical timing. No such timing or executability was established.'
    with Path(args.output).open('x',encoding='utf8',newline='\n') as f:json.dump({'results':out,'ledgers':groups},f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({'opportunities':out['opportunities'],'cohorts':{k:{'n':v['n'],'sources':v['source_counts'],'refunds':v['draw_refunds']} for k,v in out['cohorts'].items()},'exclusions':out['excluded']},ensure_ascii=False))
if __name__=='__main__':main()
