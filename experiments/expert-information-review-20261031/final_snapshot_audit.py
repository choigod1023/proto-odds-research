"""Audit saved final-snapshot metadata and metrics using the independent ledger."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import argparse,hashlib,json,math
import numpy as np
from pathlib import Path
from independent_ledger import read_jsonl,audit

def at(s):
    d=datetime.fromisoformat(s.replace('Z','+00:00'))
    if d.utcoffset() is None:raise ValueError('naive datetime')
    return d.astimezone(timezone.utc)

def main():
    p=argparse.ArgumentParser();p.add_argument('snapshot',type=Path);p.add_argument('output',type=Path);a=p.parse_args();root=a.snapshot
    files=['run.py','verify.py','verification.json','results.json','forecasts.jsonl','settlements.jsonl','protocol.json','negative-control.json','inputs/targets.jsonl.gz','inputs/votes.jsonl.gz','inputs/cases.jsonl.gz','inputs/provenance.json','protocol-origin.json']
    def digest(path, normalize=False):
        data=path.read_bytes()
        if normalize and path.suffix in ('.py','.json','.jsonl'):data=data.replace(b'\r\n',b'\n')
        return hashlib.sha256(data).hexdigest()
    hashes={name:digest(root/name,True) for name in files}
    raw_hashes={name:digest(root/name) for name in files}
    results=json.loads((root/'results.json').read_text(encoding='utf8'));verification=json.loads((root/'verification.json').read_text(encoding='utf8'))
    for name,expected in verification['code_sha256'].items():
        if hashes[name]!=expected or results['code_sha256'][name]!=expected:raise ValueError('code hash mismatch: '+name)
    for name,expected in results['inputs'].items():
        if hashes['inputs/'+name]!=expected or verification['input_sha256'].get(name)!=expected:raise ValueError('input hash mismatch: '+name)
    origin=json.loads((root/'protocol-origin.json').read_text(encoding='utf8'))
    if results['parent_protocol_commit']!='396a12e0a477418c8fee66562e36dba4bb274525' or origin['parent_commit']!=results['parent_protocol_commit'] or origin['sha256']!=hashes['protocol.json']:
        raise ValueError('protocol origin mismatch')
    if results['negative_control_parent_commit']!='f962647e41015be807c9570591f763c5b98889d0':raise ValueError('negative-control origin mismatch')
    for name,expected in verification['hashes'].items():
        if hashes[name]!=expected:raise ValueError('verification hash mismatch: '+name)
    targets=[x for x in read_jsonl(root/'inputs/targets.jsonl.gz') if x['actual_votes']]
    forecasts=read_jsonl(root/'forecasts.jsonl'); settlements=read_jsonl(root/'settlements.jsonl')
    ledger=audit(targets,forecasts,settlements)
    if len(targets)!=results['eligible']:raise ValueError('independent preflight population differs from eligible; need explicit eligible list')
    lookup={t['event_id']:t for t in targets};june=at('2026-06-01T00:00:00Z');july=at('2026-07-01T00:00:00Z');nov=at('2026-11-01T00:00:00Z')
    def available(cut,inclusive=False):return [x for x in targets if (at(x['kickoff'])+timedelta(hours=96)<=cut if inclusive else at(x['kickoff'])+timedelta(hours=96)<cut) and at(x['decision_at'])<cut]
    train_june=len(available(june));train_july=len(available(july));fit=results['fit']
    if fit['train_n_before_june']!=train_june or fit['frozen_n_before_july']!=train_july:raise ValueError('cutoff count mismatch')
    candidates=['market','expert_global','expert_hierarchical']; losses=fit['validation_logloss']; winner=next(k for k in candidates if losses[k]<=min(losses.values())+1e-12)
    if winner!=fit['selected']:raise ValueError('validation choice differs from stored losses')
    cells=defaultdict(list); cutchecks=0
    for f in forecasts:
        d=at(f['decision_at']); t=lookup[f['event_id']]
        if not july<=d<nov:raise ValueError('forecast outside test period')
        if f['model'] in ('selected','sham'):
            cutoff=july if f['mode']=='frozen' else d.replace(hour=0,minute=0,second=0,microsecond=0)
            expected=0 if winner=='market' else len(available(cutoff,inclusive=f['mode']=='prequential'))
            if f['training_n']!=expected:raise ValueError('training_n mismatch')
            cutchecks+=1
        for l in (f['league'],'ALL'):cells[(l,f['mode'],f['model'])].append((f,t))
    checks=0
    for cell in results['metrics']:
        if cell['split']!='test':raise ValueError('unexpected metric split')
        rows=cells[(cell['league'],cell['mode'],cell['model'])];eligible=sum(1 for t in targets if july<=at(t['decision_at'])<nov and (cell['league']=='ALL' or t['league']==cell['league']))
        profit=sum((Decimal(str(t['odds'][f['pick']]))-1 if f['pick']==t['outcome'] else Decimal(-1) for f,t in rows),Decimal(0));n=len(rows);wins=sum(f['pick']==t['outcome'] for f,t in rows)
        probs=[(f,t) for f,t in rows if f['probabilities'] is not None]
        vals={'n':n,'hits':wins,'accuracy':wins/n,'roi':float(profit/n),'eligible_denominator':eligible,'coverage_on_eligible':n/eligible,
              'logloss':sum(-math.log(max(1e-15,f['probabilities'][t['outcome']])) for f,t in probs)/len(probs) if probs else None,
              'brier':sum(sum((v-(i==t['outcome']))**2 for i,v in enumerate(f['probabilities'])) for f,t in probs)/len(probs) if probs else None}
        daily=defaultdict(Decimal); monthly=defaultdict(Decimal); changed=0
        market_for_mode={(f['event_id'],f['mode']):f for f in forecasts if f['model']=='market'}
        for f,t in rows:
            hit=f['pick']==t['outcome'];net=Decimal(str(t['odds'][f['pick']]))-1 if hit else Decimal(-1)
            day=at(f['decision_at']).date().isoformat();daily[day]+=net;monthly[day[:7]]+=net
            ref=market_for_mode[(f['event_id'],f['mode'] if f['mode']!='comparison' else 'frozen')]
            changed+=f['pick']!=ref['pick']
        balance=peak=maximum=Decimal(0)
        for day in sorted(daily):
            balance+=daily[day];peak=max(peak,balance);maximum=max(maximum,peak-balance)
        if {k:str(v) for k,v in sorted(monthly.items())}!=cell['monthly_unit_pnl'] or Decimal(str(cell['daily_close_max_drawdown_units']))!=maximum or changed!=cell['pick_changes_vs_market']:
            raise ValueError('cash, drawdown or pick changes mismatch')
        checks+=3
        for k,value in vals.items():
            old=cell[k]
            if value is None or old is None:
                if value!=old:raise ValueError('metric mismatch '+k)
            elif abs(value-old)>1e-11:raise ValueError('metric mismatch '+k)
            checks+=1
    market={(f['event_id'],f['mode']):f for f in forecasts if f['model']=='market'}
    bootstrap_checks=0
    for cell in results['metrics']:
        if cell['model']!='selected' or cell['mode'] not in ('frozen','prequential'):continue
        rows=cells[(cell['league'],cell['mode'],'selected')];weeks=defaultdict(list)
        for f,t in rows:weeks[at(f['decision_at']).strftime('%G-W%V')].append((f,t))
        if len(weeks)<2:continue
        acc=[];profit=[];size=[]
        for w in sorted(weeks):
            group=weeks[w];size.append(len(group));dh=0;dp=Decimal(0)
            for f,t in group:
                b=market[(f['event_id'],f['mode'])]; y=t['outcome'];hit=int(f['pick']==y);basehit=int(b['pick']==y)
                dh+=hit-basehit;dp+=(Decimal(str(t['odds'][f['pick']]))-1 if hit else Decimal(-1))-(Decimal(str(t['odds'][b['pick']]))-1 if basehit else Decimal(-1))
            acc.append(dh);profit.append(float(dp))
        rng=np.random.default_rng(20261031);draw=rng.integers(len(weeks),size=(5000,len(weeks)))
        sizes=np.asarray(size)[draw].sum(axis=1)
        hit_ci=np.quantile(np.asarray(acc)[draw].sum(axis=1)/sizes,[.025,.975])
        roi_ci=np.quantile(np.asarray(profit)[draw].sum(axis=1)/sizes,[.025,.975])
        if not np.allclose(hit_ci,cell['paired_weekly_95ci_accuracy_vs_market'],rtol=0,atol=1e-12) or not np.allclose(roi_ci,cell['paired_weekly_95ci_roi_vs_market'],rtol=0,atol=1e-12):raise ValueError('weekly bootstrap mismatch')
        bootstrap_checks+=1
    report={'snapshot_sha256':hashes,'ledger_records_checked':ledger['records'],'metric_values_checked':checks,'train_cutoff_forecasts_checked':cutchecks,'bootstrap_cells_checked':bootstrap_checks,'raw_sha256':raw_hashes,'stored_parent_protocol_commit':results['parent_protocol_commit'],'copied_protocol_commit':results['copied_protocol_commit'],'reviewed_commit':'863b13ea1015fadb5b57b68af4a50b7af09b3c3b','summary':'no discrepancies in specified checks','not_checked':['candidate validation losses independently refit','probability vector reconstruction','actual receipt or executable prices','independent verification of raw HTTP','developer output reproduction from scratch']}
    a.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print('ledger records',ledger['records'],'metric values',checks,'cutoff forecasts',cutchecks,'no discrepancies')
if __name__=='__main__':main()
