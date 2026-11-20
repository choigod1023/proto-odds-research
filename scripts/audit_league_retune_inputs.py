"""Freeze and check reference inputs before integrating league workers."""
from collections import Counter
from decimal import Decimal
from pathlib import Path
import argparse,hashlib,json,sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from devig import market_probabilities


def read(path):return [json.loads(s) for s in path.read_text(encoding='utf8').splitlines() if s]


def audit(prior,old,output):
    asian=read(prior/'asian-cohort-inputs.jsonl');mlb=read(prior/'mlb-external-features.jsonl')
    original=read(old/'targets.jsonl');rounding=[];baseline_counts={};manifests={}
    for league in ['KBO','NPB','MLB']:
        rows=[r for r in original if r['league']==league];wins=push=0;net=Decimal(0)
        for r in rows:
            assert r['voters']>0 and sum(r['vote_counts'].values())==r['voters']
            assert len(r['prices'])==2 and set(r['order'])=={'home','away'}
            p=market_probabilities(r['prices'])[r['order'].index('home')]
            new=.9*p+.1*r['vote_counts'].get('home',0)/r['voters'];pick='home' if new>=.5 else 'away'
            old_pick=r['picks']['market90_vote10']
            if old_pick!=pick:rounding.append(dict(league=league,fixture_id=r['fixture_id'],unrounded_pick=pick,archived_pick=old_pick,home_probability=new))
            if r['hg']==r['ag']:push+=1;continue
            correct=(pick=='home')==(r['hg']>r['ag']);wins+=int(correct)
            net+=Decimal(str(r['prices'][r['order'].index(pick)]))-1 if correct else Decimal(-1)
        baseline_counts[league]=dict(n=len(rows),wins=wins,pushes=push,accuracy=wins/(len(rows)-push),roi=float(net/len(rows)),net_krw=int(net*10000))
    old_ids={r['fixture_id'] for r in original if r['league']=='MLB'}
    assert all('MLB:'+str(r['gamePk']) not in old_ids for r in mlb)
    assert len(mlb)==len({r['gamePk'] for r in mlb})==230
    for p in [prior/'asian-cohort-inputs.jsonl',prior/'mlb-external-features.jsonl',old/'targets.jsonl']:
        manifests[p.name]=hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    report=dict(source_rows=dict(Counter(r['league']+':'+r['role'] for r in asian)),mlb_external_n=len(mlb),
        full_original_91=baseline_counts,archive_rounding_pick_differences=rounding,input_sha256=manifests,
        reference_definition='unrounded market90_vote10 research formula; not frozen production model',
        runtime_pr409_scope='live public_tipster_votes intake; historical compare_year_model_accuracy reads frozen strict rows and imports devig only; no historical reparse asserted')
    output.mkdir(parents=True,exist_ok=True)
    (output/'input-reference-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8',newline='\n')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ['prior','old','output']:p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();audit(a.prior,a.old,a.output)
