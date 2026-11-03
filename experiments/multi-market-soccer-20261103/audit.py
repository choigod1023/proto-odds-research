"""Offline market availability audit; settled archive, not pregame evidence."""
import argparse, hashlib, json, math, os, re, sys
from collections import Counter, defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
import pandas as pd
from accuracy_formula_lab import load_unique_market_rows
from matches import load_matches

EXPECTED='9ffddb2dcbc4297c32e6ad3826f392e1842e52e10b509e4cbe47eed1f84b9da4'
def settlement(family,line,hs,aw,n):
    if family=='언더오버' and n==2:
        d=hs+aw-line
        return None if d==0 else (0 if d<0 else 1)
    if family=='핸디캡' and n==3:
        d=hs+line-aw
        return 0 if d>0 else 1 if d==0 else 2
    if family=='승무패' and n==3:
        return 0 if hs>aw else 1 if hs==aw else 2
    return None

def main():
    p=argparse.ArgumentParser();p.add_argument('--raw',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    if os.environ.get('PROODD_DB_PATH'): raise RuntimeError('offline DB required')
    path=Path(a.raw); sha=hashlib.sha256(path.read_bytes()).hexdigest()
    if sha!=EXPECTED: raise ValueError('unexpected source hash')
    records,quality=load_unique_market_rows(path)
    matches=load_matches(sports=('sc',),path=path)
    scores={(r.kickoff,r.sport,r.league,r.home_team,r.away_team):(r.home_score,r.away_score) for r in matches.itertuples(index=False)}
    stats=defaultdict(Counter); days=defaultdict(list); lines=defaultdict(Counter); valid=[]
    for r in records:
        if r['sport']!='sc': continue
        t,s,league,h,aw,family,booking,label,n=r['key']
        if family not in ('승무패','핸디캡','언더오버'): continue
        group=league if league in ('K리그1','K리그2') else '기타축구'
        k=(group,family);stats[k]['unique_valid_offers']+=1
        line=None
        if family!='승무패':
            m=re.fullmatch(r'[HU]\s+([+-]?\d+(?:\.\d+)?)',label)
            if not m: stats[k]['bad_line']+=1;continue
            line=float(m.group(1));lines[k][str(line)]+=1
        score=scores.get((t,s,league,h,aw))
        if score is None:stats[k]['missing_score']+=1;continue
        outcome=settlement(family,line,*score,n)
        if outcome is None:stats[k]['unsupported_or_push']+=1;continue
        if outcome!=r['winner']:stats[k]['score_result_conflict']+=1;continue
        stats[k]['score_line_odds_result_linked']+=1;days[k].append(t.isoformat()[:10])
        valid.append((group,family,line,t,r['odds'],outcome,league,h,aw))
    result={'source_sha256':sha,'source_rows':quality['input_rows'],'loader_quality_all_sports':quality,'definition':'unique nonvoid offers after price/result conflict exclusion; independently joined primary score; certified pregame receipts unavailable', 'groups':{}}
    for k,c in sorted(stats.items()):
        result['groups'][' / '.join(k)]={**c,'date_range':[min(days[k]),max(days[k])] if days[k] else None,'lines':dict(lines[k]),'certified_pregame':0}
    # Fixed demonstrative price-only baseline, not a trained model or an independent confirmation.
    cand=[x for x in valid if x[0] in ('K리그1','K리그2') and x[1]=='언더오버' and x[2]==2.5]
    primary={(x[3],x[6],x[7],x[8]) for x in valid if x[0] in ('K리그1','K리그2') and x[1]=='승무패'}
    candidates=Counter((x[3],x[6],x[7],x[8]) for x in cand)
    cand=[x for x in cand if (x[3],x[6],x[7],x[8]) in primary and candidates[(x[3],x[6],x[7],x[8])]==1]
    for name,rows in [('2023-2024',[x for x in cand if x[3].year<=2024]),('2025-2026',[x for x in cand if x[3].year>=2025])]:
        rows.sort(key=lambda x:(x[3],x[6],x[7],x[8]));cum=peak=dd=0.;wins=0; daily=defaultdict(float)
        for group,f,line,t,odds,y,*_ in rows:
            pick=0 if odds[0]<=odds[1] else 1; win=pick==y;wins+=win;daily[t.date().isoformat()]+=odds[pick]-1 if win else -1
        for day in sorted(daily):
            cum+=daily[day];peak=max(peak,cum);dd=max(dd,peak-cum)
        denom=sum(1 for t,*_ in primary if (t.year<=2024 if name=='2023-2024' else t.year>=2025))
        result.setdefault('descriptive_candidate',{})[name]={'bets':len(rows),'wins':wins,'hit_rate':wins/len(rows) if rows else None,'roi':cum/len(rows) if rows else None,'max_daily_close_drawdown_units':dd,'coverage_numerator_offers':len(rows),'coverage_denominator_linked_1x2_offers':denom,'coverage_ratio':len(rows)/denom if denom else None}
    Path(a.out).write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8')
if __name__=='__main__': main()
