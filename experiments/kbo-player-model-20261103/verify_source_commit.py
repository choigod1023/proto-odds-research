"""Reproduce the frozen overlay from the corrected Git blob, byte for byte."""
import argparse,hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path
P=Path(__file__).resolve().parent;ROOT=P.parents[1]
SOURCE=Path('C:/Users/user/Documents/Codex/kbo-official-odds-20261103')
OBJECT='experiments/kbo-official-odds-20261103/restored-prices.jsonl'
OLD='9bcbd53bf95ce6c6961b5b88fd43877152d602b6';NEW='3167b4b19c2f5877af754d3a1a41ee2a8761a3f2'
PRICE_SHA='d9fc2f04fabec1fa41d29521aeafdb96e676747e59723396f36ba5c88a9b8117'
OLD_BLOB_SHA='cb556b0fb182eb13de6d22a513b8a04952e5e22d6b31a6b6ca44f543edb647aa'
RESULT_SHA='24938f0f5489d22bbc76a830e2f48e2491da6687d3f4a5a2f9e8e8c376790d23'
PRED_SHA='d841cd9549a9dab4f3c9612e2fbdea84b9f914b390da4f9c80c0d9e2b3e5d317'
def digest(raw):return hashlib.sha256(raw).hexdigest()
def blob(commit):return subprocess.check_output(['git','-C',str(SOURCE),'show',commit+':'+OBJECT])
new=blob(NEW);old=blob(OLD);assert digest(new)==PRICE_SHA and digest(old)==OLD_BLOB_SHA
assert old.replace(b'\r\n',b'\n')==new.replace(b'\r\n',b'\n') and new.count(b'\r\n')==373
frozen=(P/'phase2/predictions.json').read_bytes();original=(P/'phase3/overlay-results.json').read_bytes()
assert digest(frozen)==PRED_SHA and digest(original)==RESULT_SHA
with tempfile.TemporaryDirectory(prefix='kbo-exact-source-') as folder:
    temporary=Path(folder);price=temporary/'restored-prices.jsonl';result=temporary/'replay.json';price.write_bytes(new)
    assert price.read_bytes()==new and digest(price.read_bytes())==PRICE_SHA
    env=dict(os.environ);env.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    cmd=[sys.executable,'-X','utf8',str(P/'phase3_overlay.py'),'--restored-prices-path',str(price),'--output',str(result)]
    run=subprocess.run(cmd,cwd=ROOT,env=env,text=True,capture_output=True,timeout=180)
    if run.returncode:raise RuntimeError(run.stderr)
    replay=result.read_bytes();assert replay==original and digest(replay)==RESULT_SHA
assert (P/'phase2/predictions.json').read_bytes()==frozen and (P/'phase3/overlay-results.json').read_bytes()==original
output={'old_source_commit':OLD,'old_git_blob_sha256':OLD_BLOB_SHA,'old_working_crlf_sha256':PRICE_SHA,'corrected_source_commit':NEW,'corrected_git_blob_sha256':digest(new),'tempfile_sha256':digest(new),'line_endings_preserved':True,'restored_rows':373,'prediction_sha256':PRED_SHA,'original_and_replayed_results_sha256':RESULT_SHA,'results_byte_identical':True,'prediction_and_results_unmodified':True,'overlay_summary':json.loads(run.stdout),'historical_timing':'Not verified; this is retrospective pricing.'}
parser=argparse.ArgumentParser();parser.add_argument('--output');args=parser.parse_args()
if args.output:
    with Path(args.output).open('x',encoding='utf8',newline='\n') as f:json.dump(output,f,ensure_ascii=False,sort_keys=True,indent=2);f.write('\n')
print(json.dumps(output,ensure_ascii=False))
