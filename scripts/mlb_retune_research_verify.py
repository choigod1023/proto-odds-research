"""Rebuild from git-committed facts only, compare forecasts, and remove own replay tree."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

ROOT=Path(__file__).resolve().parents[1]
REL=Path('experiments/baseball-league-retune-20261029/mlb')
OUT=ROOT/REL


def sha(path):
    data=path.read_bytes()
    return hashlib.sha256(data.replace(b'\r\n',b'\n') if path.suffix in ('.json','.jsonl','.py','.md') else data).hexdigest()


def main():
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    target=(OUT/'_clean_replay').resolve()
    assert target.parent==OUT.resolve() and target.name=='_clean_replay'
    if target.exists(): raise RuntimeError('replay folder exists; inspect before retry')
    target.mkdir()
    wanted=['scripts/mlb_retune_research.py','tests/test_mlb_retune_research.py',str(REL/'sources').replace('\\','/'),str(REL/'PROTOCOL.md').replace('\\','/')]
    archive=subprocess.check_output(['git','archive','--format=tar',commit,*wanted],cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:') as tar:
        tar.extractall(target,filter='data')
    folder=target/REL
    began=time.monotonic()
    # Block networking in the model process: successful replay must use committed files.
    runner="import socket,runpy,sys; socket.socket=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('offline replay')); sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
    proc=subprocess.run([sys.executable,'-c',runner,str(target/'scripts/mlb_retune_research.py'),'run','--folder',str(folder)],cwd=ROOT,text=True,capture_output=True)
    (OUT/'clean-replay.log').write_text(proc.stdout+proc.stderr,encoding='utf8',newline='\n')
    if proc.returncode: raise RuntimeError('replay failed; see clean-replay.log; tree retained')
    before=json.loads((OUT/'results.json').read_text(encoding='utf8'))
    after=json.loads((folder/'results.json').read_text(encoding='utf8'))
    keys=['selected','validation','evaluation','periods','configs','candidate_metadata','input_sha256',
          'candidate_count','concrete_candidate_count','protocol_sha256','frozen_selection_sha256']
    for key in keys: assert before[key]==after[key],key
    hashes={}
    for name in ('predictions.jsonl','validation_predictions.jsonl','frozen-selection.json','feature-facts.jsonl.gz'):
        hashes[name]=dict(original=sha(OUT/name),committed_replay=sha(folder/name))
        assert hashes[name]['original']==hashes[name]['committed_replay'],name
    tests=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(target/'tests'),'-p','test_mlb_retune_research.py','-v'],cwd=ROOT,text=True,capture_output=True)
    (OUT/'tests.log').write_text(tests.stdout+tests.stderr,encoding='utf8',newline='\n')
    if tests.returncode: raise RuntimeError('committed-source tests failed; tree retained')
    # Adopt added audit date ranges/pick-change metadata from the committed-code run.
    shutil.copyfile(folder/'results.json',OUT/'results.json')
    result=dict(ok=True,source_commit=commit,network_blocked=True,elapsed_seconds=round(time.monotonic()-began,3),
        exact_metric_sections=keys,artifact_hashes=hashes,regression_tests=12,
        source_archive_sha256=hashlib.sha256(archive).hexdigest(),
        source_code_sha256=sha(target/'scripts/mlb_retune_research.py'))
    (OUT/'verification.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8',newline='\n')
    assert target.parent==OUT.resolve() and target.name=='_clean_replay'
    shutil.rmtree(target)
    print(json.dumps(result),flush=True)


if __name__=='__main__': main()
