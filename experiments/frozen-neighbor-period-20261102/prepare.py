"""Prepare fixed monthly DC inputs and audited 2026 cohorts; no candidate scoring."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('period_fixed_dc',HERE/'vendor/frozen_dc.py')
dc=importlib.util.module_from_spec(spec);spec.loader.exec_module(dc)
DC_SHA='79187243175ed45556af79f1c376f8628844747188865cb57b5e0b627e48bf3f'
DOMESTIC={'울산HDFC':'K01','포항스틸':'K03','제주SKFC':'K04','전북현대':'K05',
 'FC서울':'K09','대전하나':'K10','강원FC':'K21','광주FC':'K22','FC안양':'K27',
 '김천상무':'K35','인천유나':'K18','부천FC':'K26'}
OVERSEAS={'Ulsan HD':'K01','Pohang':'K03','Jeju SK':'K04','Jeonbuk':'K05',
 'Seoul':'K09','Daejeon':'K10','Gangwon':'K21','Gwangju FC':'K22','Anyang':'K27',
 'Gimcheon Sangmu':'K35','Incheon':'K18','Bucheon FC 1995':'K26'}


def read_lines(path):
    return [json.loads(x) for x in path.read_text(encoding='utf8').splitlines() if x.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n',b'\n')).hexdigest()


def save(path, value):
    with path.open('x',encoding='utf8',newline='\n') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')


def source_history():
    if sha(HERE/'vendor/frozen_dc.py')!=DC_SHA:
        raise ValueError('fixed DC code mismatch')
    old=read_lines(HERE/'inputs/official-history-2023-2025.jsonl')
    if len(old)!=684:
        raise ValueError('history coverage')
    history=[]
    for r in old:
        if (r['league']!='KLEAGUE1' or r['source_status']!='FE' or r['source_end_yn']!='Y'
            or r['year'] not in (2023,2024,2025)):
            raise ValueError('nonfinal history')
        history.append(dict(id=r['fixture_id'],day=r['day'],home_id=r['home_team_id'],
                            away_id=r['away_team_id'],hg=r['scores']['home'],ag=r['scores']['away']))
    official=read_lines(HERE/'official-2026/fixtures.jsonl')
    history.extend({k:r[k] for k in ('id','day','home_id','away_id','hg','ag')} for r in official)
    if len({r['id'] for r in history})!=len(history):
        raise ValueError('duplicate history IDs')
    return history,official


def monthly_states(history, months):
    states={}
    for month in sorted(months):
        reference=month+'-01';cutoff=(date.fromisoformat(reference)-timedelta(days=7)).isoformat()
        past=sorted((r for r in history if r['day']<=cutoff),key=lambda r:(r['day'],r['id']))
        raw=json.dumps(past,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
        state=dc.fit(past,reference)
        states[month]={'state':state,'training_sha256':hashlib.sha256(raw).hexdigest(),
                       'training_ids':[r['id'] for r in past],'training_rows':past}
    return states


def parse_domestic(r):
    h=re.fullmatch(r'(.+?)\s+(\d+)',r['home']);a=re.fullmatch(r'(\d+)\s+(.+)',r['away'])
    t=re.fullmatch(r'(\d{2})\.(\d{2})\([^)]*\)\s+(\d{2}):(\d{2})',r['date_text'])
    if not h or not a or not t or h[1] not in DOMESTIC or a[2] not in DOMESTIC:
        return None
    mm,dd,hh,minute=t.groups();day=f'2026-{mm}-{dd}'
    return dict(day=day,kickoff=day+f'T{hh}:{minute}:00',home_id=DOMESTIC[h[1]],away_id=DOMESTIC[a[2]],
                recorded_scores=[int(h[2]),int(a[1])],prices=r['odds'].split(','),is_void=r['is_void'])


def link(parsed, official):
    index=defaultdict(list)
    for r in official:index[(r['day'],r['home_id'],r['away_id'])].append(r)
    eligible=[];excluded=[]
    for position,r in enumerate(parsed):
        reason=None
        if r is None:reason='unparseable_price_identity'
        else:
            candidates=index[(r['day'],r['home_id'],r['away_id'])]
            if len(candidates)!=1:reason='official_final_missing_or_ambiguous'
            else:
                f=candidates[0]
                if r.get('is_void','False')!='False':reason='void_market'
                elif r.get('kickoff') and r['kickoff']!=f['kickoff']:reason='local_clock_conflict'
                elif r['recorded_scores']!=[f['hg'],f['ag']]:reason='archived_score_conflict'
                else:
                    try:p=[Decimal(v) for v in r['prices']]
                    except Exception:p=[]
                    if len(p)!=3 or any(not v.is_finite() or v<=1 for v in p):reason='invalid_three_way_price'
        if reason:excluded.append(dict(id=f'price-row:{position}',eligibility='excluded',reason=reason))
        else:eligible.append((position,r,f))
    ids=Counter(f['id'] for _,_,f in eligible)
    clean=[]
    for i,r,f in eligible:
        if ids[f['id']]!=1:excluded.append(dict(id=f'price-row:{i}',eligibility='excluded',reason='duplicate_price_fixture'))
        else:clean.append((i,r,f))
    return clean,excluded


def opportunities(cohort, linked, excluded, states, source_sha):
    rows=list(excluded)
    for i,r,f in linked:
        monthly=states[r['day'][:7]];state=monthly['state'];p,detail=dc.predict(state,f['home_id'],f['away_id'])
        rows.append(dict(id=f['id'],eligibility='eligible',day=f['day'],kickoff=f['kickoff']+'+09:00',
          home_id=f['home_id'],away_id=f['away_id'],competition='KLEAGUE1',market='full_time_1x2',
          selection_order=['home','draw','away'],prices=r['prices'],price_source_sha256=source_sha,
          price_time_kind='record_date_only',price_time=r['day'],identity_evidence=f'{cohort}:raw-row:{i};unique_oriented_day;'+('clock_checked' if r.get('kickoff') else 'no_price_clock'),
          dc_p=p,dc_reference=state['reference_day'],dc_training_cutoff=state['cutoff_inclusive'],
          dc_model='kleague_dc_half180_l2_10_monthly_lag7_rho_fixed',dc_training_sha256=monthly['training_sha256'],
          result_status='official_final',home_score=f['hg'],away_score=f['ag'],
          official_result_evidence=f"official-2026/{f['source_raw']}#sha256={f['source_sha256']}"))
    return rows


def main():
    history,official=source_history()
    domestic=read_lines(HERE/'inputs/domestic-2026.raw.jsonl')
    overseas_path=ROOT/'data/raw/overseas/K리그1.json'
    overseas=[r for r in json.loads(overseas_path.read_text(encoding='utf8')) if r['date'].startswith('2026-')]
    op=[]
    for r in overseas:
        if r['home_en'] not in OVERSEAS or r['away_en'] not in OVERSEAS:op.append(None);continue
        op.append(dict(day=r['date'][:10],home_id=OVERSEAS[r['home_en']],away_id=OVERSEAS[r['away_en']],
            recorded_scores=[r['home_score'],r['away_score']],prices=[str(v) for v in r['odds']]))
    cohorts={'domestic':link([parse_domestic(r) for r in domestic],official),'overseas':link(op,official)}
    months={r['day'][:7] for good,_ in cohorts.values() for _,r,_ in good}
    states=monthly_states(history,months)
    save(HERE/'dc-states.json',states)
    receipts={}
    for name,(good,bad) in cohorts.items():
        price_sha=sha(HERE/'inputs/domestic-2026.raw.jsonl') if name=='domestic' else sha(overseas_path)
        rows=opportunities(name,good,bad,states,price_sha)
        save(HERE/(name+'-opportunities.json'),rows)
        receipts[name]=dict(raw_price_rows=len(good)+len(bad),eligible=len(good),excluded=dict(Counter(r['reason'] for r in bad)),
            first_day=min((r['day'] for _,r,_ in good),default=None),last_day=max((r['day'] for _,r,_ in good),default=None),
            source_price_lf_sha256=price_sha,price_receipt_verified=False,price_execution_verified=False,
            timezone='Korean official schedule local clock interpreted as +09:00; no independent clock certification')
    save(HERE/'preparation.json',dict(cohorts=receipts,official_final_count=len(official),history_count=len(history),
        dc_source_lf_sha256=DC_SHA,prior_source_bank='unchanged 2024; target DC trained monthly on causal prior official results only',
        score_availability='month start minus7days research assumption, not historical actual receipt'))
    print(json.dumps(receipts,ensure_ascii=True))


if __name__=='__main__':main()
