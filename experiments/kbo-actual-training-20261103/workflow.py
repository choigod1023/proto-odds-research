"""Only accepts fully verified normalized source records and frozen predictions."""
import hashlib,json,subprocess
from pathlib import Path
import pipeline as p
import runner,settlement,source_adapter

def validate_snapshot(paired,lineage,evidence,projected_proofs,known_dates,verified_hashes,seasons):
 """Require paired feature lineage and official identity evidence for every fixture."""
 normalized=p.validate_pairs(paired['projected'],paired['actual'],seasons)
 ids={x['fixture_id'] for x in normalized['actual']}
 if set(evidence)!=ids or set(projected_proofs)!=ids or set(lineage)!=set(p.SELECTORS) or any(set(lineage[s])!=ids for s in p.SELECTORS) or not verified_hashes:raise ValueError('source evidence or lineage coverage mismatch')
 for selector in p.SELECTORS:
  for row in normalized[selector]:
   fid=row['fixture_id'];feature_lineage=lineage[selector][fid];p.validate_lineage(fid,row['event_date'],feature_lineage,known_dates)
   for k in p.PLAYER:
    value=p.original.adapter.scalar(k,feature_lineage[k]['value'])
    if value!=row['features'][k]['value']:raise ValueError('scalar value differs from pinned source lineage')
   if selector=='actual':source_adapter.validate_official_identity(row,evidence[fid],verified_hashes)
   else:source_adapter.validate_projected_identity(row,projected_proofs[fid],known_dates)
 return normalized

def development(snapshot,labels):
 return runner.develop(snapshot,labels)

def final(snapshot,labels,target):
 return runner.final_predictions(snapshot,labels,target)

def frozen_predictions(path,commit,sha256,repo_root):
 if len(commit)!=40 or len(sha256)!=64:raise ValueError('full prediction commit and SHA required')
 path=Path(path);raw=path.read_bytes();rel=path.resolve().relative_to(Path(repo_root).resolve()).as_posix();blob=subprocess.check_output(['git','-C',str(repo_root),'show',commit+':'+rel])
 if hashlib.sha256(raw).hexdigest()!=sha256 or blob!=raw:raise ValueError('prediction must equal committed frozen Git blob')
 data=json.loads(raw)
 if data.get('2026_labels_read') is not False or data.get('historical_receipt_verified') is not False:raise ValueError('invalid prediction freeze')
 return data

def settle_after_freeze(path,commit,sha256,repo_root,load_outcomes_and_prices):
 """No outcome loader executes until the exact prediction blob is validated."""
 frozen=frozen_predictions(path,commit,sha256,repo_root);labels,price_ledgers,reject_audit=load_outcomes_and_prices();results={};ledgers={}
 for selector,predictions in frozen['predictions'].items():results[selector],ledgers[selector]=settlement.settle(predictions,labels,price_ledgers,reject_audit)
 return {'results':results,'ledgers':ledgers,'historical_receipt_verified':False,'context_full_training_differs_from_matched':True}
