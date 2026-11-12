"""Download public tip history and audit coverage, without relabeling it model evidence."""
import argparse
import csv
from collections import Counter,defaultdict
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import urllib.request

URL='https://www.betshoot.com/archives/tipsters.php?export=json'


def unit_return(odds,outcome):
    if not math.isfinite(odds) or odds<=1:raise ValueError('invalid odds')
    label=outcome.strip().lower()
    if label=='won':return odds-1
    if label=='lost':return -1
    if label in ('draw','push','void'):return 0
    if label=='won 1/2':return (odds-1)/2
    if label=='lost 1/2':return -.5
    raise ValueError('unknown or pending outcome')


def external_summary(rows):
    seen=set(); valid=[]; excluded=Counter()
    for r in rows:
        key=tuple(r.get(k) for k in ('date','tipster','sport','league','match','tip','odds'))
        if key in seen:
            excluded['duplicate_identity']+=1;continue
        seen.add(key)
        try:profit=unit_return(float(r['odds']),r['outcome'])
        except (ValueError,KeyError,TypeError):
            excluded['invalid_or_unsettled']+=1;continue
        valid.append((r,profit))
    def stats(pairs):
        return dict(n=len(pairs),flat_unit_roi_pct=100*sum(p for _,p in pairs)/len(pairs) if pairs else None,
                    unique_matches=len({(r['date'],r['sport'],r['match']) for r,_ in pairs}))
    groups={}
    for field in ('sport','year'):
        buckets=defaultdict(list)
        for r,p in valid:buckets[r['date'][:4] if field=='year' else r['sport']].append((r,p))
        groups[field]={k:stats(v) for k,v in sorted(buckets.items())}
    return dict(downloaded_records=len(rows),excluded=dict(excluded),overall=stats(valid),groups=groups,
                first_date=min((r['date'] for r,_ in valid),default=None),last_date=max((r['date'] for r,_ in valid),default=None),
                fields=sorted(set().union(*(r.keys() for r in rows))),
                classification='publisher_archive_descriptive_NOT_our_hybrid_validation')


def fixed_diagnostics(rows):
    days=defaultdict(list)
    for r in rows:
        p=.75*r['arms']['production']+.25*r['arms']['tipster']
        side=0 if p>=.5 else 1
        won=(side==0)==r['home_won']
        days[r['date']].append(r['odds'][side]-1 if won else -1)
    def roi(values):return 100*sum(values)/len(values) if values else None
    keys=sorted(days)
    rng=random.Random(7525); samples=[]
    for _ in range(20000):
        sampled=[v for _ in keys for v in days[rng.choice(keys)]]
        samples.append(roi(sampled))
    samples=sorted(x for x in samples if x is not None)
    return dict(n=len(rows),days=len(days),roi_pct=roi([x for v in days.values() for x in v]),
        per_day={k:dict(n=len(v),roi_pct=roi(v)) for k,v in days.items()},
        leave_one_day_out={k:roi([x for other,v in days.items() if other!=k for x in v]) for k in keys},
        descriptive_day_cluster_bootstrap_95=[samples[int(.025*len(samples))],samples[int(.975*len(samples))]] if samples else None,
        warning='Same development cohort; five clusters and post-selection bias. Not an independent confidence guarantee.')


def git_coverage(path):
    lines=subprocess.check_output(['git','log','--all','--format=%H %cI','--',path],text=True).splitlines()
    dates=sorted(line.split()[1] for line in lines)
    return dict(changes=len(lines),first_commit=min(dates,default=None),last_commit=max(dates,default=None))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--paired',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=True)
    req=urllib.request.Request(URL,headers={'User-Agent':'HistoricalResearch/1.0'})
    download_error=None
    try:
        with urllib.request.urlopen(req,timeout=45) as response:raw=response.read(20_000_000)
        rows=json.loads(raw)
    except (OSError,ValueError) as exc:
        download_error=type(exc).__name__
        raw=None;rows=[]
    # Raw download is local-only. No public republication of the full archive.
    cache=Path('.local-test');cache.mkdir(exist_ok=True)
    raw_path=cache/('betshoot-'+hashlib.sha256(raw).hexdigest()[:16]+'.json') if raw else None
    if raw and not raw_path.exists():
        with raw_path.open('xb') as f:f.write(raw)
    paired_raw=a.paired.read_bytes();paired=json.loads(paired_raw)['paired_rows']
    files=['data/raw/picksters/tailslips_pick_events.jsonl','data/raw/prediction_ledger/pregame.jsonl','docs/data/picks_v2.json']
    snapshot_rows=0; malformed=0; offers=set(); sports=Counter(); csv_files=list(Path('data/raw/snapshots').glob('odds_timeseries_*.csv'))
    for path in csv_files:
        with path.open(encoding='utf-8-sig',newline='') as f:
            for r in csv.DictReader(f):
                snapshot_rows+=1
                if r.get('sport') not in ('bs','sc','vl','bk'):
                    malformed+=1;continue
                sports[r['sport']]+=1
                offers.add(tuple(r.get(k) for k in ('year','round','game_no','sport','market_family')))
    result=dict(retrieved_at=datetime.now(timezone.utc).isoformat(),source_url=URL,source_sha256=hashlib.sha256(raw).hexdigest() if raw else None,
        download_error=download_error,
        local_odds_inventory=dict(files=len(csv_files),rows=snapshot_rows,unrecognized_sport_rows=malformed,unique_market_offer_keys=len(offers),rows_by_sport=dict(sports),warning='Repeated quotes and different markets; NOT independent game count'),
        paired_sha256=hashlib.sha256(paired_raw).hexdigest(),external=external_summary(rows) if raw else None,
        fixed75_25_development_diagnostics=fixed_diagnostics(paired),git_history={f:git_coverage(f) for f in files},
        independent_hybrid_n=0,monthly_loss_limit_krw=50000,production_changed=False,
        limitations=['External archive lacks frozen production probabilities and exact publication time.',
            'Publisher outcome/odds and settlement rules are not independently verified or Proto-equivalent.',
            'No inference of historical prepublication from a date-only record.',
            'New external records are not used to retune locked 75:25.',
            'Football-Data automated training restrictions observed; no bulk download from that source.',
            'Not a crawl of every historical match or every website.'])
    with (a.output_dir/'results.json').open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
