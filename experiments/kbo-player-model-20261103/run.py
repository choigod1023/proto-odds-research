"""Two-stage offline KBO evaluation. Predict freezes before labels can be read."""
import argparse,gzip,hashlib,importlib.util,json,math,sys
from collections import Counter,defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
import numpy as np
from scipy.special import gammaln
from scipy.stats import poisson
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from src.devig import shin
_spec=importlib.util.spec_from_file_location('_kbo_player_challenger_model',Path(__file__).with_name('model.py'))
m=importlib.util.module_from_spec(_spec);sys.modules[_spec.name]=m;_spec.loader.exec_module(m)


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read_jsonl(path):
    path=Path(path)
    with (gzip.open(path,'rt',encoding='utf8') if path.suffix=='.gz' else path.open(encoding='utf8')) as f:
        return [json.loads(line) for line in f if line.strip()]
def write_once(path,obj):
    with Path(path).open('x',encoding='utf8',newline='\n') as f:f.write(json.dumps(obj,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n')

def prepare(rows,development_labels, *, assume_prior_calendar_day=False):
    blocks=m.split(rows,development_labels,allow_retrospective_dates=assume_prior_calendar_day);lookup={r['fixture_id']:r for r in development_labels}
    fits={};scores={};predictions={}
    for mode in m.MODES:
        grid=[]
        for alpha in m.RIDGE:
            fitted=m.fit(blocks['train'],development_labels,mode,alpha,allow_retrospective_dates=assume_prior_calendar_day)
            pred=m.predict(fitted,blocks['validation']);loss=0.
            for p in pred:
                lab=lookup[p['fixture_id']]
                for side in ('home','away'):
                    y=lab[side+'_score'];rate=p[side+'_rate'];loss+=rate-y*math.log(rate)+gammaln(y+1)
            grid.append({'alpha':alpha,'mean_score_logloss':float(loss/(2*len(pred)))})
        best=min(grid,key=lambda x:(x['mean_score_logloss'],-x['alpha']))
        final=m.fit(blocks['train']+blocks['validation'],development_labels,mode,best['alpha'],allow_retrospective_dates=assume_prior_calendar_day)
        fits[mode]={'alpha':best['alpha'],'teams':list(final.teams),'mean':final.mean.tolist(),'scale':final.scale.tolist(),'beta':final.beta.tolist()}
        scores[mode]=grid;predictions[mode]=m.predict(final,blocks['test'])
    missing=Counter(name for row in blocks['test'] for name in m.FEATURES if row['features'].get(name,{}).get('value') is None)
    return {'registration':'b12cf82a','ablation_addendum':'61f3ec7d','retrospective_addendum':'9babf4e1','integration_protocol':'3d468fbf','assume_prior_calendar_day':assume_prior_calendar_day,'counts':{k:len(v) for k,v in blocks.items()},'availability_tier':blocks['test'][0]['availability_tier'],'validation_grid':scores,'models':fits,'predictions':predictions,'test_missing_features':dict(missing),'test_rows_with_any_player_feature':sum(any(row['features'].get(name,{}).get('value') is not None for name in m.FEATURES[5:]) for row in blocks['test']),'evaluation_status':'predictions_frozen_before_test_labels','limits':['independent Poisson, not production or wallet','2026 previously examined, not blind','tipster/current/9:1 not supplied']}


def credible_market(row, *, archived_price_scenario=False):
    market=row['market']
    if market is None:return False
    if archived_price_scenario:
        return row['availability_tier']=='retrospective_unproven' and market['kind']=='full_game_2way' and market['settlement_rule']=='draw_void_refund'
    if market.get('odds_observed_at') is None:return False
    source=next((s for s in row['sources'] if s['source_id']==market.get('source_id')),None)
    if source is None or source.get('received_at') is None:return False
    return m.stamp(source['received_at'])<=m.stamp(row['decision_at']) and m.stamp(market['odds_observed_at'])<=m.stamp(row['decision_at'])


def report(rows, frozen, labels, *, assume_prior_calendar_day=False, archived_price_scenario=False):
    if archived_price_scenario and not assume_prior_calendar_day:raise ValueError('archive price scenario requires retrospective assumption')
    m.validate_rows(rows,allow_retrospective_dates=assume_prior_calendar_day);m.validate_labels(labels,allow_retrospective_dates=assume_prior_calendar_day)
    test={r['fixture_id']:r for r in rows if int(r['season'])==2026};lab={x['fixture_id']:x for x in labels}
    if set(lab)-set(test):raise ValueError('test labels must refer only to frozen test fixtures')
    out={'availability_tier':frozen['availability_tier'],'opportunities':len(test),'models':{},'pairwise':{},'label_status':{},'diagnostics':{},'wallet_replay':False,'price_scenario':'archived_assumed_not_verified' if archived_price_scenario else 'verified_only','comparison_note':'only matched products and independently evidenced historical price timing; no actual operating or tipster data supplied'}
    bymode={}
    for mode,predictions in frozen['predictions'].items():
        if len(predictions)!=len(test) or {p['fixture_id'] for p in predictions}!=set(test):raise ValueError('prediction cohort mismatch')
        ledger=[];miss=Counter();score_diffs=[];poisson_chis=[];outcome_logloss=[];outcome_brier=[];matched=0
        for p in predictions:
            r=test[p['fixture_id']];l=lab.get(p['fixture_id'])
            if not l:miss['label_missing']+=1;continue
            status=l['status'];out['label_status'][status]=out['label_status'].get(status,0)+int(mode=='team')
            if status!='final':miss[status]+=1;continue
            if l['completed_at'] is None:
                if not assume_prior_calendar_day or l.get('assumed_completed_date')!=r.get('event_date'):raise ValueError('invalid retrospective result date')
            elif m.stamp(l['completed_at'])<m.stamp(r['kickoff_at']):raise ValueError('result completed before kickoff')
            y=0 if l['home_score']>l['away_score'] else 1 if l['home_score']==l['away_score'] else 2
            probs=p['probabilities_3way'];vec=[probs[k] for k in ('home','draw','away')];outcome_logloss.append(-math.log(max(vec[y],1e-15)));outcome_brier.append(sum((v-int(j==y))**2 for j,v in enumerate(vec)));score_diffs.append((l['home_score']-p['home_rate'],l['away_score']-p['away_rate']));poisson_chis.extend([(l['home_score']-p['home_rate'])**2/p['home_rate'],(l['away_score']-p['away_rate'])**2/p['away_rate']])
            market=r['market']
            if not credible_market(r,archived_price_scenario=archived_price_scenario):miss['price_or_timing_unverified']+=1;continue
            if l.get('market_id')!=market['market_id'] or l.get('settlement_rule')!=market['settlement_rule']:raise ValueError('label/product mismatch')
            order=['home','draw','away'] if market['kind']=='full_game_3way' else ['home','away']; prices=market['selections'];market_p=shin([float(prices[k]) for k in order]);candidate_p=[probs[k] for k in order] if len(order)==3 else [p['probabilities_2way'][k] for k in order]
            winner='home' if y==0 else 'draw' if y==1 else 'away'; entry={'fixture_id':r['fixture_id'],'day':r.get('event_date') or r['kickoff_at'][:10],'market_kind':market['kind'],'winner':winner,'forecasts':{}}
            for name,vec in [(mode,candidate_p),('market',market_p)]:
                pick=order[max(range(len(order)),key=lambda i:vec[i])];void=winner=='draw' and len(order)==2;hit=pick==winner and not void
                net=Decimal(0) if void else Decimal(str(prices[pick]))-1 if hit else Decimal(-1)
                entry['forecasts'][name]={'pick':pick,'hit':hit,'void':void,'net':str(net),'probabilities':vec}
            ledger.append(entry);matched+=1
        bymode[mode]=ledger
        out['diagnostics'][mode]={'final_labels':len(score_diffs),'all_final_3way_logloss':float(np.mean(outcome_logloss)) if outcome_logloss else None,'all_final_3way_brier':float(np.mean(outcome_brier)) if outcome_brier else None,'pearson_dispersion':float(np.mean(poisson_chis)) if poisson_chis else None,'residual_correlation':float(np.corrcoef(np.array(score_diffs).T)[0,1]) if len(score_diffs)>1 and np.std(np.array(score_diffs)[:,0])>0 and np.std(np.array(score_diffs)[:,1])>0 else None}
        out['models'][mode]={'excluded_or_pending':dict(miss),'matched_price_final':matched,'candidate':metrics(ledger,mode),'market':metrics(ledger,'market'),'by_product':{kind:{'candidate':metrics([v for v in ledger if v['market_kind']==kind],mode),'market':metrics([v for v in ledger if v['market_kind']==kind],'market')} for kind in ('full_game_2way','full_game_3way')}}
        out['pairwise'][mode]=paired(ledger,mode)
    team={line['fixture_id']:line for line in bymode['team']}
    for mode in ('starter','lineup','starter_lineup','bullpen','player'):
        aligned=[]
        for line in bymode[mode]:
            reference=team.get(line['fixture_id'])
            if reference is None:raise ValueError('team ablation cohort mismatch')
            aligned.append({**line,'forecasts':{**line['forecasts'],'team':reference['forecasts']['team']}})
        out['pairwise'][mode+'_vs_team']=paired(aligned,mode,'team')
    return out,bymode


def metrics(ledger,key):
    hits=0;void=0;net=Decimal(0);daily=defaultdict(Decimal);monthly=defaultdict(Decimal);ll=[];brier=[]
    for line in ledger:
        f=line['forecasts'][key];v=Decimal(f['net']);net+=v;daily[line['day']]+=v;monthly[line['day'][:7]]+=v;hits+=f['hit'];void+=f['void']
        if not f['void']:
            order=['home','away'] if len(f['probabilities'])==2 else ['home','draw','away'];y=order.index(line['winner']);ll.append(-math.log(max(f['probabilities'][y],1e-15)));brier.append(sum((p-int(j==y))**2 for j,p in enumerate(f['probabilities'])))
    cumulative=peak=dd=Decimal(0)
    for day in sorted(daily):cumulative+=daily[day];peak=max(peak,cumulative);dd=max(dd,peak-cumulative)
    n=len(ledger);return {'bets':n,'hits':hits,'voids':void,'accuracy_decisive':hits/(n-void) if n>void else None,'net_units':str(net),'roi_including_refunds':float(net/n) if n else None,'logloss_decisive':float(np.mean(ll)) if ll else None,'brier_decisive':float(np.mean(brier)) if brier else None,'monthly_units':{k:str(v) for k,v in sorted(monthly.items())},'max_daily_close_drawdown_units':str(dd) if n else None}


def paired(ledger,mode,baseline='market'):
    groups=defaultdict(lambda:np.zeros(4));changed=0
    for line in ledger:
        a=line['forecasts'][mode];b=line['forecasts'][baseline];d=date.fromisoformat(line['day']);g=groups[d.isocalendar()[:2]]
        if not a['void']:g[0]+=1;g[1]+=int(a['hit'])-int(b['hit'])
        g[2]+=1;g[3]+=float(Decimal(a['net'])-Decimal(b['net']));changed+=a['pick']!=b['pick']
    result={'changed':changed,'weeks':len(groups),'accuracy_delta_ci95':None,'roi_delta_ci95':None}
    if len(groups)>=2:
        arr=np.array([groups[k] for k in sorted(groups)]);rng=np.random.default_rng(20261103);draw=arr[rng.integers(0,len(arr),size=(5000,len(arr)))].sum(axis=1);valid=draw[:,0]>0
        if valid.all():result['accuracy_delta_ci95']=np.quantile(draw[:,1]/draw[:,0],[.025,.975]).tolist()
        result['roi_delta_ci95']=np.quantile(draw[:,3]/draw[:,2],[.025,.975]).tolist()
    return result


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='stage',required=True)
    predict=sub.add_parser('predict');predict.add_argument('--rows',required=True);predict.add_argument('--development-labels',required=True);predict.add_argument('--output',required=True);predict.add_argument('--assume-prior-calendar-day',action='store_true')
    score=sub.add_parser('score');score.add_argument('--rows',required=True);score.add_argument('--predictions',required=True);score.add_argument('--predictions-sha256',required=True);score.add_argument('--test-labels',required=True);score.add_argument('--output',required=True);score.add_argument('--assume-prior-calendar-day',action='store_true');score.add_argument('--archived-price-scenario',action='store_true')
    a=p.parse_args();rows=read_jsonl(a.rows)
    if a.stage=='predict':
        frozen=prepare(rows,read_jsonl(a.development_labels),assume_prior_calendar_day=a.assume_prior_calendar_day);frozen['input_sha256']={'rows':digest(a.rows),'development_labels':digest(a.development_labels)};frozen['code_sha256']={'model':digest(Path(__file__).with_name('model.py')),'run':digest(__file__),'devig':digest(ROOT/'src'/'devig.py')};write_once(a.output,frozen)
    else:
        if digest(a.predictions)!=a.predictions_sha256:raise ValueError('frozen predictions SHA mismatch')
        frozen=json.loads(Path(a.predictions).read_text(encoding='utf8'))
        if frozen['code_sha256']!={'model':digest(Path(__file__).with_name('model.py')),'run':digest(__file__),'devig':digest(ROOT/'src'/'devig.py')}:raise ValueError('model code changed after forecast')
        if frozen['input_sha256']['rows']!=digest(a.rows):raise ValueError('feature input changed after forecast')
        if bool(frozen['assume_prior_calendar_day'])!=bool(a.assume_prior_calendar_day):raise ValueError('retrospective assumption mismatch')
        labels=read_jsonl(a.test_labels);results,ledger=report(rows,frozen,labels,assume_prior_calendar_day=a.assume_prior_calendar_day)
        output={'verified_results':results,'verified_ledger':ledger,'sha256':{'rows':digest(a.rows),'predictions':digest(a.predictions),'test_labels':digest(a.test_labels)}}
        if a.archived_price_scenario:
            output['archived_price_scenario'],output['archived_scenario_ledger']=report(rows,frozen,labels,assume_prior_calendar_day=a.assume_prior_calendar_day,archived_price_scenario=True)
        write_once(a.output,output)
if __name__=='__main__':main()
