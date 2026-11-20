# KBO 상대강도 온라인 상태 연구 — 성과 계산 전 고정

2026-10-29. 본 후보 성과를 계산하기 전에 커밋한다. 기존 회고 평가를 재사용하며 미열람 외부 검증이 아니다. 운영 코드·DB·서버를 변경하지 않는다.

## 입력과 시점

지정된 league-retune 연구의 cohort/pitching/scores 원본과 이전 대조 결과·감사 자료를 이 폴더에 복사하고 SHA256으로 보존한다. 타자/당일 투수는 사용하지 않는다. 고정 train710(2023), validation712(2024), evaluation198(2026, 7월 전152/이후46), 원래203 중 기존 제외5 유지. 과거 원장 전체를 고유 pitching official_id로 복원하고, score-only 날짜/팀 앵커를 추가하되 충돌은 실패한다. 올스타4 제외, 합성연도 접두어3333/4444/5555/6666/7777/9999는 MMDD와 말미 실제 연도를 검사한다. 더블헤더는 서로 다른 ID를 모두 갱신한다.

같은 날짜 전체를 하나의 rating period로 처리한다. 날짜 시작에 상태를 감쇠한 공통 snapshot으로 모든 목표와 관측 경기의 예측을 계산하고, 그 뒤 해당 날짜 관측의 gradient/Hessian을 합쳐 한 번만 갱신한다. 현재/미래 점수 변형, 같은 날짜 순서 변경에 해당 날짜 특징은 불변이어야 한다. 날짜가 지난 결과만 사용한다. 2025/26은 당시 과거 상태 갱신만 허용하며 보정계수 학습에 포함하지 않는다.

## 상태 family 두 가지와 정확한 24후보

모든 팀을 중립0에서 시작한다. 직전 날짜와의 간격 d에 평균 상태는 a=2^(-d/half_life)로 감쇠하고 연도가 바뀌면 season_keep^(연도차)를 추가로 곱한다. 각 날짜 모든 팀을 함께 감쇠한다. 팀별 실제 경기수를 증거에 남긴다. 같은 날짜 경기 입력은 ID 정렬 후 합산한다.

1. **margin Elo**: scalar r. 홈승 q=sigmoid(.12+r_h-r_a), 무승부 y=.5. 관측마다 w=min(2, log(1+|득점차|)/log(4)); 무승부 w=1. gradient delta=K*w*(y-q), 홈에 +delta, 원정에 -delta를 당일 snapshot에서 누적한다. 설정 e0=(K .08, half120일, keep .35), e1=(.16,90,.50), e2=(.28,60,.65). 과거 상대의 당시 강도에 따라 같은 승리도 다른 갱신량을 만든다. 보정 특징 x=[r_h-r_a], 혼합용 logit=.12+x.
2. **attack/defence quasi-Poisson diagonal Laplace**: 팀별 공격 A, 수비 D 및 각각 분산 V. 초기분산 .08, 최대분산 .25. 당일 mu_h=4.5*exp(.06+A_h-D_a), mu_a=4.5*exp(-.06+A_a-D_h). 선형예측치는 [-2,2]로 제한한다. 각 득점 y의 gradient g=(y-mu)/2와 diagonal Hessian h=mu/2를 공격 +g/수비 -g로 누적한다. 2는 과산포 완화 상수이며 KBO 추정 사실이 아니다. Vnew=1/(1/Vprior+sum(h)), Mnew=Mprior+Vnew*sum(g). 평균은 [-1,1] 제한. Vprior=min(.25,a²*V+process*d+.08*(1-season_keep²) if year changed). 마지막 항은 연도 전환에만 더한다. 설정 a0=(process .0005, half180, keep .35), a1=(.002,120,.50), a2=(.006,60,.65). 공격·수비 합산분산 U=V_Ah+V_Aa+V_Dh+V_Da, c=sqrt(1+pi*1.8²*U/8). 보정 특징 x=[(A_h-A_a)/c,(D_h-D_a)/c]. 혼합용 logit=1.8*(.12+A_h-A_a+D_h-D_a)/c. 방어력이 높을수록 상대 기대득점이 줄어드는 부호다. 완전한 결합 posterior나 정식 Glicko가 아닌 대각근사이며, 득점 기반 승리확률의 1.8지수 역시 후보 가정이다.

각 family의 3설정 × 다음 4출력식 =12, 총24:

- offset residual ridge lambda={.03,.30}: logit(p)=logit(reference)+clip(x/train_std,-6,6) beta. 절편 없음. 무승부 제외 평균 logloss+lambda*||beta||²/2 최소화; std 하한1e-6. 2023 fit→2024 선택, 2023+24 fit→2026 평가. 실제 선수/타순·기존 팀평균 특징은 넣지 않는다.
- logit blend weight={.15,.35}: logit(p)=(1-weight)*logit(reference)+weight*state_logit. 보정계수 없음.

기준2: baseline은 2023/24 순수 Shin 시장, 2026은 기존 동결연구 시장90%+투표10%; market_null은 전 기간 순수 Shin. 독립 구현의 Shin을 src/devig.py 및 이전 원장의 기준 확률과 atol1e-12/rtol0 비교한다. 이 baseline은 운영 예측 재현이 아니다. 평가에만 투표가 존재하는 입력 차이를 명시한다.

## 선택, 동결, 정산

primary는 검증 시장 대비 적중/원단위 손익이 모두 비악화인 후보 중 logloss→Brier→이름 최소(기준 포함). hit_head는 적중 최대→Brier→이름, profit_head는 손익 최대→logloss→이름. 검증 전체 원장·선택·2023과2023+24 계수를 저장한 뒤 평가하며 평가 성과로 교체하지 않는다. frozen-selection을 별도 커밋하고 evaluate에서 입력/코드/프로토콜 해시를 확인한다.

모두 동일198/동일 배당, p>=.5 홈, 단폴1만원, 전체 매수, 월 손실제한 없음. 무승부 환불·적중/logloss/Brier에서 제외·ROI는 전체 매수198만원 분모. Decimal로 정수 원단위 정산한다. 전체/7월 전후/월별 적중·손익·ROI·logloss·Brier·일마감낙폭·픽 변경 수를 보존한다. 세 선택 head와 기준 비교는 월요일 시작 주단위 paired bootstrap 5000회/seed20261029/percentile95%로 적중률 및 ROI 차이 구간을 계산한다. 유의성·미래 수익을 주장하지 않는다.

## 재현/검사와 한계

원천·정규화 상태 관측·모든 날짜 팀상태·목표 특징·후보 사양·스케일/계수·검증/평가 확률·정산 원장을 저장한다. 실제 Python tests로 상대강도 반응, 공격/수비 부호, 휴식/연도 수축, 더블헤더/동일날 snapshot 불변, 현재/미래 변형 불변, 2026 결과 변형시 선택·계수 불변, 모든 후보 coverage/환불·독립 기준/정산, coefficient 재학습·재현을 확인한다. float atol1e-12/rtol0, decision와 정수 손익 exact. 새 상태가 0이 아니며 실제 수식/픽에 들어가는지 검사한다.

선수라인업·당일 선발·구장·이닝/연장/홈말 종료·상대전적 상호작용 계수는 다루지 않는다. 결과 아카이브 관측날짜는 있으나 당시 공개/수집시각 및 실제 구매 가능성은 입증하지 못한다. 2026은 기존 반복 연구에 노출되어 있다. 후보 탐색 다중비교 보정 없음. 상대강도 보정은 팀 상태 자체를 뜻하며 특정 팀쌍 matchup 별도계수는 넣지 않는다.

## 1차 근거와 본 연구 가정의 구분

- [Glickman 1999, 저자 공개 논문](https://www.glicko.net/research/glicko.pdf): 동적 paired comparison과 불확실성 갱신의 근거. 본 연구의 Elo/공수 모형은 논문 알고리즘의 정확한 복제가 아니다.
- [Crowder, Dixon, Ledford, Robinson 2002, 원저](https://academic.oup.com/jrsssd/article/51/2/157/7120674): 시간에 따라 공격/수비가 변하는 확률모형과 빠른 근사 접근. 축구 연구이며 KBO 적용·과산포2·half-life·지수1.8의 정확성을 보증하지 않는다.
- [Dixon & Coles 1997, 원문](https://ajbuckeconbikesail.net/wkpapers/Airports/MVPoisson/soccer_betting.pdf): 상대 공격/수비 결합 점수모형과 시간가중의 선행 근거. 본 실험은 축구 저득점 보정이나 해당 논문의 수익결론을 KBO에 옮기지 않는다.

작성 시 이번24후보 성과는 계산하지 않았다.
