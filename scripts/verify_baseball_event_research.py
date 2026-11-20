"""Independent saved-coefficient, Decimal payout and source-workload replay."""
from collections import defaultdict
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
import argparse,gzip,hashlib,json,math


def read(path):
    opener=gzip.open if path.suffix=='.gz' else open
    with opener(path,'rt',encoding='utf8') as f:return [json.loads(s) for s in f if s.strip()]


def dt(value):return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)


def probability(config,row,base):
    if config is None:return base
    z=[max(-6,min(6,row['features'][n]/s)) for n,s in zip(config['names'],config['scale'])]
    eta=math.log(base/(1-base))+sum(x*b for x,b in zip(z,config['beta']))
    return 1/(1+math.exp(-eta)) if eta>=0 else math.exp(eta)/(1+math.exp(eta))


def settle(rows,ps):
    wins=pushes=0;profit=Decimal(0)
    for r,p in zip(rows,ps):
        if r['hg']==r['ag']:pushes+=1;continue
        home=p>=.5;correct=home==(r['hg']>r['ag']);wins+=int(correct)
        t=r.get('target');odds=t['prices'] if t else r['historical_prices'];order=t['order'] if t else ['home','away']
        profit+=Decimal(str(odds[order.index('home' if home else 'away')]))-1 if correct else Decimal(-1)
    return dict(n=len(rows),wins=wins,pushes=pushes,roi=float(profit/len(rows)) if rows else None,net_krw=int(profit*10000))


def check_metrics(metric,rows,ps):
    values=settle(rows,ps)
    for k,v in values.items():
        if not rows and k not in metric:continue
        if isinstance(v,float):assert abs(metric[k]-v)<1e-12,(k,metric[k],v)
        else:assert metric[k]==v,(k,metric[k],v)


def verify(folder):
    report=json.loads((folder/'event-results.json').read_text(encoding='utf8'))
    facts=read(folder/'event-features.jsonl');rows=read(folder/'event-predictions.jsonl')
    assert len(facts)==390 and len(rows)==116
    assert hashlib.sha256((folder/'event-features.jsonl').read_bytes().replace(b'\r\n',b'\n')).hexdigest()==report['input_sha256']
    models=report['models'];selected=min(models,key=lambda n:(-models[n]['validation']['accuracy'],models[n]['validation']['brier'],n))
    assert selected==report['selected']
    ps_by_model={};replayed=settlements=0
    for name,m in models.items():
        ps=[probability(m['final_fit'],r,r['existing91_probability']) for r in rows]
        assert all(abs(p-r['probabilities'][name])<1e-12 for p,r in zip(ps,rows));replayed+=len(rows)
        check_metrics(m['evaluation_91'],rows,ps)
        check_metrics(m['evaluation_market'],rows,[probability(m['final_fit'],r,r['market_probability']) for r in rows]);settlements+=2
        ps_by_model[name]=ps
    for period,p in report['periods'].items():
        subset=[r for r in rows if period=='all' or (period=='2025_postseason' and r['date'].startswith('2025')) or
            (period=='2026_before_july' and '2026-01-01'<=r['date']<'2026-07-01') or (period=='2026_july_onward' and r['date']>='2026-07-01')]
        check_metrics(p['existing91'],subset,[r['existing91_probability'] for r in subset])
        check_metrics(p['selected91'],subset,[r['probabilities'][selected] for r in subset]);settlements+=2
    games={};all_games={}
    for f in (folder/'sources').glob('schedule-*.gz'):
        for g in read(f)[0]['games']:
            all_games[g['gamePk']]=g
            info=g.get('gameInfo',{})
            if g.get('status',{}).get('codedGameState')=='F' and not g.get('resumeDate') and not g.get('resumedFrom') and info.get('firstPitch') and info.get('gameDurationMinutes'):
                g['bound']=dt(info['firstPitch'])+timedelta(minutes=float(info['gameDurationMinutes'])+float(info.get('delayDurationMinutes',0))+60)
                games[g['gamePk']]=g
    by_team=defaultdict(list);score_totals=defaultdict(float)
    repairs=list((folder/'sources').glob('repair-*.gz'));repaired_ids={read(f)[0]['gamePk'] for f in repairs}
    for f in list((folder/'sources').glob('logs-*.gz'))+repairs:
        for r in read(f)[0].get('rows',[]):
            if not f.name.startswith('repair-') and r['gamePk'] in repaired_ids:continue
            score_totals[r['gamePk'],r['team'],r['kind']]+=r['stat'].get('runs',0)
            if r['kind']=='pitching' and r['stat'].get('gamesStarted')==0 and r['gamePk'] in games:by_team[r['team']].append(r)
    score_checks=0
    for pk,g in all_games.items():
        if g.get('status',{}).get('codedGameState')!='F':continue
        for side in ['home','away']:
            other='away' if side=='home' else 'home';tid=g['teams'][side]['team']['id']
            for kind in ['hitting','pitching']:
                expected=g['teams'][side if kind=='hitting' else other].get('score')
                if expected is None:continue
                assert score_totals.get((pk,tid,kind))==expected,(pk,tid,kind,expected)
                score_checks+=1
    announcements={(r['gamePk'],r['minutes_before']):r for r in read(folder/'announcements.jsonl.gz')}
    workload_checks=0
    for r in facts:
        cutoff=dt(r['proof']['cutoff_at']);g=games[r['gamePk']];day=datetime.fromisoformat(g['officialDate']).date()
        assert r['gamePk'] not in r['proof']['source_games']
        for pk in r['proof']['source_games']:assert games[pk]['bound']<cutoff
        sides=[]
        for side in ['home','away']:
            proof=r['proof']['sides'][side];a=announcements[(r['gamePk'],10)]
            assert a['sides'][side]['starter_kind']=='pregame_box_listed' and dt(a['observed_at'])<=cutoff
            assert proof['lineup']==a['sides'][side]['lineup']
            relief=[x for x in by_team[proof['team']] if x['date']<g['officialDate'] and games[x['gamePk']]['bound']<cutoff and dt(games[x['gamePk']]['gameInfo']['firstPitch'])>=cutoff-timedelta(days=28)]
            yesterday=(day-timedelta(days=1)).isoformat();d2=(day-timedelta(days=2)).isoformat();d3=(day-timedelta(days=3)).isoformat()
            y=[x for x in relief if x['date']==yesterday];three=[x for x in relief if x['date'] in [yesterday,d2,d3]]
            ids={x['player'] for x in y};ids2={x['player'] for x in relief if x['date']==d2};rank=defaultdict(lambda:[0.,0.])
            for x in relief:rank[x['player']][0]+=x['stat'].get('saves',0)+x['stat'].get('holds',0);rank[x['player']][1]+=x['stat'].get('outs',0)
            top=sorted(rank,key=lambda pid:(-rank[pid][0],-rank[pid][1],pid))[:3]
            assert top==proof['top3_past_save_hold_ids']
            sides.append(dict(relief_pitches1=sum(x['stat'].get('numberOfPitches',0) for x in y),
                relief_pitches3=sum(x['stat'].get('numberOfPitches',0) for x in three),relief_consecutive=len(ids&ids2),relief_top3_used1=sum(x in ids for x in top)))
        for key in sides[0]:assert r['features'][key]==sides[0][key]-sides[1][key];workload_checks+=1
    flips=[];config=models[selected]['final_fit']
    for r in rows:
        if (r['existing91_probability']>=.5)==(r['probabilities'][selected]>=.5):continue
        flips.append(dict(gamePk=r['gamePk'],date=r['date'],home=r['target']['home'],away=r['target']['away'],hg=r['hg'],ag=r['ag'],
            existing91_probability=r['existing91_probability'],new_probability=r['probabilities'][selected],
            logit_terms={n:max(-6,min(6,r['features'][n]/s))*b for n,s,b in zip(config['names'],config['scale'],config['beta'])},
            interpretation='algebraic contribution of fitted terms; not a causal estimate'))
    (folder/'selected-flip-explanations.json').write_text(json.dumps(flips,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    verification=dict(ok=True,fixtures=len(rows),probabilities=replayed,settlements=settlements,raw_workload_checks=workload_checks,
                      changed_picks=len(flips),official_team_game_score_checks=score_checks,score_mismatches=0)
    (folder/'verification.json').write_text(json.dumps(verification,indent=2)+'\n',encoding='utf8',newline='\n')
    manifests={str(p.relative_to(folder)).replace('\\','/'):hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n') if p.suffix in ['.json','.jsonl','.md'] else p.read_bytes()).hexdigest()
        for p in folder.rglob('*') if p.is_file() and p.name!='verified-sha256.json' and not p.name.startswith(('announcement-','confirmation-'))}
    (folder/'verified-sha256.json').write_text(json.dumps(manifests,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(verification))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--folder',type=Path,required=True);verify(p.parse_args().folder)
