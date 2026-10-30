"""Join immutable local history with later cached-round result revisions.

Reads cached HTML locally. Never collects remotely or rebuilds production DBs.
Newer nonempty round snapshots replace the entire old round, not just winning
rows. Archived closing quotes remain retrospective, not proven purchase prices.
"""
import argparse
import csv
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from build_dataset import _game_record
from wisetoto import parse_rows,repair_mojibake


def read_jsonl(path):
    with gzip.open(path,'rt',encoding='utf8') as f:return [json.loads(l) for l in f if l.strip()]


def merge_round_revisions(rows,revisions,base_observed_at):
    """Replaces complete rounds only from later known observations; no outcome filter."""
    result=list(rows);audit=[]
    for meta,new_rows in revisions:
        season,rnd=int(meta['season']),int(meta['round'])
        existing=[r for r in result if int(r['year'])==season and int(r['round'])==rnd]
        newer_time=datetime.fromisoformat(meta['stored_at'].replace('Z','+00:00'))
        base_time=datetime.fromisoformat(base_observed_at.replace('Z','+00:00'))
        if newer_time.tzinfo is None or base_time.tzinfo is None:raise ValueError('observation timezone required')
        later=newer_time>=base_time
        if not new_rows:
            audit.append(dict(**meta,action='empty_snapshot_not_used',rows=0));continue
        if not later and existing:
            audit.append(dict(**meta,action='older_snapshot_not_used',rows=len(new_rows)));continue
        result=[r for r in result if not (int(r['year'])==season and int(r['round'])==rnd)]
        result.extend(new_rows)
        audit.append(dict(**meta,action='replace_whole_round' if existing else 'new_round',rows=len(new_rows),replaced_rows=len(existing)))
    return result,audit


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture',type=Path,required=True)
    p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--dataset-metadata',type=Path,required=True)
    p.add_argument('--cached-documents',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    with args.fixture.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    fixture_rows=len(rows);dataset_rows=read_jsonl(args.dataset);rows.extend(dataset_rows)
    base=json.loads(args.dataset_metadata.read_text(encoding='utf8'))
    observed=base['header']['dataset']['generated_at'];revisions=[]
    for doc in read_jsonl(args.cached_documents):
        _,season,rnd=doc['name'].split(':')
        payload=doc['payload_json'];actual=hashlib.sha256(payload.encode()).hexdigest()
        if actual!=doc['content_hash']:raise ValueError('document content hash mismatch')
        html=json.loads(payload)
        if not isinstance(html,str):raise ValueError('archive is not HTML text')
        new_rows=[_game_record(r) for r in parse_rows(repair_mojibake(html),int(season),int(rnd))]
        revisions.append((dict(season=int(season),round=int(rnd),name=doc['name'],stored_at=doc['stored_at'],revision=doc['revision'],content_hash=actual),new_rows))
    combined,audit=merge_round_revisions(rows,revisions,observed)
    args.output.mkdir(parents=True,exist_ok=True)
    data=''.join(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n' for r in combined).encode()
    (args.output/'combined-games.jsonl.gz').write_bytes(gzip.compress(data,mtime=0))
    manifest=dict(fixture_rows=fixture_rows,dataset_rows=len(dataset_rows),combined_rows=len(combined),
        base_dataset_generated_at=observed,round_revisions=audit,
        source_sha256={str(x):hashlib.sha256(x.read_bytes()).hexdigest() for x in
            [args.fixture,args.dataset,args.dataset_metadata,args.cached_documents]},
        archive_parser_sha256=hashlib.sha256(Path(parse_rows.__code__.co_filename).read_bytes()).hexdigest(),
        reissue_price_conflicts_not_optimistically_resolved=True,purchase_availability_unproven=True)
    (args.output/'combined-input-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'combined_rows':len(combined),'rounds_added_or_updated':sum(r['action'] in ('replace_whole_round','new_round') for r in audit)}))


if __name__=='__main__':main()
