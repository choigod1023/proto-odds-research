"""각 후보의 공통 가드 반영 전후 고정 산출물과 공통 코드를 대조한다."""
import json,hashlib,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent;PREFIX='experiments/kbo-m0-evolution-20261103/'
CASES=[('recency',r'C:\Users\user\Documents\Codex\kbmrec','1110b13e7a1b552607cbeed53468cf15ca3bfbda','30ca43bdda624480f2ff57c09537a516bc967d1f'),('opponent',r'C:\Users\user\Documents\Codex\kbmopp','8c708d0915f08b2f17c51e34b266cafc574b1035','8c6da19b054d2ad4db591ca40ad780b30e409b78'),('distribution',r'C:\Users\user\Documents\Codex\kbmdist','fddc7f998d4b1860867601529192031f1de188de','0c5da7822939f6140640415c0bb3b03103740e1b')]
def command(repo,*args):return subprocess.check_output(['git','-C',repo,*args])
def tree(repo,commit,path):
 lines=command(repo,'ls-tree','-r',commit,'--',path).decode().splitlines();return {ln.split('\t',1)[1]:ln.split()[2] for ln in lines}
def sha(x):return hashlib.sha256(x).hexdigest()
def main():
 out={};common=sha((ROOT/'common/core.py').read_bytes());manifest=sha((ROOT/'common/manifest.json').read_bytes())
 for name,repo,before,after in CASES:
  subprocess.check_call(['git','-C',repo,'merge-base','--is-ancestor',before,after]);a=tree(repo,before,PREFIX+name+'/');b=tree(repo,after,PREFIX+name+'/');changed=[k for k in a if a[k]!=b.get(k)]
  if changed:raise ValueError('고정 산출물 변경: '+str(changed))
  for f,want in (('common/core.py',common),('common/manifest.json',manifest)):
   if sha(command(repo,'show',after+':'+PREFIX+f))!=want:raise ValueError('공통 가드 해시 불일치 '+name)
  out[name]={'예측동결커밋':before,'가드반영커밋':after,'기존후보파일불변':len(a),'추가파일':sorted(set(b)-set(a))}
 print(json.dumps({'공통core_SHA256':common,'공통manifest_SHA256':manifest,'후보별':out},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
