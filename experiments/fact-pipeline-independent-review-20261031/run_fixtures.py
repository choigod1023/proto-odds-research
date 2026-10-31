"""Offline JSON fixture harness. The caller supplies a local, test-only driver."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent

def check(case, observed):
    if not isinstance(observed,dict):return ['result must be a JSON object']
    diffs=[]
    for name,want in case['expected'].items():
        if name not in observed or type(observed[name]) is not type(want) or observed[name]!=want:
            diffs.append(f'{name}: expected {want!r}, received {observed.get(name)!r}')
    return diffs

def invoke(driver,case,timeout):
    with tempfile.TemporaryDirectory(prefix='facts-review-') as temp:
        try:
            result=subprocess.run([sys.executable,'-I',str(driver)],input=json.dumps(case,ensure_ascii=False),text=True,encoding='utf8',capture_output=True,cwd=temp,timeout=timeout,shell=False)
        except subprocess.TimeoutExpired:
            return {'id':case['id'],'status':'driver_timeout'}
        if result.returncode:
            return {'id':case['id'],'status':'driver_error','exit_code':result.returncode}
        try:observed=json.loads(result.stdout)
        except json.JSONDecodeError:return {'id':case['id'],'status':'invalid_json'}
        errors=check(case,observed)
        return {'id':case['id'],'status':'fail' if errors else 'pass','differences':errors}

def main():
    p=argparse.ArgumentParser(description='Test-only driver; NEVER point at production services.')
    p.add_argument('--driver',required=True,type=Path,help='absolute or relative local Python driver path')
    p.add_argument('--scenario',action='append',help='explicit scenario id, may be repeated')
    p.add_argument('--output',required=True,type=Path);p.add_argument('--timeout',type=float,default=10)
    a=p.parse_args();driver=a.driver.resolve(strict=True)
    if not driver.is_file() or a.timeout<=0:raise ValueError('local driver file and positive timeout required')
    cases=json.loads((HERE/'conformance-scenarios.json').read_text(encoding='utf8'))['cases'];ids={c['id'] for c in cases}
    if a.scenario:
        if set(a.scenario)-ids:raise ValueError('unknown scenario')
        cases=[c for c in cases if c['id'] in set(a.scenario)]
    results=[invoke(driver,c,a.timeout) for c in cases]
    a.output.write_text(json.dumps({'status':'fixture-driver-result-not-performance','results':results},ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('checked',len(results),'passed',sum(r['status']=='pass' for r in results))
    if any(r['status']!='pass' for r in results):raise SystemExit(1)
if __name__=='__main__':main()
