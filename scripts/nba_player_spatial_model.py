"""Retrospective player shot-location proxy; never uses target participation."""
import os
for name in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','MKL_NUM_THREADS'): os.environ[name]='1'
import gzip,hashlib,json,math
from collections import defaultdict,Counter
from datetime import date,timedelta
from pathlib import Path
import numpy as np
from scipy.special import expit,logit
import nba_player_conserved_snapshot as base
prior=base.prior;old=prior.old
ROOT=Path(__file__).resolve().parents[1]
CACHE=Path(os.environ.get('NBA_ESPN_CACHE','C:/Users/user/Documents/Codex/nba-espn-history-20261108e/.local-test/nba-espn-history-20261108'))
OLD=Path(os.environ.get('NBA_SHOT_PREVIOUS','C:/Users/user/Documents/Codex/nba-shot-profile-model-20261108/experiments/nba-shot-profile-model-20261108'))
HARD=Path(os.environ.get('NBA_SHOT_HARD','C:/Users/user/Documents/Codex/nba-shot-profile-data-20261108/experiments/nba-shot-profile-data-20261108/games.jsonl.gz'))
PLAYER=Path(os.environ.get('NBA_PLAYER_INPUT','C:/Users/user/Documents/Codex/nba-player-shot-inputs-20261108/experiments/nba-player-shot-inputs-20261108'))
OUT=Path(os.environ.get('NBA_PLAYER_OUTPUT',str(ROOT/'experiments/nba-player-spatial-model-20261108')))
EPS=1e-6

def player_minutes(value):
    if isinstance(value,str) and ':' in value:
        parts=value.split(':');return int(parts[0])+int(parts[1])/60,1/60
    return float(value),1.

def rotation_box(game,cache=CACHE):
    path=cache/'summary'/f"{game['id']}.json"
    if not path.exists() or game.get('summary_invalid') or game.get('expected_team_minutes') is None:return None
    try:
        data=old.verified_json(path);by={z['team']['abbreviation']:z for z in data['boxscore']['players']}; rotations={}
        if set(by)!=set(game['teams']):return None
        for team in game['teams']:
            mins={}; precision=0.
            for block in by[team]['statistics']:
                keys=block.get('keys',[])
                for item in block['athletes']:
                    aid=str(item['athlete']['id']);stats=dict(zip(keys,item.get('stats',[])))
                    if aid in mins:return None
                    if 'minutes' not in stats:
                        if item.get('didNotPlay'):mins[aid]=0.;continue
                        return None
                    minute,prec=player_minutes(stats['minutes'])
                    if not math.isfinite(minute) or not 0<=minute<=75:return None
                    mins[aid]=minute;precision+=prec
            if not 200<=sum(mins.values())<=400:return None
            # Rounded display must remain plausible as 240 + 25 per overtime.
            actual=sum(mins.values())
            if abs(actual-game['expected_team_minutes'])>precision+1e-9:return None
            rotations[team]=mins
        return rotations
    except (KeyError,ValueError,TypeError,IndexError):return None

def projected_rotation(team,day,team_history,boxes):
    end=date.fromisoformat(day)-timedelta(days=4);games=[g for g in team_history[team] if date.fromisoformat(g['day'])<=end][-10:]
    if len(games)<10:return None,'insufficient_team_games'
    totals=defaultdict(float)
    for g in games:
        box=boxes[g['id']]
        if box is None or team not in box:return None,'missing_or_invalid_recent_box'
        for pid,minute in box[team].items():totals[pid]+=minute
    total=sum(totals.values())
    if total<=0:return None,'zero_minutes'
    projected={p:240*v/total for p,v in totals.items() if v>0}
    return {'minutes':projected,'history_game_ids':[g['id'] for g in games]},None

def mix_shooting(rotation,player_states,league_attempts,league_makes,league_minutes,defense_attempts,defense_makes,spatial):
    if not spatial:
        def pool(v):return np.array([sum(v[:5]),sum(v[5:])],dtype=float)
        la,lm,da,dm=map(pool,(league_attempts,league_makes,defense_attempts,defense_makes));n=2;families=(range(0,1),range(1,2))
    else:la,lm,da,dm=map(lambda v:np.asarray(v,dtype=float),(league_attempts,league_makes,defense_attempts,defense_makes));n=9;families=(range(0,5),range(5,9))
    if league_minutes<=0 or any(sum(la[i] for i in fam)<=0 for fam in families):raise ValueError('prior_denominator')
    made=[]; cold=0.;detail=[]
    for fam in families:
        total_family=sum(la[i] for i in fam); denom=0.;numer=0.;zweights=np.zeros(n)
        for pid,minute in rotation.items():
            state=player_states.get(pid);eligible=state is not None and state['games']>=5
            if fam==families[0] and not eligible:cold+=minute
            if eligible:
                a=state['attempts'];m=state['makes'];a=pool(a) if not spatial else np.asarray(a);m=pool(m) if not spatial else np.asarray(m);exposure=state['minutes']
            for i in fam:
                if la[i]<=0:continue
                league_p=float(np.clip(lm[i]/la[i],EPS,1-EPS));strength=200*la[i]/total_family;rate=la[i]/league_minutes
                if eligible:
                    attempt_rate=(a[i]+240*rate)/(exposure+240);p_off=np.clip((m[i]+strength*league_p)/(a[i]+strength),EPS,1-EPS)
                else:attempt_rate=rate;p_off=league_p
                p_def=np.clip((dm[i]+strength*league_p)/(da[i]+strength),EPS,1-EPS)
                p=np.clip(expit(logit(p_off)+logit(p_def)-logit(league_p)),EPS,1-EPS)
                w=minute*attempt_rate;zweights[i]+=w;denom+=w;numer+=w*p
        if denom<=0:raise ValueError('zero_projected_attempts')
        made.append(float(numer/denom));detail.append({'zone_weights':(zweights/denom).tolist(),'weighted_accuracy':float(numer/denom)})
    return np.array(made),cold/240,detail
def hash_file(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read_gzip(path):return [json.loads(x) for x in gzip.decompress(path.read_bytes()).splitlines()]

def reconcile_sources(pool,players,audits):
    grouped=defaultdict(list)
    for row in players:grouped[(row['game_id'],row['team'])].append(row)
    audits_by={(x['game_id'],x['team']):x for x in audits}
    if len(audits_by)!=len(audits):raise ValueError('duplicate team audit')
    selected=[];reasons=Counter()
    for g in pool:
        eligible=True
        for i,team in enumerate(g['teams']):
            key=(g['game_id'],team);rows=grouped.get(key,[]);a=audits_by.get(key)
            if a is None or not rows or len({str(p['athlete_id']) for p in rows})!=len(rows):reasons['missing_or_duplicate_player_identity']+=1;eligible=False;continue
            if a['minutes_status'] not in ('exact_match','display_precision_consistent') or sum(a['unattributed'].values())!=0:reasons['team_minutes_or_unattributed']+=1;eligible=False;continue
            played=[p for p in rows if (p['minutes_seconds_displayed'] or 0)>0]
            if any(not p['accounting_ok'] for p in played):reasons['player_box_mismatch']+=1;eligible=False;continue
            aa=np.sum([p['hard_attempts'] for p in played],axis=0);mm=np.sum([p['hard_makes'] for p in played],axis=0)
            if not np.array_equal(aa,g['sides'][i]['attempts']) or not np.array_equal(mm,g['sides'][i]['makes']):reasons['player_shots_not_team_totals']+=1;eligible=False
        if eligible:selected.append(g)
    return selected,grouped,reasons

def build_day_state(day,pool,clean_pool,grouped):
    allgames=prior.window(pool,day);clean=prior.window(clean_pool,day);teamgames=defaultdict(list)
    for g in allgames:
        for t in g['teams']:teamgames[t].append(g)
    cleanteam=defaultdict(list);states={};league_a=np.zeros(9);league_m=np.zeros(9);league_minutes=0.
    for g in clean:
        for team in g['teams']:
            cleanteam[team].append(g)
            for p in grouped[(g['game_id'],team)]:
                minute=(p['minutes_seconds_displayed'] or 0)/60
                if minute<=0:continue
                if not p['accounting_ok']:raise ValueError('unclean player row')
                ident=str(p['athlete_id']);state=states.setdefault(ident,{'games':0,'minutes':0.,'attempts':np.zeros(9),'makes':np.zeros(9)})
                state['games']+=1;state['minutes']+=minute;state['attempts']+=p['hard_attempts'];state['makes']+=p['hard_makes'];league_a+=p['hard_attempts'];league_m+=p['hard_makes'];league_minutes+=minute
    return {'teamgames':teamgames,'clean_team':cleanteam,'player_states':states,'league_attempts':league_a,'league_makes':league_m,'league_minutes':league_minutes,'prior_games':allgames,'clean_games':clean}

def calculate_match(game,daystate,rotations):
    teams=game['teams'];tg=daystate['teamgames'];ct=daystate['clean_team']
    if any(len(tg[t])<5 or len(ct[t])<5 for t in teams):return None,'insufficient_reconciled_source_games'
    league=np.sum([prior.arr(s) for g in daystate['prior_games'] for s in g['sides']],axis=0);own=[];against=[];p=[]
    for team in teams:
        h=tg[team];pairs=[(prior.arr(g['sides'][g['teams'].index(team)]),prior.arr(g['sides'][1-g['teams'].index(team)])) for g in h];a=np.sum([z[0] for z in pairs],axis=0);d=np.sum([z[1] for z in pairs],axis=0);own.append(a);against.append(d);p.append((a[0]+d[0])/(2*len(h)))
    shared=math.sqrt(p[0]*p[1]);qmeans=[shared*prior.aggregate(own[i],against[1-i],league,False) for i in range(2)];q=prior.probability(qmeans)
    data={'q_recomputed':q,'shared_opportunities':shared,'player_source_game_ids':[[g['game_id'] for g in ct[t]] for t in teams]}
    for key,spatial in [('player_nonspatial',False),('player_spatial',True)]:
        mus=[];cold=[];details=[]
        for i,team in enumerate(teams):
            opponent=teams[1-i];allowed=ct[opponent]
            da=np.sum([np.array(g['sides'][1-g['teams'].index(opponent)]['attempts']) for g in allowed],axis=0);dm=np.sum([np.array(g['sides'][1-g['teams'].index(opponent)]['makes']) for g in allowed],axis=0)
            acc,c,detail=mix_shooting(rotations[i]['minutes'],daystate['player_states'],daystate['league_attempts'],daystate['league_makes'],daystate['league_minutes'],da,dm,spatial)
            family=base.family_details(own[i],against[1-i],league);ft=prior.aggregate(own[i],against[1-i],league,False)[2];mus.append(shared*np.array([family[0]['family_rate']*acc[0],family[1]['family_rate']*acc[1],ft]));cold.append(c);details.append(detail)
        data[key]=prior.probability(mus);data[key+'_expected_points']=[float(np.dot(mu,[2,3,1])) for mu in mus];data[key+'_cold_fraction']=cold;data[key+'_details']=details
    return data,None
def main():
    manifest=json.loads((PLAYER/'manifest.json').read_text(encoding='utf8'));lineup=json.loads((PLAYER/'pregame-lineup-coverage.json').read_text(encoding='utf8'));oldmeta=json.loads((OLD/'manifest.json').read_text(encoding='utf8'))
    checks=[(PLAYER/'player_games.jsonl.gz',manifest['player_games_sha256']),(PLAYER/'team_audit.jsonl.gz',manifest['team_audit_sha256']),(HARD,manifest['pool_sha256']),(OLD/'predictions.jsonl.gz',manifest['predictions_sha256'])]
    if any(hash_file(p)!=expected for p,expected in checks):raise ValueError('input hash mismatch')
    if lineup['strict_verified_pregame_lineup_games']!=0:raise ValueError('strict lineup evidence requires separate implementation')
    pool=read_gzip(HARD);players=read_gzip(PLAYER/'player_games.jsonl.gz');audits=read_gzip(PLAYER/'team_audit.jsonl.gz');selected,grouped,quality_reasons=reconcile_sources(pool,players,audits)
    targets=read_gzip(OLD/'predictions.jsonl.gz');events,dups,conflicts=old.read_events(CACHE);games=[]
    for e in events.values():
        g=old.parse_game(e,CACHE)
        if g:
            competitors=e['competitions'][0]['competitors']; lengths=[len(c.get('linescores',[])) for c in competitors]
            g['expected_team_minutes']=240+25*(lengths[0]-4) if len(lengths)==2 and lengths[0]==lengths[1] and lengths[0]>=4 else None
            games.append(g)
    games.sort(key=lambda g:(g['day'],g['utc'],g['id']));byid={g['id']:g for g in games};team_history=defaultdict(list)
    for g in games:
        for team in g['teams']:team_history[team].append(g)
    for g in pool:
        ref=byid.get(g['game_id'])
        if ref is None or ref['day']!=g['day'] or ref['teams']!=g['teams']:raise ValueError('source identity mismatch')
        for i,s in enumerate(g['sides']):
            box=ref['stats'][i]
            if sum(s['attempts'])!=box.get('fga') or sum(s['makes'])!=box.get('fgm') or s['orb']!=box.get('orb') or s['tov']!=box.get('tov'):raise ValueError('source box mismatch')
    boxes={g['id']:rotation_box(g) for g in games};rows=[];excluded=Counter();exclusion_rows=[]
    for day in sorted({r['day'] for r in targets}):
        state=build_day_state(day,pool,selected,grouped)
        for r in (x for x in targets if x['day']==day):
            g=byid.get(r['espn_id'])
            if not g or [g['day'],g['teams'],g['scores']]!=[r['day'],r['teams'],r['scores']]:raise ValueError('target identity mismatch')
            rotation=[];reason=None
            for t in r['teams']:
                x,why=projected_rotation(t,day,team_history,boxes)
                if why:reason=why;break
                rotation.append(x)
            if not reason:
                result,reason=calculate_match(r,state,rotation)
            if reason:
                excluded[reason]+=1;exclusion_rows.append({'id':r['id'],'espn_id':r['espn_id'],'day':day,'period':r['period'],'reason':reason});continue
            if abs(result['q_recomputed']-r['probabilities']['nonspatial'])>1e-10:raise ValueError('Q reconstruction mismatch')
            probs={'market':r['probabilities']['market'],'prior_outcome_form':r['probabilities']['prior_outcome_form'],'q':r['probabilities']['nonspatial'],'player_nonspatial':result['player_nonspatial'],'player_spatial':result['player_spatial']}
            row={k:r[k] for k in ('id','espn_id','day','teams','scores','odds','home_win','period')};row.update(probabilities=probs,rotation=rotation,player_source_game_ids=result['player_source_game_ids'],shared_opportunities=result['shared_opportunities'],player_nonspatial_expected_points=result['player_nonspatial_expected_points'],player_spatial_expected_points=result['player_spatial_expected_points'],cold_fraction=result['player_spatial_cold_fraction'],zone_details=result['player_spatial_details'],selections={k:{'side':'home' if v>=.5 else 'away','odds':r['odds'][0 if v>=.5 else 1]} for k,v in probs.items()});rows.append(row)
    models=['market','prior_outcome_form','q','player_nonspatial','player_spatial']; results={};previous=json.loads((OLD/'results.json').read_text(encoding='utf8'))
    for period in 'AB':
        subset=[r for r in rows if r['period']==period];original=[r for r in targets if r['period']==period]
        if not subset:results[period]={'coverage':{'prior_rows':len(original),'evaluated':0},'models':{},'monthly':{},'comparisons':{}};continue
        d={'actual_dates':[min(x['day'] for x in subset),max(x['day'] for x in subset)],'coverage':{'price_archive_rows':previous[period]['coverage']['price_archive_rows'],'prior_rows':len(original),'evaluated':len(subset),'excluded':len(original)-len(subset)},'monthly_coverage':{},'models':{k:prior.calc(subset,k) for k in models},'monthly':{},'comparisons':{}}
        for month,cov in previous[period]['monthly_coverage'].items():d['monthly_coverage'][month]=dict(cov,player_evaluated=sum(r['day'].startswith(month) for r in subset))
        for month in sorted({r['day'][:7] for r in subset}):d['monthly'][month]={k:prior.calc([r for r in subset if r['day'].startswith(month)],k) for k in models}
        comparisons=[(a,b) for a in ('player_nonspatial','player_spatial') for b in ('market','prior_outcome_form','q')]+[('player_spatial','player_nonspatial')]
        for a,b in comparisons:
            aa=d['models'][a];bb=d['models'][b];d['comparisons'][a+'_minus_'+b]={'changed_picks':sum((r['probabilities'][a]>=.5)!=(r['probabilities'][b]>=.5) for r in subset),'joint_hit_roi_improvement':aa['hit_rate']>bb['hit_rate'] and aa['roi']>bb['roi'],'paired_date_bootstrap_95':prior.bootstrap(subset,a,b)}
        results[period]=d
    OUT.mkdir(exist_ok=True,parents=True);base.save_gzip(OUT/'predictions.jsonl.gz',rows);base.save_gzip(OUT/'exclusions.jsonl.gz',exclusion_rows)
    (OUT/'results.json').write_text(json.dumps(results,indent=2),encoding='utf8')
    meta={'strict_confirmed_lineup_status':'blocked_no_verified_pre_tipoff_lineup','strict_coverage':lineup,'input_manifest_sha256':hash_file(PLAYER/'manifest.json'),'input_sha256':{p.name:hash_file(p) for p,_ in checks},'original_pool_games':len(pool),'reconciled_source_games':len(selected),'source_team_reconciliation_failures':dict(quality_reasons),'input_coverage':{'scoreboard_valid_games':len(games),'valid_rotation_boxes':sum(v is not None for v in boxes.values())},'target_exclusions':dict(excluded),'prior_training_cutoff_note':'no fitted coefficient; all player and league features are as-of target D-4','scoreboard_duplicate_identical':dups,'scoreboard_conflicts':conflicts,'cold_minute_fraction_mean':float(np.mean([x for r in rows for x in r['cold_fraction']])) if rows else None,'prior_price_sha256':oldmeta['price_sha256'],'protocol_commits':['8277a9f9','95caa270']}
    (OUT/'manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf8');print(json.dumps({'source':len(selected),'excluded':dict(excluded),'coverage':{k:v['coverage'] for k,v in results.items()}},indent=2))
if __name__=='__main__':main()
