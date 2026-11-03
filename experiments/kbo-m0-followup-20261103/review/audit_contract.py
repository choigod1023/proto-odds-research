"""후속 연구의 고정 원천과 개발 레이블 경계만 읽기 전용 검사한다."""
import collections,gzip,hashlib,json,subprocess,sys
from datetime import date,timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent;COMMIT='ed11ca0561e485319f2b951409287f42167f42b5';ORIGIN=Path(r'C:\Users\user\Documents\Codex\kbm0plus');BASE='experiments/kbo-m0-evolution-20261103/'
def sha(b):return hashlib.sha256(b).hexdigest()
def need(v,msg):
 if not v:raise ValueError(msg)
def readgz(p):return [json.loads(x) for x in gzip.decompress(p.read_bytes()).splitlines()]
def idx(xs):
 d={x['fixture_id']:x for x in xs};need(len(d)==len(xs),'중복 경기');return d
def main():
 manifest=json.loads((ROOT/'source-manifest.json').read_bytes());contract=(ROOT/'contract.json').read_bytes();need(sha(contract)==manifest['contract_sha256'] and manifest['source_commit']==COMMIT,'계약/원천 해시');spec=json.loads(contract)
 need(spec['recipe_frozen']['baseline']=='original M0 baseL2=10' and spec['recipe_frozen']['distribution']=='NB2alpha=.3 baseL2=10' and spec['recipe_frozen']['weights']==[1/3]*3 and spec['recipe_frozen']['aggregation']=='average unconditional3way then derive conditional2way','레시피 변경')
 source_count=0
 for path,want in manifest['source_files'].items():
  need(path.startswith(BASE),'원천 경로');relative=path[len(BASE):];local=ROOT/'vendor/evolution'/relative;blob=subprocess.check_output(['git','-C',str(ORIGIN),'show',COMMIT+':'+path]);need(sha(blob)==want and sha(local.read_bytes())==want and local.read_bytes()==blob,'고정 원천 불일치 '+relative);source_count+=1
 fitpath=ROOT/'inputs/fit2023-labels.jsonl.gz';need(sha(fitpath.read_bytes())==manifest['fit2023_labels_sha256'],'2023 레이블 SHA');fit=idx(readgz(fitpath));common=ROOT/'vendor/evolution/common';all_labels=idx(readgz(common/'inputs/development-labels.jsonl.gz'));train=readgz(common/'inputs/train-actual.jsonl.gz');games=idx(train);years=collections.Counter(r['season'] for r in train);need(dict(years)=={2023:723,2024:728,2025:731},'연도 모집단');ids23={r['fixture_id'] for r in train if r['season']==2023};ids24={r['fixture_id'] for r in train if r['season']==2024};need(len(fit)==723 and set(fit)==ids23 and ids23.isdisjoint(ids24),'2023 레이블·2024 평가 집합')
 for fid,y in fit.items():need(y==all_labels[fid] and y['assumed_completed_date'][:4]=='2023' and y['status']=='final' and all(type(y[k]) is int and y[k]>=0 for k in ('home_score','away_score')),'2023 원천 레이블 '+fid)
 cutoff=min(date.fromisoformat(r['event_date']) for r in train if r['season']==2024)-timedelta(days=7);fitrows=[r for r in train if r['season']==2023 and date.fromisoformat(r['event_date'])<=cutoff];need(len(fitrows)==723,'7일 경계에 따른 학습 수')
 sys.path.insert(0,str(common));import core
 loaded=core.load();loaded_years=collections.Counter(y['assumed_completed_date'][:4] for y in loaded['labels'].values());need(dict(loaded_years)=={'2023':723,'2024':728,'2025':731},'공통 레이블 접근 범위');reference=core.baseline_reference();need(reference['development_model']['n_train']==1451 and reference['final_model']['n_train']==2182,'기존 모델 학습 범위')
 print(json.dumps({'상태':'계약원천_확인_레이블노출주의','원천커밋':COMMIT,'원천파일_해시일치':source_count,'학습2023_레이블':len(fit),'평가2024':len(ids24),'2024첫경기':min(r['event_date'] for r in train if r['season']==2024),'학습마감':cutoff.isoformat(),'공통core_load_레이블연도별':loaded_years,'공통기준개발모델_학습경기':1451,'공통기준최종모델_학습경기':2182,'실제예측코드감사':False,'핵심제약':'공통 core.load는 2024/2025 레이블도 읽으므로 2023 전용 fit 코드와 평가 레이블 접근 경계를 별도 입증해야 한다'},ensure_ascii=False,sort_keys=True,indent=2))
if __name__=='__main__':main()
