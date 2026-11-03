"""Preregistered offline KBO score model; never reads future target labels."""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import math
import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

FEATURES=('home_team_strength','away_team_strength','home_rest_days','away_rest_days','park_factor',
          'home_lineup_strength','away_lineup_strength','home_starter_strength','away_starter_strength',
          'home_bullpen_load','away_bullpen_load')
MODES={'team':(), 'starter':('starter',), 'lineup':('lineup',), 'starter_lineup':('lineup','starter'), 'bullpen':('bullpen',),
       'player':('lineup','starter','bullpen')}
RIDGE=(1.,10.,100.)
ROW_KEYS={'fixture_id','league','season','kickoff_at','decision_at','home_id','away_id','market','availability_tier','features','sources'}
HISTORY_FEATURES=set(FEATURES)-{'park_factor'}


def stamp(value, required=True):
    if value is None and not required:return None
    try: d=datetime.fromisoformat(value.replace('Z','+00:00'))
    except (AttributeError,TypeError,ValueError) as e:raise ValueError('invalid timestamp') from e
    if d.tzinfo is None or d.utcoffset() is None:raise ValueError('timezone required')
    return d.astimezone(timezone.utc)


def validate_rows(rows, *, allow_retrospective_dates=False):
    seen=set()
    for row in rows:
        extra={'event_date','decision_time_basis','identity_basis'} if allow_retrospective_dates else set()
        if not ROW_KEYS<=set(row) or set(row)-ROW_KEYS-extra:raise ValueError('unexpected/missing row keys: target results forbidden')
        if row['league']!='KBO' or not all(isinstance(row[k],str) and row[k].strip() for k in ('fixture_id','home_id','away_id')) or row['home_id']==row['away_id']:raise ValueError('invalid fixture identity')
        if row['fixture_id'] in seen:raise ValueError('duplicate fixture')
        seen.add(row['fixture_id'])
        tier=row['availability_tier'];retro=allow_retrospective_dates and tier=='retrospective_unproven'
        kickoff=stamp(row['kickoff_at'],required=not retro);decision=stamp(row['decision_at'],required=not retro)
        if tier not in ('point_in_time_verified','retrospective_unproven'):raise ValueError('invalid tier')
        if retro:
            event=date.fromisoformat(row.get('event_date',''))
            if int(row['season'])!=event.year or (kickoff and kickoff.astimezone(timezone(timedelta(hours=9))).date()!=event):raise ValueError('invalid event date')
            if (kickoff is None)!=(decision is None):raise ValueError('partial kickoff/decision')
        else:
            event=kickoff.date()
            if int(row['season'])!=kickoff.year:raise ValueError('invalid season')
        if decision is not None and decision>kickoff-timedelta(minutes=30):raise ValueError('invalid chronology')
        if not isinstance(row['sources'],list):raise ValueError('sources must be a list')
        sources={}
        for s in row['sources']:
            if not isinstance(s,dict) or not {'source_id','path_or_url','sha256','received_at','availability_basis'}<=set(s):raise ValueError('invalid source')
            sid=s['source_id'];sha=s['sha256']
            if not isinstance(sid,str) or not sid or sid in sources or not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('source identity/hash')
            received=stamp(s['received_at'],required=False)
            sources[sid]=(s,received)
        fs=row['features']
        if not isinstance(fs,dict) or set(fs)-set(FEATURES):raise ValueError('unknown feature or result')
        for name,item in fs.items():
            if not isinstance(item,dict) or not {'value','available_at','availability_basis','source_id'}<=set(item):raise ValueError('invalid feature')
            if item['value'] is None:continue
            if type(item['value']) not in (float,int) or not math.isfinite(item['value']):raise ValueError('invalid feature value')
            if item['source_id'] not in sources or not isinstance(item['availability_basis'],str) or not item['availability_basis'].strip():raise ValueError('feature source missing')
            available=stamp(item['available_at'],required=False);source,received=sources[item['source_id']]
            if available is not None and (decision is None or available>decision):raise ValueError('future feature')
            if tier=='point_in_time_verified' and (available is None or received is None or received>decision or not source['availability_basis']):raise ValueError('unverified feature')
            if name in HISTORY_FEATURES:
                completed=stamp(item.get('history_max_completed_at'),required=False);ids=item.get('history_fixture_ids')
                if not isinstance(ids,list) or not ids or row['fixture_id'] in ids:raise ValueError('history fixture leakage')
                if retro and completed is None:
                    latest=date.fromisoformat(item.get('history_latest_date',''))
                    if item.get('availability_basis')!='prior_calendar_day_assumed_completed_not_received' or latest>=event:raise ValueError('date-only history leakage')
                elif completed is None or decision is None or completed>=decision or (available is not None and completed>available) or (tier=='point_in_time_verified' and received is not None and completed>received):raise ValueError('history completion or fixture leakage')
        market=row['market']
        if market is not None:
            if not isinstance(market,dict) or not {'market_id','kind','selections','odds_observed_at','settlement_rule'}<=set(market):raise ValueError('invalid market')
            kind=market['kind'];expected={'home','away'} if kind=='full_game_2way' else {'home','draw','away'} if kind=='full_game_3way' else set()
            if not isinstance(market['market_id'],str) or not market['market_id'] or not expected or not isinstance(market['selections'],dict) or set(market['selections'])!=expected:raise ValueError('invalid market product')
            if market['settlement_rule'] not in (('draw_refund','draw_void_refund') if retro and len(expected)==2 else ('draw_refund',) if len(expected)==2 else ('draw_selection',)):raise ValueError('invalid draw rule')
            for price in market['selections'].values():
                try: val=Decimal(str(price))
                except InvalidOperation as e:raise ValueError('bad price') from e
                if not val.is_finite() or val<=1:raise ValueError('bad price')
            observed=stamp(market['odds_observed_at'],required=False)
            if observed and (decision is None or observed>decision):raise ValueError('late odds')
    return rows


def validate_labels(labels, *, allow_retrospective_dates=False):
    seen=set()
    for label in labels:
        required={'fixture_id','status','home_score','away_score','completed_at','received_at','revision'}
        if not required<=set(label) or not isinstance(label['fixture_id'],str) or label['fixture_id'] in seen or (not label['revision'] and not (allow_retrospective_dates and label.get('revision_basis')=='archive_content_hash_not_official_revision')):raise ValueError('duplicate or incomplete label')
        seen.add(label['fixture_id'])
        if label['status'] not in ('final','void','pending'):raise ValueError('unknown label status')
        if label['status']=='final':
            if any(type(label[k]) is not int or label[k]<0 for k in ('home_score','away_score')):raise ValueError('invalid final score')
            end=stamp(label['completed_at'],required=False);received=stamp(label['received_at'],required=False)
            if end is None:
                if not allow_retrospective_dates or label.get('availability_basis')!='retrospective_prior_calendar_day_assumption' or received is not None:raise ValueError('missing completion evidence')
                date.fromisoformat(label['assumed_completed_date'])
            elif received is None and label.get('availability_basis')!='retrospective_completion_assumption':raise ValueError('missing label receipt basis')
            if received is not None and (end is None or received<end):raise ValueError('label received before completion')
    return labels


def split(rows,labels, *, allow_retrospective_dates=False):
    validate_rows(rows,allow_retrospective_dates=allow_retrospective_dates);validate_labels(labels,allow_retrospective_dates=allow_retrospective_dates)
    if len({r['availability_tier'] for r in rows})>1:raise ValueError('availability tiers must be evaluated separately')
    lookup={r['fixture_id']:r for r in labels};out={}
    for name,years in [('train',range(1,2025)),('validation',(2025,)),('test',(2026,))]:
        subset=sorted([r for r in rows if int(r['season']) in years],key=lambda r:(r.get('event_date') or r['kickoff_at'],r['fixture_id']))
        out[name]=subset
    if any(len(out[k])<n for k,n in [('train',100),('validation',30),('test',30)]):raise ValueError('insufficient preregistered season split')
    if any(r['fixture_id'] in lookup for r in out['test']):raise ValueError('test labels forbidden before prediction freeze')
    for later,earlier in [('validation','train'),('test','train'),('test','validation')]:
        retro=allow_retrospective_dates and out[later][0]['availability_tier']=='retrospective_unproven'
        first=min(date.fromisoformat(r['event_date']) for r in out[later]) if retro else min(stamp(r['decision_at']) for r in out[later]);cut=first-timedelta(days=7)
        for row in out[earlier]:
            label=lookup.get(row['fixture_id'])
            if label is None or label['status']!='final':raise ValueError('missing training label at fold boundary')
            if retro and label['completed_at'] is None:
                assumed=date.fromisoformat(label['assumed_completed_date'])
                if assumed!=date.fromisoformat(row['event_date']) or assumed>cut:raise ValueError('late or inconsistent date-only label')
            else:
                if stamp(label['completed_at'])<stamp(row['kickoff_at']):raise ValueError('label before kickoff')
                received=stamp(label['received_at'],required=False)
                if received is None and row['availability_tier']!='retrospective_unproven':raise ValueError('verified training label receipt missing')
                boundary=(received or stamp(label['completed_at']))
                if (boundary.date() if retro else boundary)>cut:raise ValueError('missing/late training label at fold boundary')
    return out


def oriented(row,side,mode):
    """Symmetric feature columns, one home and one away scoring equation."""
    own=side;opp='away' if side=='home' else 'home'
    names=[f'{own}_team_strength',f'{opp}_team_strength',f'{own}_rest_days',f'{opp}_rest_days','park_factor']
    if 'lineup' in MODES[mode]:names += [f'{own}_lineup_strength']
    if 'starter' in MODES[mode]:names += [f'{opp}_starter_strength']
    if 'bullpen' in MODES[mode]:names += [f'{opp}_bullpen_load']
    values=[]
    for name in names:
        item=row['features'].get(name);values.append(None if item is None else item['value'])
    return values

@dataclass
class ScoreModel:
    mode:str
    teams:tuple
    mean:np.ndarray
    scale:np.ndarray
    beta:np.ndarray
    alpha:float
    retrospective:bool=False


def _raw(rows,mode,teams):
    if mode not in MODES:raise ValueError('unknown model mode')
    values=[];metadata=[];unknown=[];index={name:i for i,name in enumerate(teams)}
    for row in rows:
        for side in ('home','away'):
            own=row[side+'_id'];other=row[('away' if side=='home' else 'home')+'_id']
            cat=np.zeros(2*len(teams));known_own=own in index;known_other=other in index
            if known_own:cat[index[own]]=1
            if known_other:cat[len(teams)+index[other]]=1
            values.append(oriented(row,side,mode));metadata.append(np.r_[1.,float(side=='home'),cat]);unknown.append(not known_own or not known_other)
    return np.array(values,dtype=float),np.array(metadata,dtype=float),unknown


def _matrix(raw,meta,mean,scale):
    missing=np.isnan(raw);filled=np.where(missing,mean,raw)
    return np.c_[meta,(filled-mean)/scale,missing.astype(float)]


def fit(rows,labels,mode,alpha, *, allow_retrospective_dates=False):
    validate_rows(rows,allow_retrospective_dates=allow_retrospective_dates);validate_labels(labels,allow_retrospective_dates=allow_retrospective_dates)
    if not rows or alpha not in RIDGE:raise ValueError('bad training inputs')
    byid={r['fixture_id']:r for r in labels};teams=tuple(sorted({r[k] for r in rows for k in ('home_id','away_id')}))
    raw,meta,_=_raw(rows,mode,teams);observed=~np.isnan(raw);counts=observed.sum(axis=0);mean=np.divide(np.where(observed,raw,0).sum(axis=0),counts,out=np.zeros(raw.shape[1]),where=counts>0)
    var=np.divide(np.where(observed,(raw-mean)**2,0).sum(axis=0),counts,out=np.zeros(raw.shape[1]),where=counts>0);scale=np.sqrt(var);scale[scale<1e-12]=1.
    x=_matrix(raw,meta,mean,scale);y=[]
    for row in rows:
        label=byid.get(row['fixture_id'])
        if not label or label['status']!='final':raise ValueError('training label unavailable or invalid')
        if label['completed_at'] is None:
            if not allow_retrospective_dates or row['availability_tier']!='retrospective_unproven' or label['assumed_completed_date']!=row['event_date']:raise ValueError('invalid date-only label')
        elif stamp(label['completed_at'])<stamp(row['kickoff_at']):raise ValueError('training label before kickoff')
        if label['received_at'] is None and row['availability_tier']!='retrospective_unproven':raise ValueError('verified training label receipt missing')
        y.extend((label['home_score'],label['away_score']))
    y=np.array(y,dtype=float)
    def obj(beta):
        linear=x@beta
        if np.max(np.abs(linear))>50:return 1e100,np.zeros_like(beta)
        mu=np.exp(linear);pen=beta.copy();pen[0]=0
        return float(np.sum(mu-y*linear)+alpha*np.dot(pen,pen)/2),x.T@(mu-y)+alpha*pen
    beta=np.zeros(x.shape[1]);beta[0]=math.log(max(float(y.mean()),.1))
    result=minimize(obj,beta,jac=True,method='L-BFGS-B',options={'maxiter':2000,'ftol':1e-11})
    if not result.success or not np.isfinite(result.x).all():raise ValueError('fit did not converge')
    return ScoreModel(mode,teams,mean,scale,result.x,float(alpha),allow_retrospective_dates)


def distribution(home_rate,away_rate):
    if not all(math.isfinite(x) and x>0 for x in (home_rate,away_rate)):raise ValueError('invalid rate')
    max_goal=max(8,int(math.ceil(max(home_rate,away_rate))))
    while max_goal<10000 and poisson.sf(max_goal,home_rate)+poisson.sf(max_goal,away_rate)>1e-10:max_goal+=1
    tail=float(poisson.sf(max_goal,home_rate)+poisson.sf(max_goal,away_rate))
    if tail>1e-10:raise ValueError('tail did not converge')
    h=poisson.pmf(np.arange(max_goal+1),home_rate);a=poisson.pmf(np.arange(max_goal+1),away_rate);joint=np.outer(h,a)
    probs=np.array([np.tril(joint,-1).sum(),np.trace(joint),np.triu(joint,1).sum()]);probs/=probs.sum()
    return {'home':float(probs[0]),'draw':float(probs[1]),'away':float(probs[2]),'tail_upper_bound':tail}


def predict(model,rows):
    validate_rows(rows,allow_retrospective_dates=model.retrospective)
    if not rows:return []
    raw,meta,unknown=_raw(rows,model.mode,model.teams);eta=_matrix(raw,meta,model.mean,model.scale)@model.beta
    if not np.isfinite(eta).all() or np.any(np.abs(eta)>20):raise ValueError('unstable predicted rate')
    rates=np.exp(eta);out=[]
    for i,row in enumerate(rows):
        dist=distribution(float(rates[2*i]),float(rates[2*i+1]));non_draw=dist['home']+dist['away']
        out.append({'fixture_id':row['fixture_id'],'home_rate':float(rates[2*i]),'away_rate':float(rates[2*i+1]),'probabilities_3way':dist,'probabilities_2way':{'home':dist['home']/non_draw,'away':dist['away']/non_draw},'unknown_team':bool(unknown[2*i] or unknown[2*i+1]),'missing_features':[name for name in FEATURES if row['features'].get(name,{}).get('value') is None]})
    return out
