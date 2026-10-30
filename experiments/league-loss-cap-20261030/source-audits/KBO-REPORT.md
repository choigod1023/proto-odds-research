# KBO 무제한 ROI 및 경기별 원장 검증 — 2026-10-30

운영 변경 없이 기존 로컬 자료만 읽어 계산했다. 월 한도 재생·확률/배당 필터 탐색·부트스트랩은 수행하지 않았다. 후보를 재학습하거나 평가 결과로 승격하지 않았다. 이 보고서에서 말하는 ROI는 단폴 동일 금액을 매 경기 매수한 수익률이다. 2폴·주별 구매 제한·리그 병렬 지갑 결과와 직접 같다고 보면 안 된다.

## 전달 파일

- `records.json`: {"records": [...]} 형식, 2,936행. 2024 검증 712×3모델 + 2026 회고 평가 198×4모델 + 실제 저장 입력 재생 2×4모델. 유일 키는 (cohort, model, event_id).
- `cohorts.json`: 검증/평가 910개 경기의 점수·가격·투표 및 저장된 class 선택, 별도로 2023 학습 입력 710경기. 학습 자료에 모델 예측을 새로 만들어 넣지 않았다.
- `uncapped-validation.json`: 구간별·월별 원단위 손익, ROI, 제외 이유, 정확한 원본 경로/바이트 SHA256.
- `normalization-checks.json`: 실제 Python 검증 집계.
- `paired-changes.json`: 사전 동결 e0와 baseline의 선택이 달랐던 경기별 차이.
- `extract_validate.py`: 표준 라이브러리만 사용하는 재현 스크립트. 지정 출력 폴더에만 파일을 쓴다.

## 코호트와 시점

- 학습 710경기: 2023-04-01~2023-11-13. 이 구간으로 selection_fit을 계산한 원본 결과를 사용했다.
- `frozen712_validation_selection_2024`: 2024-03-23~2024-10-28, 712경기. 후보 선택에 사용한 구간이며 독립 평가가 아니다.
- `frozen198_evaluation_reused_2026`: 2026-03-29~2026-07-23, 198경기. 2023+2024 총 1,422경기로 보정계수를 재학습한 원본 final_fit을 사용했다. 기존 203경기에서 과거28일 투수 자료가 부족했던 기존 제외5를 그대로 유지한 집합이다. 이번 작업에서 성과에 따른 제외는 하지 않았다.
- 2026-07-01 이전 152경기, 이후 46경기. 이 경계는 기존 원본 프로토콜의 구분이다. 152경기의 마지막 날짜는 06-19다.
- `saved_operating_evaluation_replay_2026_10`: 2026-10-26~10-27의 정산 가능한 2경기. 최신 비교 자료의 10-29 경기는 fixed91이 no_valid_votes로 unavailable이며 operating도 abstained이므로 만들어 채우지 않았다. 원본 비교에는 공식 결과가 없다고 기록되어 있다.

후보 e0는 2024 검증으로 선택한 뒤 2026을 평가했지만, 2026 자료는 여러 선행 연구에서 이미 열람했다. 따라서 새로 미열람 상태에서 검증한 기간은 없다. 2025는 보정계수 학습에 포함하지 않지만 과거 팀 상태 갱신에는 쓰인다.

## 모델 정의

- `market_null`: 저장된 두 가격에 Shin 마진 제거를 적용한 순수 시장 대조군. 해당 연구 원장에 저장된 홈 확률·선택을 그대로 사용했다.
- `baseline`: 검증에서는 market_null과 동일하다. 평가에서는 0.9×Shin 홈시장확률 + 0.1×저장된 홈 추천 작성자 투표비중이다. 별도 학습 모델이나 실제 과거 운영 버전을 완전히 재생한 것이라고 부르지 않는다.
- `e0:residual:0.03`: 2024 검증에서 사전 선택·동결된 후보. 득점차 가중 Elo 상대강도 차이 x에 offset ridge residual을 적용한다. p_home=sigmoid(logit(reference)+clip(x/scale,-6,6)×beta), ridge=.03. Elo K=.08, half-life=120일, 연도 유지율=.35; 홈 logit=.12+r_home-r_away. 같은 날 전체 결과는 공통 날짜 시작 상태로 계산한 뒤 합산 갱신하므로 그날 앞 경기 결과를 같은 날 예측에 쓰지 않는다.
  selection_fit(2023): scale=0.3034738474034072, beta=-0.04499373652751718. final_fit(2023+2024): scale=0.28102288647783685, beta=-0.06637396467535583. 원본의 24후보+2기준 가운데 동결된 primary만 정규화해 비교했으며 다른 후보의 평가 성과로 교체하지 않았다.
- `tipster_vote_saved_choice`: evaluation 원장의 저장된 tipster_vote 선택. 투표비중은 확률 보정 결과가 아니므로 probability를 넣지 않고 vote_share로 구분했다.
- `fixed91_same_electorate`: 실제 저장 입력 비교 원장의 frozen_market90_vote10. 같은 snapshot electorate의 고정9:1 대조군으로, 실제 배포된 과거 정책의 정확한 재생은 아니다.
- `operating`: snapshot에 저장된 실제 운영 최종확률·선택. 위 두 경기에서는 fixed91과 같은 원정 선택이지만 확률은 각각 저장된 반올림 정밀도가 다르다.
- `market_reference`, `home_always`: 같은 두 경기의 저장 시장선택 및 무조건 홈 대조군. home_always에 확률을 지어 넣지 않았다.

## 무제한 실계산 결과

각 경기 1만 원. 승리 odds−1, 패배 −1, 무승부 환불 0. ROI 분모는 환불을 포함한 전체 매수 원금이다. 실제 추출 표본에는 무승부가 없다. Decimal 정산으로 저장된 정수 원단위 손익과 대조했다.

### frozen198_evaluation_reused_2026

- baseline: 198경기, 119승, 손익 -146,600원 / 투입 1,980,000원, ROI -7.404040%.
- e0:residual:0.03: 198경기, 119승, 손익 -144,500원 / 투입 1,980,000원, ROI -7.297980%.
- market_null: 198경기, 117승, 손익 -208,400원 / 투입 1,980,000원, ROI -10.525253%.
- tipster_vote_saved_choice: 198경기, 112승, 손익 -180,500원 / 투입 1,980,000원, ROI -9.116162%.

### frozen712_validation_selection_2024

- baseline: 712경기, 411승, 손익 -815,800원 / 투입 7,120,000원, ROI -11.457865%.
- e0:residual:0.03: 712경기, 417승, 손익 -708,100원 / 투입 7,120,000원, ROI -9.945225%.
- market_null: 712경기, 411승, 손익 -815,800원 / 투입 7,120,000원, ROI -11.457865%.

### saved_operating_evaluation_replay_2026_10

- fixed91_same_electorate: 2경기, 1승, 손익 -4,700원 / 투입 20,000원, ROI -23.500000%.
- home_always: 2경기, 1승, 손익 +200원 / 투입 20,000원, ROI +1.000000%.
- market_reference: 2경기, 1승, 손익 -4,700원 / 투입 20,000원, ROI -23.500000%.
- operating: 2경기, 1승, 손익 -4,700원 / 투입 20,000원, ROI -23.500000%.

검증에서 e0의 baseline 대비 개선은 14경기 선택 변경으로 +107,700원(+1.512640%p)이다. 평가에서는 6경기만 바뀌어 +2,100원(+0.106061%p)이다. 양쪽 모두 적중 119회이며, 이 작은 수익차는 적중 증가 없이 선택 가격 구성이 달라져 생겼다.

2026의 7월 전 e0/baseline ROI는 -6.565789%/-6.901316%, 7월 이후는 -9.717391%/-9.065217%다. 후반 구간에서 e0가 오히려 3,000원 나빠졌다. 순수 시장의 7월 이후 +1.108696%만 보고 사후 승격해서는 안 된다.

## 기존 ROI에 한도가 적용되었는가

이번에 확인한 frozen712/198 결과는 명시적으로 월 한도 없는 전 경기 매수 결과다. 원본 PROTOCOL.md의 “단폴1만원, 전체 매수, 월 손실제한 없음”과 metrics()의 총손익/(경기수×10000) 정의를 확인했다. 원본 비교 원장도 monthly_cap_applied=false다. 재계산한 세 모델×두 기간 총6개 집계의 경기수·정수손익·ROI가 원본 results.json과 일치했다. 다른 연구의 2폴 결과까지 모두 무제한이라고 일반화하지 않는다.

따라서 이 숫자에서 “한도를 없애 얻는 추가 개선”을 다시 계산하면 같은 기준을 중복해서 비교할 수 있다. 제한을 적용한 전략은 일부 경기만 매수하므로 손익의 합과 매수 원금 분모가 함께 변한다. 한도가 막은 후속 경기에 좋은 수익이 몰렸다면 무제한 ROI가 더 높아질 수 있지만, 위 원장만으로 실제 한도 대비 우월성을 확정하지 않는다. 부모의 동일 경기·정산 시점 가정에 따른 재생이 필요하다.

## 검증 및 시점 한계

- 2,730개 후보/기준 기록에서 score→hit→가격→원단위 손익을 직접 재계산하고, 현재 작업 폴더 archived-reproduction의 같은 기록과 완전 일치하는지 검사했다.
- 평가 198개 baseline은 저장된 시장확률·작성자 투표로 9:1 식이 1e-12 이내에서 일치했다.
- 저장 research probability는 홈 기준이다. records.json의 probability는 실제 선택한 결과 기준으로 바꾸고 home_probability를 함께 보존했다.
- 모든 2,936행의 필수 필드·숫자 유한성·배당>1·ISO 날짜·모델별 경기 중복·손익 공식을 실행 검증했다.
- 역사 자료는 경기 날짜만 있어 kickoff에 YYYY-MM-DD를 쓰고 kickoff_precision=date_only로 표시했다. 이를 실제 자정 시작 시각으로 주장하지 않는다.
- settled_at과 available_at은 모든 행에서 생략했다. 부모가 day+1/day+2 정산 민감도를 별도로 적용해야 한다. 최근2경기의 원본 결과 observed_at은 source_result_observed_at로만 보존했으며 정산 시각으로 쓰지 않았다. source_as_of/captured_at 역시 원본 필드의 뜻 그대로이며 과거 입력이 그때 실제 구매 가능했음을 새로 인증하지 않는다.
- 최근 운영 입력 비교는 capture_kind=retrospective_saved_input_replay다. comparison.json의 정규화된 결과를 사용했으며 전체 원본 운영 원장 hash chain을 이번 작업에서 재검증한 것은 아니다. 결과 출처도 저장 Proto 피드이며 독립 공식 인증을 수행하지 않았다.

## 다음 KBO 변경을 판단할 수 있는 반증 조건

1. 동결 e0는 당장 우승 후보로 승격하지 않는다. 이후 확보하는 동일 경기·동일 가격의 전향 자료에서 baseline 대비 선택 변경 손익이 개선되는지 검증한다. 후반46경기의 악화가 반복되거나 사전 지정한 평가 창의 손익/확률오차가 개선되지 않으면 상대강도 보정 개선 가설을 기각한다.
2. 평가 baseline의 투표 추가 효과는 2024 검증에서 선택할 수 없다(당시 투표 없음). 투표가 저장된 새 과거/전향 집합으로 학습과 평가를 시간순 분리한 뒤, 작성자 부족/시장과의 불일치 조건을 미리 고정해 검증한다. vote_share를 확률로 간주하거나 198경기 사후 승자를 승격하면 안 된다.
3. 단순 확률/배당 필터는 부모가 학습 구간에서만 정해 후속 평가에 고정한다. p×odds와 실현 수익을 구분하고, selected outcome 확률을 사용한다. 이 작업은 필터나 최적 경계를 계산하지 않았다. 2024 순수시장 baseline에 고른 경계가 2026 9:1 baseline에 그대로 유효하다는 가정도 검증 대상이다.

## 정확한 입력 파일

- `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\kbo\evaluation-ledger.jsonl.gz`
  - bytes=85223, SHA256=616a7d826c51e6fee2c2507a77592388ca9b45bf0cff93767baf333ff660fa77
- `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\kbo\frozen-selection.json`
  - bytes=43349, SHA256=e15c0158ce2e02d80ff589db2b5a1ae8cb09e110144fc677b88ea464dd4eb195
- `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\kbo\PROTOCOL.md`
  - bytes=7326, SHA256=e8944bc3b14bbebbaa440aa86079fe761df849b45c57208d9498d640f92ba585
- `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\kbo\results.json`
  - bytes=50122, SHA256=91defbcef34f01e3ccea4c22ee9218cb8d183b4fc7beb0d7771a74b2da90f6f8
- `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\kbo\source-audit.json`
  - bytes=505, SHA256=587aa1e6ab536a1a474474c451aefae564eeda15f0074dd780efa4bdf62bbc6f
- `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\kbo\sources\cohort.jsonl.gz`
  - bytes=20352, SHA256=e75c5ea74769afe7917c7339ecd082518cfa36ee095ef54eb6343d74110608c4
- `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\experiments\baseball-information-stepchange-20261029\kbo\validation-ledger.jsonl.gz`
  - bytes=279628, SHA256=00720f6ba5577422a8573a928843dad99751347ee8d02e26744be23f73d0fcf1
- `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\baseball-information-stepchange-20261029\scripts\kbo_opponent_dynamic_research.py`
  - bytes=27110, SHA256=ea8823596732af943044813cea9e1a505031ac3e6de89d55940e67c47058e0aa
- `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\league-loss-cap-ablation-20261030\data\model_candidates\kbo\archived-reproduction.jsonl.gz`
  - bytes=101653, SHA256=4f5bc713a6487585ea5d42aff6371043b55cdaacdef3690c97ea67e54e2c1773
- `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\league-loss-cap-ablation-20261030\data\model_candidates\kbo\frozen-e0.json`
  - bytes=2214, SHA256=e5501cbc1a545d272d4de1408564ab76af0ecd75b0ff7c9b0232e3a4f34ef2de
- `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\league-loss-cap-ablation-20261030\docs\research\2026-10-25-research-scope-correction.md`
  - bytes=3351, SHA256=9eaa7a2c20be065f0478689edf563552b65df4fc13518ba91a502aceb32aab3d
- `C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\league-loss-cap-ablation-20261030\experiments\operating-ledger-comparison-20261029\comparison.json`
  - bytes=1122647, SHA256=a04285b6a14c09415e713cdc0e4b887073eb97da4c41d10b24b03631053c8223

## 재현

```powershell
python "C:\Users\user\Documents\ChatGPT\sportstoto-worktrees\league-loss-cap-ablation-20261030\.local-test\KBO\extract_validate.py"
```

브랜치·운영 코드·DB·배포를 바꾸지 않았고 커밋·push·PR을 만들지 않았다. 모든 산출물은 요청한 `.local-test/KBO/` 안에 있다.
