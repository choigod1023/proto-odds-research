# 실제 자료 접합·회고 시나리오 사전 등록

부모 보완 계약 `1770f8b3`과 모델 등록 `b12cf82a`, `61f3ec7d`, `9babf4e1`을 유지한다. 실제 훈련·검증 성적 계산 이전에 이 매핑을 고정한다. 데이터 PR #511 `b0833a6`; raw rows SHA256 `66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c`, labels `ef32ffe806b41521f5bd53c12d0eea1362bd44293181fc3ebb7c24c6fcb249ca`, prior-projected gzip `eca78a16ca59ccc1de6eca47713acded513541c59cddbbb4d8dd193e7c5b27ae`. 이들은 원본 바이트 SHA다. 데이터 선발 시 2,913경기, 2023~26 원본이며 입력 원본은 수정하지 않는다.

선수 입력은 `prior-projected-features.jsonl.gz`의 `projected_from_prior_games`만 사용하고 원본 rows.features 또는 타깃 경기의 실제 선수 ID를 읽어 선수 정보를 만들지 않는다. 이전 날짜 팀 타순과 5인 선발 로테이션은 사전 확정 선수 명단이 아닌 가정이다. 모든 feature의 `source_fixture_ids`와 `latest_source_date`, 내부 객체의 모든 `date`·`fixture_id`, projected lineup source·rotation candidate가 타깃 ID와 다르고 target date보다 엄격히 과거인지 확인한다. 누락 또는 위반은 제외하거나 중단하고 덮어쓰지 않는다.

각 숫자 변환은 고정한다. 팀 전력은 최근 팀 경기 `mean(runs_for-runs_against)`; 휴식은 제공된 과거 일수 그대로. 예상 타순은 9명 중 유효한 과거 타자 이력의 합 `(sum(hit)+sum(bb))/(sum(ab)+sum(bb))`이며 해당 4개 수치가 없는 기록은 사용하지 않고 분모가 0이면 null이다. 이는 OPS/wOBA가 아닌 제한된 안타·볼넷 proxy다. 선발은 과거 등판 `sum(er)*27/sum(outs)`이며 분자가 없거나 아웃 합이 0이면 null; 계산된 아웃은 원본의 야구 표기 기준 아웃 그대로이며 공식 ERA가 아니다. 불펜은 이전 1~2개 달력 날짜의 구원투수 아웃 합이고 연투 시간·투구수·완전한 구원기록으로 해석하지 않는다. 구장 수치 팩터는 없는 채 null이다. 0은 실제 0이며 결측은 null로 보존한다. 모든 변환은 2024 이전 2023~24 훈련·2025 검증·2026 진단에 똑같이 적용하고 분포 추정, 6개 ablation, alpha {1,10,100}은 기존 등록을 따른다.

실제 kickoff/decision/완료/수신/배당관측 null은 null로 보존한다. `retrospective_unproven`에만 명시적 `--assume-prior-calendar-day`를 요구하며 과거 날짜 결과 완료를 가정한다. 타깃 날짜는 원본 kickoff 날짜가 있으면 그 날짜, 없으면 `score:YYYY-MM-DD:...` 또는 원본 fixture ID의 선행 `YYYYMMDD`에서 추출하고 불일치/모호성은 중단한다. 가정 날짜는 `assumed_completed_date` 별도 필드로 표현하며 실제 completed_at/received_at/official revision을 발명하지 않는다. 훈련 라벨의 가정 날짜는 다음 fold의 첫 target 날짜에서 적어도 7일 전이어야 한다. strict PIT 경로는 변경 없이 실제 시각과 수신 증거를 요구한다.

타깃 테스트 라벨은 예측 저장 후에만 읽고 점수화한다. archived price는 시점/체결 미인증이므로 기본 ROI 비교에서 모두 제외한다. opt-in `--archived-price-scenario`에서만 동일 완전 2-way 가격과 동일 경기의 고정 확률 최댓값 선택, 동률 홈 우선, 2-way 실제 무승부 환불로 별도 단위 손익을 계산한다. 라벨 시장 규칙은 원본 `draw_void_refund`를 유지한다. 무승부 60경기 중 원본 가격은 모두 null이고 그 중 46경기는 CSV 가격 1/1로 대체되어 복구 불가하므로 가격 표본은 결과 조건부로 선택된 편향 표본이다. 가격 시나리오와 확인된 ROI=미산출을 반드시 따로 기록한다.

원본 feature 행, 원본 2023~25 라벨로 파생학습 파일을 먼저 고정하고 2026 예측과 SHA를 따로 저장해 커밋한다. 그 뒤에만 2026 라벨을 분리·점수화한다. 동일 경기 시장/팀/선수군의 교집합 수, logloss/Brier/적중률, 5,000회 주 단위 bootstrap, 날짜 마감 낙폭, 관측 점수 잔차 진단을 기록한다. 5만원 제한 지갑 재생, 현행 운영/픽스터/9:1 비교는 자료·구현이 없어 별도 미구현으로 둔다. 훈련 이후 튜닝/규제·특징/표본/선택 규칙을 바꾸지 않는다.
