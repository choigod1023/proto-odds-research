# 역배 조합 수익 집중도 사후분석 — 2026-10-01

## 판단과 범위

기존 조건부 모델의 `b365/control/DD`와 `b365/control/any` 수익은 무승부 두 개 조합에 집중되어 있었다. 두 설정 모두 실현 순이익 상위 3조합을 제외하면 ROI가 음수로 바뀐다. 이는 기존 test 자료를 재사용한 사후탐색(post hoc)이며, 새로운 독립 검증이나 운영 전략 승인 근거가 아니다.

`DD`는 무승부 두 개라는 뜻이 아니라 역배 두 선택이다. 역배는 Bet365 H/D/A 중 최저 배당이 아닌 선택이며 무승부도 포함한다. `any`는 정배·역배 구성 제한이 없다. `control`은 각 선택 EV>=3% 필터를 적용하지 않는 대조군이다. 분석 전략은 모두 `conditional`이다.

이 문서는 앞선 읽기 전용 Python 실행 결과를 기록한다. 문서 작성 단계에서는 새 실험을 실행하지 않았다. 운영 코드·추천 정책·DB·배포·서비스 상태는 이 분석으로 변경하지 않았다. 운영장애 대응은 별도 담당 범위다.

## 기존 PR254와 데이터 근거

- PR: [#254](https://github.com/choigod1023/proto-odds-research/pull/254). 문서 작성 시 `gh pr view 254 --json url,state,headRefOid,mergeCommit,commits`로 확인한 상태는 `OPEN`, `mergeCommit=null`이다. 병합 또는 배포 완료로 해석하지 않는다.
- 사전 프로토콜 커밋: `67f0991276d95b69f188cc4a932446ae5de9cd01`.
- 평가 코드·결과 커밋 및 PR head: `dcc14dccd0f2fbbb6c5c4b61b5b350449a9d3c81`. 분석 원본 worktree의 HEAD와 일치한다.
- 고정 데이터 커밋: `e82cf59161e82e0fbcdc49ad02819e45c821a5c8`, [원본 미러](https://github.com/sosthene14/footballdataset/tree/e82cf59161e82e0fbcdc49ad02819e45c821a5c8/datasets).
- 원본 18파일, 6,591경기. 가격 누락 5경기를 후보에서 제외한 특징 생성 결과는 6,586경기다. 기존 `download()` 반환 메타데이터 전체를 결과 JSON의 `sources`와 비교하여 파일별 SHA256까지 일치함을 확인했다. CSV 직접 수정·별도 파싱은 하지 않았다.
- Serie A·Ligue 1의 2016/17~2024/25 자료. 2016~19 특징 준비, 2019/20~2021/22 학습, 2022/23 온도 보정, 2023/24·2024/25 평가다. 평가 1,372경기, 473 리그-날짜 예산, 설정별 332조합이다.
- 저장된 조건부 모델 계수·온도를 그대로 사용했다. 재학습, 기준 변경, 새로운 holdout 사용은 없었다.

읽은 파일의 절대 경로:

```text
C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/mixed-underdog-validation-20260928/scripts/evaluate_mixed_underdog.py
C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/mixed-underdog-validation-20260928/scripts/evaluate_calibration_underdog.py
C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/mixed-underdog-validation-20260928/scripts/evaluate_external_temporal_roi.py
C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/mixed-underdog-validation-20260928/docs/MIXED_UNDERDOG_RESULTS_20260928.json
C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/mixed-underdog-validation-20260928/docs/MIXED_UNDERDOG_PROTOCOL_20260928.md
C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/mixed-underdog-validation-20260928/docs/MIXED_UNDERDOG_20260928.md
```

## 무승부 개수별 분해

조합당 투자 1단위, 원배당 정산이다. ROI는 순손익/투자액이다. 예상확률은 각 선택 확률의 곱 `p1*p2`를 조합별로 계산한 뒤 평균했다. 실제 적중률은 두 선택이 모두 적중한 비율이다.

| 설정 | 무승부 선택 수 | 조합 | 적중 | 순손익(단위) | ROI | 평균 예상확률 | 실제 적중률 |
|---|---:|---:|---:|---:|---:|---:|---:|
| DD | 0 | 86 | 7 | -2.1360 | -2.4837% | 7.0187% | 8.1395% |
| DD | 1 | 98 | 6 | -10.8040 | -11.0245% | 7.0884% | 6.1224% |
| DD | 2 | 148 | 16 | +59.7450 | +40.3682% | 7.4281% | 10.8108% |
| DD | 전체 | 332 | 29 | +46.8050 | +14.0979% | 7.2218% | 8.7349% |
| any | 0 | 236 | 54 | -14.2018 | -6.0177% | 22.8535% | 22.8814% |
| any | 1 | 71 | 9 | -13.7300 | -19.3380% | 13.6996% | 12.6761% |
| any | 2 | 25 | 7 | +62.1850 | +248.7400% | 7.6426% | 28.0000% |
| any | 전체 | 332 | 70 | +34.2532 | +10.3172% | 19.7505% | 21.0843% |

DD의 무승부 0개는 배당상 약팀 승리 두 선택이며, 1개는 무승부와 약팀 승리다. 무승부 두 개의 +59.745단위가 나머지 -12.940단위를 상쇄했다. any에서도 무승부 두 개를 제외한 307조합은 -27.9318단위다. any의 무승부 0개에는 정배 승리도 있으므로 약팀 승리만의 성과로 해석하면 안 된다.

any를 역배 선택 수로 따로 나누면 0개 144조합/+10.5482단위, 1개 136조합/-34.7600단위, 2개 52조합/+58.4650단위다. 이 분류는 무승부 개수별 분류와 중첩되므로 합산하지 않는다.

## 큰 적중 제외 민감도

실현 순손익 내림차순으로 상위 1·3·5조합을 제외했다. 동률은 날짜, 리그 순으로 정렬했다. 제외 조합의 투자액도 빼므로 분모는 각각 331·329·327이다. 사후적으로 큰 적중을 골라 빼는 집중도 진단이지 실행 가능한 선택 규칙이나 미래 ROI 추정이 아니다.

| 설정 | 제외 수 | 잔여 순손익 | 잔여 ROI | 주 cluster ROI 95% 구간 |
|---|---:|---:|---:|---:|
| DD | 1 | +21.4050 | +6.4668% | -29.8185~+44.8617% |
| DD | 3 | -15.6810 | -4.7663% | -39.4133~+32.8966% |
| DD | 5 | -45.1810 | -13.8168% | -45.5148~+20.5086% |
| any | 1 | +19.5032 | +5.8922% | -24.4015~+38.7110% |
| any | 3 | -9.4468 | -2.8714% | -29.8238~+26.8325% |
| any | 5 | -31.3718 | -9.5938% | -33.7935~+17.3808% |

DD 상위 5조합:

1. 2023-10-23 I1, Fiorentina–Empoli 원정승 + Udinese–Lecce 무승부: 무승부 1개, 배당 26.400, 순이익 +25.400.
2. 2024-04-14 I1, Napoli–Frosinone + Sassuolo–Milan 모두 무승부: 배당 20.900, 순이익 +19.900.
3. 2023-08-19 F1, Lyon–Montpellier 원정승 + Toulouse–Paris SG 무승부: 무승부 1개, 배당 18.186, 순이익 +17.186.
4. 2024-04-15 I1, Atalanta–Verona + Fiorentina–Genoa 모두 무승부: 배당 15.750, 순이익 +14.750.
5. 2025-04-28 I1, Lazio–Parma + Udinese–Bologna 모두 무승부: 배당 15.750, 순이익 +14.750.

any 상위 5조합:

1. 2024-04-15 I1, 위와 같은 두 무승부: 순이익 +14.750.
2. 2025-04-28 I1, 위와 같은 두 무승부: 순이익 +14.750.
3. 2025-02-15 I1, Atalanta–Cagliari + Lazio–Napoli 모두 무승부: 배당 15.200, 순이익 +14.200.
4. 2024-03-16 F1, Lens–Nice + Nantes–Strasbourg 모두 원정승: 배당 13.200, 순이익 +12.200.
5. 2024-04-27 I1, Juventus–Milan + Lecce–Monza 모두 무승부: 배당 10.725, 순이익 +9.725.

추가로 최고 이익 주 전체를 제외하면 DD는 2025년 ISO 2주(+24.550단위, 5조합) 제외 후 ROI +6.8058%, any는 2024년 ISO 11주(+21.375단위, 4조합) 제외 후 +3.9263%다. 이는 상위 개별 조합 제외와 별개의 진단이다.

## 두폴 확률과 주별 cluster 불확실성

기존 `cluster_interval()`을 재사용했다. ISO 연도·주가 같은 두 리그의 기록을 함께 묶고 주를 복원추출한다. 난수 시드 `20260928`, 5,000회, 2.5·97.5 백분위 구간이다. 최종 보고는 기존 보고서와 동일하게 전체 473 리그-날짜, 76 ISO 주를 유지한다. 실제 베팅이 있는 주는 75개다. 하위집단 밖 또는 제외된 조합의 stake·손익·적중·예상확률·오차는 0으로 하여 주 범위를 보존했다. 분모가 0인 재표집은 기존 함수대로 제외한다.

| 설정 | 무승부 수 | ROI 95% 구간 | 실제−예상 차이(%p) | 차이 95% 구간(%p) |
|---|---:|---:|---:|---:|
| DD | 전체 | -23.5329~+56.2507% | +1.5132 | -1.2410~+4.4283 |
| DD | 0 | -66.2379~+77.9110% | +1.1209 | -4.0483~+7.5423 |
| DD | 1 | -74.1449~+66.0633% | -0.9659 | -5.0003~+3.6477 |
| DD | 2 | -19.8699~+105.0249% | +3.3827 | -1.1882~+8.3901 |
| any | 전체 | -21.7094~+44.6311% | +1.3339 | -3.2705~+6.0898 |
| any | 0 | -31.3257~+22.1360% | +0.0279 | -4.7119~+5.0198 |
| any | 1 | -66.3502~+38.4615% | -1.0236 | -8.0515~+7.4316 |
| any | 2 | +23.6956~+493.8001% | +20.3574 | +2.1465~+39.7109 |

DD 예상 적중 수는 23.9763회, 실제 29회다. any는 예상 65.5716회, 실제 70회다. 전체 확률 오차 구간은 모두 0을 포함하므로 지속적인 과소예측을 확정할 수 없다. 독립 가정하 모델 평균 기대 ROI는 DD -5.4405%, any -0.4398%로, 실현 양의 ROI와 구별해야 한다.

any 무승부 두 개는 예상 1.9106회 대비 실제 7회 적중했다. 보정 전 구간은 양수지만 25조합·23개 베팅 주에 불과하고, 결과를 본 뒤 분해한 하위집단이다. 새로운 무승부 정책의 유효성 입증으로 사용해서는 안 된다.

첫 번째 실행은 실제 베팅 기록만으로도 CI를 계산했다. 그 경우 DD 전체 ROI 구간은 -23.1066~+54.7930%, any는 -20.6755~+45.7224%였다. 두 번째 실행에서 전체 76주 범위로 맞췄고, 위 최종 표는 그 결과만 사용한다. 표집 범위가 다르므로 두 실행의 CI를 혼용하지 않는다.

## 시즌별 수익 집중

- DD 2023/24: 무승부 0/1/2개 손익은 +15.464/+16.666/+14.435단위, 합계 +46.565단위.
- DD 2024/25: -17.600/-27.470/+45.310단위, 합계 +0.240단위.
- any 2023/24: +15.9914/+0.8350/+14.4750단위, 합계 +31.3014단위.
- any 2024/25: -30.1932/-14.5650/+47.7100단위, 합계 +2.9518단위.

최근 시즌의 전체 이익 역시 무승부 두 개 적중이 다른 구성의 손실을 상쇄한 결과다. 리그별 이질성도 있다. DD의 F1 2024/25 ROI는 -47.0548%, any의 F1 2024/25 ROI는 -31.9188%다.

## 실제 실행 방법과 검증 범위

작업 디렉터리는 `C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/mixed-underdog-validation-20260928`이었다. Python 3.14.3, NumPy 2.5.1, SciPy 1.18.0 환경에서 PowerShell here-string을 `python -B -`에 전달했다. 진단 스크립트 파일은 만들지 않았으므로 별도의 진단 파일 경로는 없다. `-B`는 import 시 바이트코드 파일 생성을 막는다. 원 평가 스크립트의 `--output` 실행은 결과 파일을 쓰므로 이번 진단에서는 사용하지 않았다.

첫 실행은 `download → add_features → JSON의 conditional 모델로 predict → choose → settle → summarize` 순서였다. `composition`은 DD와 any, 리그 I1/F1, 시즌 23/24를 반복했다. 8개 리그·시즌·구성 결과의 `selection_sha256`, tickets, wins, profit_raw_units, roi_raw를 저장 JSON과 대조했다. 수치 허용오차는 `1e-10`이었다. 통합 두 설정도 선택 해시와 ROI가 일치했다. 개별 조합을 무승부 수·시즌·리그로 분해하고 손익 상위 제외 결과를 출력했다. 모든 assert를 통과했고 프로세스 종료 코드는 0이었다.

두 번째 실행에서는 아래 명령을 실제 사용하여 기존 보고서와 같은 전체 주 범위의 CI 및 최고 이익 주 제외 결과를 확인했다. 이 코드는 파일을 쓰지 않으며, 문서 작성 중 다시 실행하지 않았다.

```powershell
@'
import json
from pathlib import Path
from collections import defaultdict
import numpy as np
import scripts.evaluate_mixed_underdog as m
from scripts.evaluate_calibration_underdog import cluster_interval,week_key
j=json.loads(Path('docs/MIXED_UNDERDOG_RESULTS_20260928.json').read_text(encoding='utf-8'))
raw,src=m.download(); assert src==j['sources']; data=m.add_features(raw)
for comp in ['DD','any']:
 rec=[]
 for league in m.LEAGUES:
  for year in (23,24):
   rows=[r for r in data if r['league']==league and r['season']==year]
   t=m.choose(rows,m.predict(j['models'][league]['conditional'],rows),comp,'conditional','control','b365')
   for r in m.settle(rows,t):
    pair=t[r['date']];pred=float(np.prod([a['probability'] for a in pair])) if pair else 0.
    rec.append({**r,'league':league,'draws':sum(a['choice']==1 for a in pair) if pair else -1,'pred':pred,'gap':r['won']-pred})
 def ci(rs):return {'roi':cluster_interval(rs,'raw','stake'),'gap':cluster_interval(rs,'gap','stake')}
 def mask(test):return [{**r,**({} if test(i,r) else dict(raw=0.,stake=0,won=0,pred=0.,gap=0.))} for i,r in enumerate(rec)]
 print(comp,'FULL_UNIVERSE',len(rec),len(set(week_key(r['date']) for r in rec)),json.dumps({'all':ci(rec),**{str(d):ci(mask(lambda i,r:r['draws']==d)) for d in range(3)}}))
 ranks=sorted([i for i,r in enumerate(rec) if r['stake']],key=lambda i:(-rec[i]['raw'],rec[i]['date'],rec[i]['league']))
 print(comp,'TRIM_CI',json.dumps({str(k):ci(mask(lambda i,r:i not in ranks[:k])) for k in [1,3,5]}))
 weeks=defaultdict(lambda:[0.,0])
 for r in rec:weeks[week_key(r['date'])][0]+=r['raw'];weeks[week_key(r['date'])][1]+=r['stake']
 best=sorted(weeks.items(),key=lambda x:-x[1][0])[:3]
 print(comp,'TOP_WEEKS',json.dumps(best),'DROP_TOP_WEEK_ROI',(sum(r['raw'] for r in rec)-best[0][1][0])/(332-best[0][1][1]))
'@ | python -B -
```

검증 로그의 핵심은 `SOURCE_CHECK: files=18, raw=6591, usable=6586, hashes_match=true`, DD/any 각각 `reproduced=true`, `FULL_UNIVERSE 473 76`이다. 기존 결과 재현을 확인한 것이며 원천 데이터의 독립적 진실성이나 실제 배당 체결 가능성을 새로 확인한 것은 아니다.

## 재사용 test 및 해석 한계

- 2023/24·2024/25는 PR254에서 이미 평가 결과를 본 test다. 동일 표본을 다시 다운로드해도 새 검증이 되지 않는다. 무승부 분해·큰 적중 제외를 보고 새 규칙을 고르면 그 규칙은 별도의 미사용 기간에서 평가해야 한다.
- 기존 80설정 탐색에 이번 하위집단 탐색이 더해졌다. CI는 다중비교 보정 전이며, 양수인 하위집단만 채택하면 선택 편향이 생긴다.
- 두폴 예상확률 `p1*p2`는 경기 결과 독립 가정이다. 주 cluster CI가 이 가정을 검증하거나 보정하는 것은 아니다. 주 사이 의존성, 모델 추정 불확실성, 사후 정책 선택 불확실성도 모두 해결하지 않는다.
- 큰 적중 제외는 결과를 보고 시행한 민감도 분석이다. 제외할 조합을 사전에 알아낼 수 있다는 주장이 아니다. 제외 목록을 고정한 조건부 bootstrap 구간이다.
- 배당 관측 시각·당시 부상·라인업·로테이션·배당 이동 및 실제 Proto 체결 가격이 없다. 해외 비마감 Bet365 가격의 가상 정산이며 실현 가능한 운영 수익을 입증하지 않는다.
- 기존 보고서의 868개 테스트 및 58 subtests 통과 기록은 이전 작업의 기록이다. 이번 진단에서는 전체 회귀 테스트를 다시 실행하지 않았으며, 위 재현 assert와 진단 실행 성공만 직접 확인했다.
- 이번 작업으로 운영 정책·추천·DB·배포를 변경하지 않았다. 이 문서만 추가하며 다른 파일 수정, 커밋, push, PR 생성·수정·병합은 수행하지 않는다. 대상 worktree의 기존 운영장애 관련 변경은 다른 작업의 것으로 보존한다.
