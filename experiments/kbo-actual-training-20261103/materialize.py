"""Materialize only immutable, hash-checked input Git blobs; no labels after 2025."""
import argparse,gzip,hashlib,json,subprocess
from pathlib import Path
import source_adapter as source
P=Path(__file__).resolve().parent;I=P/'inputs';DATA=Path('C:/Users/user/Documents/Codex/kbtdata');OLD=Path('C:/Users/user/Documents/Codex/kbeffects');M='experiments/kbo-actual-training-data-20261103/'
COMMIT='58556de08c64604276de8682373d3ccc8d535d7c';MAN_SHA='8be3db58b4915383acd1f375a1e244840c22fcf6b2a2a40cf71a9bf5441e84f9'
def blob(repo,commit,path,sha):return source.committed_blob(repo,commit,path,sha)
def save(name,raw):
 path=I/name
 if path.exists():raise ValueError('input already exists: '+name)
 path.write_bytes(raw);return hashlib.sha256(raw).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--commit',required=True);p.add_argument('--manifest-sha256',required=True);args=p.parse_args()
 if args.commit!=COMMIT or args.manifest_sha256!=MAN_SHA:raise ValueError('explicit final source pin mismatch')
 manifest_raw=blob(DATA,COMMIT,M+'paired-inputs-manifest.json',MAN_SHA);manifest=json.loads(manifest_raw)
 if manifest['status']!='finalized_paired_research_inputs' or manifest['parent_contract_sha256']!='512c6172eb792ed6e5f7afe7b90d4cfaa0e4087b05e0a497ec7cf9a997cb2480' or manifest['point_in_time_proven']!=0 or manifest['no_labels_read'] is not True or manifest['no_model_fit_or_score'] is not True:raise ValueError('unexpected source manifest')
 I.mkdir(exist_ok=True);files={};files['paired-inputs-manifest.json']=save('paired-inputs-manifest.json',manifest_raw)
 for key,sha in manifest['output_sha256'].items():
  name='paired-'+key+'.jsonl.gz';files[name]=save(name,blob(DATA,COMMIT,M+name,sha))
 sources={'2023':('C:/Users/user/Documents/Codex/kbtdata','actual-rosters-2023.jsonl.gz'),'2024':('C:/Users/user/Documents/Codex/kbt24','actual-rosters-2024.jsonl.gz'),'2025':('C:/Users/user/Documents/Codex/kbt25','actual-rosters-2025.jsonl.gz')};raw_hashes={}
 for year,(repo,dst) in sources.items():
  info=manifest['source_rosters'][year];raw=blob(Path(repo),info['commit'],info['path'],info['sha256']);raw_hashes[dst]=info['sha256'];files[dst]=save(dst,gzip.compress(raw,mtime=0) if not info['path'].endswith('.gz') else raw)
 info=manifest['verification'];files['actual-starters-2026.jsonl.gz']=save('actual-starters-2026.jsonl.gz',blob(DATA,info['commit'],info['path'],info['sha256']))
 legacy='experiments/kbo-player-effects-20261103/inputs/';old='d08ccb708033fb6b067e84e80eae81ea3c9b4cfa'
 for name,sha in [('features.jsonl.gz','346edd84d4388f99cc9ad23f7ad530560445b4bc06b6f841b745dabffa1a8b42'),('development-labels.jsonl.gz','22f3691f1e9017bbcfd9b2bde35ca0f6c208e292f117d056dd5271a80e9f6a33'),('prior-projected-features.jsonl.gz','eca78a16ca59ccc1de6eca47713acded513541c59cddbbb4d8dd193e7c5b27ae'),('fresh-history-2026-projected.jsonl.gz','8b090ed9b204a50584c5e2a854bd0c6210a133a8518de78db51ca8f4217aa23f'),('fresh-history-2026-official_batting_and_starter.jsonl.gz','82a1e8630fafe2e790957900e981512fdfc8e70ba536f5ced45ac785f79dc706')]:files[name]=save(name,blob(OLD,old,legacy+name,sha))
 index={'paired_source_commit':COMMIT,'paired_manifest_sha256':MAN_SHA,'files':files,'source_roster_raw_blob_sha256':raw_hashes,'no_2026_labels_materialized':True};out=(json.dumps(index,indent=2,sort_keys=True)+'\n').encode('utf8');save('snapshot-index.json',out);print(json.dumps({'files':len(files),'paired_commit':COMMIT,'manifest_sha256':MAN_SHA}))
if __name__=='__main__':main()
