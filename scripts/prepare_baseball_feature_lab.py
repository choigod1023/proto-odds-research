"""Build compact pregame feature facts from #406, without fetching production."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
import argparse
import csv
import gzip
import hashlib
import json
import math
import re

import numpy as np

MLB_EN='Los Angeles Dodgers|Arizona Diamondbacks|New York Yankees|San Francisco Giants|Miami Marlins|New York Mets|Seattle Mariners|Cleveland Guardians|Cincinnati Reds|Pittsburgh Pirates|Los Angeles Angels|Kansas City Royals|Minnesota Twins|Texas Rangers|Philadelphia Phillies|Houston Astros|Chicago White Sox|San Diego Padres|Colorado Rockies|Boston Red Sox|Baltimore Orioles|St. Louis Cardinals|Toronto Blue Jays|Chicago Cubs|Milwaukee Brewers|Washington Nationals|Atlanta Braves|Tampa Bay Rays|Detroit Tigers|Athletics'.split('|')
MLB_KO='LA다저스 애리조나 뉴욕양키스 샌프란시스코 마이애미 뉴욕메츠 시애틀 클리블랜드 신시내티 피츠버그 LA에인절스 캔자스시티 미네소타 텍사스 필라델피아 휴스턴 시카고W 샌디에이고 콜로라도 보스턴 볼티모어 세인트루이스 토론토 시카고컵스 밀워키 워싱턴 애틀랜타 탬파베이 디트로이트 애슬레틱스'.split()


def read(path):
    if path.exists():text=path.read_text(encoding='utf8')
    else:
        with gzip.open(str(path)+'.gz','rt',encoding='utf8') as f:text=f.read()
    return [json.loads(s) for s in text.splitlines() if s]


def num(value,default=0.):
    try:
        x=float(value)
        return x if math.isfinite(x) else default
    except (ValueError,TypeError):return default


def canonical_map(team_map):
    clean=lambda s:re.sub(r'\s+','',str(s)).casefold()
    out={clean(k):v for k,v in team_map['MLB'].items()}
    out.update({clean(k):v for k,v in zip(MLB_EN,MLB_KO)})
    out.update({clean(k):k for k in MLB_KO})
    out[clean('오클랜드')]='애슬레틱스';out[clean('Oakland Athletics')]='애슬레틱스'
    return lambda s:out.get(clean(s))


def history_prices(path,canon):
    groups=defaultdict(set);excluded=Counter()
    with path.open(encoding='utf8') as f:
        for r in csv.DictReader(f):
            if r['league']!='MLB' or r['market_family']!='승패' or r['n_way']!='2' or r['is_void'].lower()=='true':continue
            h=re.fullmatch(r'(.+?)\s+(\d+)',r['home'].strip());a=re.fullmatch(r'(\d+)\s+(.+)',r['away'].strip())
            dt=re.search(r'(\d{2})\.(\d{2}).*?(\d{2}):(\d{2})',r['date_text'])
            if not h or not a or not dt:excluded['unparsed_price_row']+=1;continue
            home,away=canon(h[1]),canon(a[2])
            if not home or not away:excluded['unknown_price_team']+=1;continue
            year=int(r['year'])-int(r['round']=='1' and dt[1]=='12')
            day=f'{year}-{dt[1]}-{dt[2]}'
            odds=tuple(float(x) for x in r['odds'].split(','))
            if len(odds)!=2 or not all(math.isfinite(x) and x>1 for x in odds):continue
            groups[(day,home,away)].add((odds,int(h[2]),int(a[1])))
    return groups,excluded


def pregame_features(row,savant):
    """Only player seasonStats in pregame snapshots and previous-season Savant."""
    year=int(row['start_at'][:4])-1
    features=[];sides=[]
    for side in ['home','away']:
        p=row['sides'][side]['starter']['pitching'];sp=row['sides'][side]['starter']
        outs=num(p.get('outs'));bf=num(p.get('battersFaced'));ip=outs/3
        era=(27*num(p.get('earnedRuns'))+150*4.3)/(outs+150)
        kbb=(num(p.get('strikeOuts'))-num(p.get('baseOnBalls'))+100*.14)/(bf+100)
        fip_component=(13*num(p.get('homeRuns'))+3*(num(p.get('baseOnBalls'))+num(p.get('hitByPitch')))-2*num(p.get('strikeOuts')))/(ip+50)
        pk=(num(p.get('strikeOuts'))+100*.22)/(bf+100)
        pbb=(num(p.get('baseOnBalls'))+num(p.get('hitByPitch'))+100*.09)/(bf+100)
        phr=(num(p.get('homeRuns'))+100*.03)/(bf+100)
        pcontact=(num(p.get('hits'))+num(p.get('baseOnBalls'))+num(p.get('hitByPitch'))+100*.32)/(bf+100)
        gb=(num(p.get('groundOuts'))+30*.45)/(num(p.get('groundOuts'))+num(p.get('airOuts'))+30)
        endurance=(outs+5*16)/(num(p.get('gamesStarted'))+5)
        efficiency=(num(p.get('numberOfPitches'))+50*5)/(outs+50)
        def expected(player,kind,field,prior,shrink):
            r=savant[(year,kind)].get(player['id'],{});pa=num(r.get('pa'))
            return (pa*num(r.get(field),prior)+shrink*prior)/(pa+shrink)
        px=expected(sp,'pitcher','est_woba',.32,100);xe=expected(sp,'pitcher','xera',4.3,100)
        bats=[]
        for batter in row['sides'][side]['lineup']:
            b=batter['batting'];ab=num(b.get('atBats'));walk=num(b.get('baseOnBalls'));hbp=num(b.get('hitByPitch'))
            pa=ab+walk+hbp+num(b.get('sacFlies'));hits=num(b.get('hits'));tb=num(b.get('totalBases'))
            obp=(hits+walk+hbp+150*.32)/(pa+150);slg=(tb+150*.41)/(ab+150);avg=(hits+150*.24)/(ab+150)
            kr=(num(b.get('strikeOuts'))+150*.22)/(pa+150);wr=(walk+150*.085)/(pa+150)
            xw=expected(batter,'batter','est_woba',.32,150)
            xb=expected(batter,'batter','est_ba',.24,150);xs=expected(batter,'batter','est_slg',.41,150)
            bats.append([obp,slg,slg-avg,kr,wr,xw,avg-xb,slg-xs])
        b=np.array(bats);weights=np.array([1.15,1.12,1.09,1.06,1.03,1.,.97,.94,.91]);weights/=weights.sum()
        ratings=dict(era=era,kbb=kbb,fip=fip_component,k=pk,bb=pbb,hr=phr,contact=pcontact,gb=gb,
            endurance=endurance,efficiency=efficiency,xw_allowed=px,xera=xe,obp=b[:,0].mean(),slg=b[:,1].mean(),
            iso=b[:,2].mean(),batk=b[:,3].mean(),batbb=b[:,4].mean(),xw=b[:,5].mean(),
            weighted_obp=float(weights@b[:,0]),weighted_slg=float(weights@b[:,1]),
            top4_power=b[:4,2].mean(),bottom5_obp=b[4:,0].mean(),
            batting_depth=float(b[:,0].min()),bat_dispersion=float(b[:,0].std()),
            average_vs_prior_xba=b[:,6].mean(),slg_vs_prior_xslg=b[:,7].mean(),
            era_vs_prior_xera=era-xe,bf=bf,bat_pa_mean=np.mean([num(x['batting'].get('plateAppearances')) for x in row['sides'][side]['lineup']]))
        sides.append(ratings)
    h,a=sides
    names=['era','kbb','xw_allowed','xera','obp','slg','xw','fip','k','bb','hr','contact','gb','endurance','efficiency',
           'iso','batk','batbb','weighted_obp','weighted_slg','top4_power','bottom5_obp','batting_depth','bat_dispersion',
           'average_vs_prior_xba','slg_vs_prior_xslg','era_vs_prior_xera']
    out={n:float(h[n]-a[n]) for n in names}
    out['legacy_log_era']=math.log(a['era']/h['era'])
    logit=lambda p:math.log(max(.02,min(.98,p))/(1-max(.02,min(.98,p))))
    out['log5_matchup']=(logit(h['obp'])+logit(a['contact']))-(logit(a['obp'])+logit(h['contact']))
    out['strikeout_matchup']=h['batk']*a['k']-a['batk']*h['k']
    out['power_matchup']=h['iso']*a['hr']-a['iso']*h['hr']
    out['discipline_matchup']=h['batbb']*a['bb']-a['batbb']*h['bb']
    out['pitcher_run_environment']=out['fip']*(h['era']+a['era'])/2
    out['power_run_environment']=out['iso']*(h['hr']+a['hr'])/2
    game_month=datetime.fromisoformat(row['start_at']).astimezone(timezone(timedelta(hours=9))).month
    groups=dict(early_season=int(game_month<=4),novice_starter=int(min(h['bf'],a['bf'])<150),
        low_batter_sample=int(min(h['bat_pa_mean'],a['bat_pa_mean'])<100),postseason=int(row['game_type']!='R'))
    return out,groups


def build(source,prior,archive,output,team_map):
    output.mkdir(parents=True,exist_ok=True)
    rows=[r for r in read(source/'mlb-snapshots.jsonl') if r['accepted']]
    savant={(y,k):{int(r['player_id']):r for r in json.loads((source/f'savant-{y}-{k}.json').read_text(encoding='utf8'))['rows']}
        for y in [2023,2024,2025] for k in ['batter','pitcher']}
    prices,excluded=history_prices(archive,canonical_map(json.loads(team_map.read_text(encoding='utf8'))))
    canon=canonical_map(json.loads(team_map.read_text(encoding='utf8')))
    official=read(prior/'mlb-official-final.jsonl');pairs=Counter((datetime.fromisoformat(r['start_at'].replace('Z','+00:00')).astimezone(timezone(timedelta(hours=9))).date().isoformat(),canon(r['home']),canon(r['away'])) for r in official)
    facts=[]
    for r in rows:
        x,g=pregame_features(r,savant)
        fact=dict(gamePk=r['gamePk'],role=r['role'],date=datetime.fromisoformat(r['start_at']).astimezone(timezone(timedelta(hours=9))).date().isoformat(),
            start_at=r['start_at'],snapshot_at=r['snapshot_at'],source_url=r['url'],home=r['home'],away=r['away'],
            hg=r['home_score'],ag=r['away_score'],features=x,groups=g)
        if r.get('target'):fact['target']=r['target']
        k=(fact['date'],canon(r['home']),canon(r['away']))
        if pairs[k]!=1:excluded['ambiguous_same_day_price_fixture']+=1
        elif len(prices.get(k,[]))==1:
            odds,hg,ag=next(iter(prices[k]))
            if (hg,ag)==(r['home_score'],r['away_score']):fact['historical_prices']=list(odds)
            else:excluded['historical_price_score_mismatch']+=1
        else:excluded['missing_or_multiple_historical_prices']+=1
        facts.append(fact)
    (output/'mlb-feature-facts.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in facts),encoding='utf8',newline='\n')
    cohort=read(prior/'targets.jsonl')
    (output/'baseball-common-cohort.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in cohort),encoding='utf8',newline='\n')
    meta=dict(n=len(facts),by_role=dict(Counter(r['role'] for r in facts)),price_matched_by_role=dict(Counter(r['role'] for r in facts if 'historical_prices' in r)),excluded=dict(excluded),
        sources={str(p.name):hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n') if p.suffix!='.gz' else p.read_bytes()).hexdigest() for p in [source/'mlb-snapshots.jsonl.gz',archive,prior/'targets.jsonl']})
    (output/'inputs.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(meta,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['source','prior','archive','output','team-map']:p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();build(a.source,a.prior,a.archive,a.output,a.team_map)
