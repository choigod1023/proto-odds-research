# KBO 재연구 사전 규칙 — 2026-10-29

이 문서는 이번 후보의 검증·평가 성적을 계산하기 전에 작성했다. 2026 평가는 여러 선행 연구에서 이미 본 회고 집합이며 미열람 외부 검증이 아니다. 원래 KBO 203경기 중 과거28일 투수 원본 누락 5경기를 제외한 기존198경기를 고정한다. 나쁜 결과·높은 배당·낮은 확신을 이유로 제외하지 않는다. 운영 코드·DB·정책·배포를 변경하지 않는다.

## 자료와 인과성

- 2023 train710 → 2024 validation712 선택 → 2023+2024 재학습 → 2026 evaluation198. 평가는 2026-07-01 전152/이후46도 공개한다.
- 지정된 공통불펜 연구의 KBO cohort와 normalized pitching history, league-fit의 KBO score history, 원천 타자 JSON을 KBO 전용 입력으로 복사·해시 보존한다. 최종 실행은 이 커밋의 입력만 사용한다.
- 모든 특징은 `source.date < target.date`이며 동일 날짜 더블헤더도 현재 날짜 자료를 쓰지 않는다. 과거 더블헤더는 원천 id로 합산한다. 현재 박스 첫 투수·선발 이름·타순은 사용하지 않는다. 타자는 과거 경기의 팀 전체 AB/H/BB/HR/2B/3B를 합산한다. BF는 사용하지 않는다.
- 원천 투수별 허용실점 합계=상대 점수, 원천 id·날짜·팀·점수, 타자 H≤AB와 2B+3B+HR≤H, 중복/충돌, 기존28일 투수 완전성을 검사한다. 누락을 휴식0으로 바꾸지 않는다. 점수 원장의 날짜/팀 중복 모호성은 고유 투수 id 원장을 우선하여 더블헤더를 복구하고 score-only 고유 경기를 추가한다. 추가 점수원천과 투수원천의 같은 경기 점수는 일치해야 한다.
- 현재·미래 날짜의 점수/타자/투수 원본을 변형해도 해당 경기 특징이 불변인지 Python 회귀 테스트한다. 2026 결과를 변형해도 선택과 최종 학습계수는 불변이어야 한다.

## 특징과 식 (평가 후 재탐색 금지)

리그 득점환경 L은 해당 시즌의 과거 모든 보유 경기 양 팀 평균 득점에 20팀경기×4.5점 사전값을 더해 수축한다. 팀 평균 득점 O/실점 D는 `(합계+s*L)/(경기수+s)`. 시즌은 해당 연도, 최근은 과거28일이다. 기본 s=20. 자료가 없는 시즌 초기는 명시한 사전값으로 수축하며 개수를 증거로 기록한다.

기본 strength 특징 5개: 시즌 `log(O_h/D_h)-log(O_a/D_a)`; 최근28일의 같은 비율; 해당 홈/원정에서 `(득점-실점)/(n+20)/L`의 홈-원정 차; 이전 경기 이후 쉬는 일수 `min(7, gap-1)` 차(시즌 과거 경기 없으면7, 별도 개수 기록); 최근7일 경기수 차.

batting 2개: 시즌/최근28일 팀 전체 과거 타격의 `OPS* = (H+BB+100*.32)/(AB+BB+100) + (H+2B+2*3B+3*HR+100*.40)/(AB+100)` 홈-원정 차. HBP/SF가 없어 정식 OPS가 아닌 대리지표다. 현재 타순을 추정하지 않는다. 모든 팀의 같은 정의를 적용하며 과거28일 알려진 투수 경기 대비 타자 누락은 실패시켜 조용한 subset 선택을 막는다.

bullpen 3개: 과거28일 선발 평균 outs를 5경기×15outs로 수축한 값/3의 홈-원정 차; 과거28일 구원 `(K-BB+45*.20)/(outs+45)` 차; 의존도×부하 차. 의존도=`clip(1-수축선발outs/27,0,1)`; 부하=최근3일 구원outs/27. 과거 첫 투수만 선발로 분류하며 당일 선발은 사용하지 않는다.

후보를 아래 **28개 + baseline + market_null = 총30개**로 고정한다.

1. logistic 12개: strength / strength+batting / strength+bullpen / full의4묶음 × ridge {0.01,0.1,1}. `logit(p)=logit(reference)+clip(x/train_std,-6,6)β`. 학습 목적은 무승부 제외 평균 binary logloss + ridge×||β||²/2; intercept 없음. 스케일·계수는 학습에서만 추정한다.
2. negative_binomial 8개: s={20,60} × window={season,28days} × blend={0.1,0.3}. `mu_h=O_h*D_a/L*exp(H)`, `mu_a=O_a*D_h/L*exp(-H)`. H=`0.5*log((train_home_runs+20*4.5)/(train_away_runs+20*4.5))`. r=`clip(mean²/max(var-mean,1e-6),.5,100)`를 학습 양 팀 점수로 추정, Var=mu+mu²/r. 0..200 정수 PMF로 홈승/원정승/동점 질량을 계산하고 `P(home | non-draw)`를 반환한다. 남은 tail >1e-8이면 실패. 경기 종료 점수 모형의 근사이며 정확한 이닝·연장·홈말 종료 과정을 모델링하지 않는다.
3. pythagorean 8개: s={20,60} × exponent={1.5,2} × blend={0.1,0.3}. 같은 시즌 mu에 `p=logistic(exponent*log(mu_h/mu_a))`. MLB에서 추정한 지수를 KBO의 사실로 주장하지 않으며 후보 가설로만 비교한다.
4. 위 분포형 후보의 최종 p는 `logistic((1-blend)*logit(reference)+blend*logit(model))`.
5. reference baseline은 train/validation에서 픽스터 없음으로 devig 시장만, evaluation에서 **비교용 시장90%+픽스터 투표10%**. 시장은 양방향 역배당을 합1 정규화한다. 운영투표모형 재현을 뜻하지 않는다. `market_null`은 전 기간 순수 시장확률이다. 검증에서 baseline과 동률이어도 이름 tie 규칙을 그대로 적용한다. 평가에서 reference 가용성이 달라지는 한계를 밝힌다.

## 동결 선택과 정산

- primary: validation baseline 대비 적중률과 ROI가 모두 비악화인 후보(기준 baseline 포함) 중 logloss 최소 → Brier → 이름 순. hit_leader: validation 적중률 최대 → Brier → 이름. profit_leader: validation ROI 최대 → logloss → 이름. 셋을 별도로 저장하며 평가 성과로 서로 교체하지 않는다. 평가 전 validation 전체 성적·선택·재학습 계수를 `frozen-selection.json`으로 저장하고 해시한다.
- 단폴1만원·모든 경기 매수·월 손실 제한 없음. p≥.5 홈, 아니면 원정. 무승부는 원금 환불, 적중률/logloss/Brier 분모에서만 제외, ROI 분모에는 매수 포함. decimal 배당으로 원 단위 손익을 계산한다.
- 모든 후보 validation/evaluation 수치와 세 역할의 전체/7월1일 전후 성적·픽 변경 수·월별 손익·일마감 최대낙폭을 공개한다. 동일 경기 baseline과 주(월요일 시작) 단위 paired bootstrap seed20261029, 5000회, 95% percentile CI를 계산한다. 다중 후보 탐색 보정 없음·작은 표본·기존 회고 집합이므로 유의하지 않은 최적해나 미래 수익을 주장하지 않는다.
- 평가를 본 뒤 변수/기간/계수/후보를 재탐색하지 않는다. 구현 오류 수정은 원인과 변경을 기록하고 후보 사양은 유지한다. 나쁜 결과도 보존한다.

## 실제 검토한 근거

- [KBO 공식 팀 투수 기록](https://www.koreabaseball.com/Record/Team/Pitcher/Basic1.aspx): IP, SO, BB, R, ER의 구분을 확인했다. HTML이 열리지만 이번 실행의 수치 원천은 보존한 과거 원본이며 새 공식수치 수집을 주장하지 않는다.
- [Sean Dolinar, 2014, Run Distribution Using the Negative Binomial Distribution](https://community.fangraphs.com/run-distribution-using-the-negative-binomial-distribution/): 저자 자신의 MLB 점수분포 분석에서 과산포와 음이항 접근, 무득점 과소추정 한계를 검토했다. MLB 수치/분산을 KBO에 복사하지 않는다.
- [Steven J. Miller, A Derivation of the Pythagorean Won-Loss Formula in Baseball](https://arxiv.org/abs/math/0509698): 득실점 비율의 이론적 근거와 독립성·분포 가정을 검토했다. KBO 적합성의 증명이 아니다.

작성 시점에는 입력 스키마와 선행 코드만 확인했으며 이번 후보의 validation/evaluation 성적은 아직 계산하지 않았다.
