"""Pin committed collector code outside worktrees; install only its own new task."""
import argparse,hashlib,io,json,subprocess,sys,tarfile
from pathlib import Path

REPO=Path(__file__).resolve().parents[2]
PREFIXES=('research/kbo_forward','research/m1_service','experiments/kbo-m0-followup-20261103','src','scripts/export_forward_operating_votes.py')

def bootstrap(state,m1_state=None):
    state=Path(state).resolve();state.mkdir(parents=True,exist_ok=True)
    dirty=subprocess.check_output(['git','-C',str(REPO),'status','--porcelain','--',*PREFIXES],text=True)
    if dirty.strip():raise ValueError('commit collector changes before pinning a runtime')
    revision=subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip()
    archive=subprocess.check_output(['git','-C',str(REPO),'archive',revision,*PREFIXES])
    root=state/'runtime'/revision;root.mkdir(parents=True,exist_ok=True);files={}
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            if member.isdir():continue
            target=(root/member.name).resolve()
            if not member.isfile() or not target.is_relative_to(root) or member.size>60_000_000:raise ValueError('unsafe archive member')
            raw=tar.extractfile(member).read();files[member.name]=hashlib.sha256(raw).hexdigest()
            if target.exists():
                if target.read_bytes()!=raw:raise ValueError('existing pinned runtime differs')
                continue
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as out:out.write(raw)
    receipt={'code_revision':revision,'archive_sha256':hashlib.sha256(archive).hexdigest(),'runtime_root':str(root),'state_root':str(state),'python_executable':sys.executable,'files':files,'task_name':'ProtoKboM0Forward-v1','run_interval_minutes':5,'requires_pc_awake_logged_in_online':True,'no_production_or_budget_changes':True}
    receipt['m1_state_root']=str(Path(m1_state).resolve()) if m1_state is not None else None
    (state/'installation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return receipt

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--state',type=Path,required=True);ap.add_argument('--m1-state',type=Path);ap.add_argument('--install',action='store_true');a=ap.parse_args();r=bootstrap(a.state,a.m1_state)
    if a.install:
        command=['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(Path(r['runtime_root'])/'research/kbo_forward/install_task.ps1'),'-PythonExe',r['python_executable'],'-RuntimeRoot',r['runtime_root'],'-StateRoot',r['state_root']]
        if r['m1_state_root']:command+=['-M1StateRoot',r['m1_state_root']]
        subprocess.run(command,check=True)
    print(json.dumps({k:r[k] for k in ('code_revision','runtime_root','state_root','task_name','requires_pc_awake_logged_in_online')},ensure_ascii=False))
