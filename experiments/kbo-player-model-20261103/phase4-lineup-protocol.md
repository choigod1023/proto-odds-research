# 공식 타순 가정·투수 추정 유지: 예측 전 고정 계획

총괄 계약 `d2685536`, SHA256 `c190df6c0f8f8f515dc2214dd59c963719b0bf81c9fa09c74ed5fbc113e351f5`을 따른다. 예측·정산 전에 이 파일을 커밋한다. 입력 `official-lineup-overlay-2026.jsonl.gz`의 데이터 PR #511 commit `a28cf0d19003bc436d174bb85af46be86ee1ee24` Git blob과 작업 파일의 원바이트 SHA `ddc66e073ada98ef9cbf469fac64aed53e45ac3d4e44a1e4330f2c661eced627`을 대조한다. 공식 수집 PR #514의 `fdcad130` 원천 SHA `92015aa1af2e23813b0b4d8c3c5bd182d1c0ee7b2226920af3445e86569ae67c` 및 증거를 별도 보존한다.

## 예측

`retrospective_official_batting_order_assumed_available`은 2026년 수집된 193/726경기, 양팀 9명씩의 현재 공식 LINEUP_CK=true 타순을 과거에 이용할 수 있었다고 가정하는 회고 반사실이다. 실제 과거 공표·수신·정정 시각은 입증되지 않았으며 `eligible_as_pregame_feature=false`와 `point_in_time_verified=false`를 반드시 유지한다. 선수 ID가 사후 동일경기 순서와 공식 이름 대조를 이용했다는 한계도 유지한다. 투수는 기존 이전 경기 기반 추정 그대로이며 공식 선발로 부르지 않는다. 미처리 533경기는 공식 타순 부재로 단정하지 않는다.

기존 동결 `predictions.json` SHA `d841cd9549a9dab4f3c9612e2fbdea84b9f914b390da4f9c80c0d9e2b3e5d317`의 `player` 모델 alpha·beta·mean·scale·teams를 그대로 복원해 `model.predict`만 호출한다. 어떤 경우에도 `fit` 또는 `minimize`를 호출하지 않는다. 원 feature 파일 SHA `346edd84d4388f99cc9ad23f7ad530560445b4bc06b6f841b745dabffa1a8b42`, 2026 원래 예측/결과·가중치·실행 코드는 바꾸지 않는다. `home_lineup_strength`, `away_lineup_strength`만 타순 overlay의 과거 이력 객체에 기존 `adapter.scalar` 공식을 적용해 교체하며, 나머지 모든 행의 입력은 canonical 값과 실제 모델 행렬에서 동일함을 검사한다. 이전 projected 입력에서 타순 외 전체 JSON 및 projection 메타데이터는 overlay와 서로 같아야 한다.

기존 `adapter.check_nested`로 모든 타자 이력의 fixture ID와 날짜가 타깃과 다르고 타깃보다 엄격히 이전인지 확인한다. 양 팀 1~9번 유일 선수 ID와 history profile ID 순서가 공식 context와 일치해야 한다. 입력에 타깃 결과·동일날·미래 기록 또는 임의 추가 수치필드가 있으면 중단한다. 누락 선수 이력은 0으로 채우지 않는다. 기존 scalar는 유효한 이력의 `(안타+볼넷)/(타수+볼넷)`을 사용하며 OPS/wOBA가 아니다. 누락 이력 10명의 영향과 양팀 타순의 null 여부를 센다.

기존 모델을 같은 193경기에 재적용한 확률이 동결된 원예측과 1e-10 이내로 맞아야 하고, 새로운 예측에서 타순 이외의 행렬과 가중치가 같아야 한다. 새 예측 파일은 기존 파일과 별도 x 모드로 저장하고 SHA와 Git commit으로 고정한다. 이때 2026 타깃 라벨·phase3 점수화 원장은 열지 않는다. 그 후에만 라벨을 연결한다.

## 정산과 해석

공식 타순 193경기 전체의 원래 선수 모델·변경 후 선수 모델·원래 팀 모델의 3-way logloss/Brier를 동일 라벨에서 비교하고 픽 변경 및 확률 평균 절대 차이를 보고한다. 시장은 가격이 없는 경기에는 확률이 없으므로 공통 가격 교집합에만 존재한다. 주 결론은 `player` 모드로 고정하며 선발·타순 등 다른 모델을 결과에 맞춰 고르지 않는다.

기존 phase3의 원 가격과 공식 복원 `3167b4b19c2f5877af754d3a1a41ee2a8761a3f2`, SHA `d9fc2f04fabec1fa41d29521aeafdb96e676747e59723396f36ba5c88a9b8117`의 동결 원장과 193경기 교집합을 취한다. `strict_original_time_match`와 `extended_day_team_source_confirmed`를 분리하고 원자료 상품·가격·환급 원장을 그대로 쓴다. 2-way 후보는 기존처럼 조건부 승률 argmax, 동률 홈이며, 무승부는 환급·ROI 분모 포함·적중률/2-way logloss/Brier 분모 제외다. 공통 표본에서 세 모델·시장의 동일 가격 1단위 수익·월별 손익·일 마감 최대낙폭·선택/확률 변화, 주단위 쌍체 bootstrap 5000회 seed 20261103을 산출한다. 가정 가격 시나리오이며 실제 당시 수신·실거래는 입증되지 않았다. 공식 가격 연결에 결과 점수가 사용된 선택 편향도 남는다.

독립 Decimal 정산·원예측 해시 불변·타순 외 피처 불변·동일날/미래/타깃 오염 거부·fit/minimize 호출 금지·환급·strict PIT 상승 금지를 테스트한다. source/data gzip Git blob과 checkout bytes를 정규화 없이 대조한다. 월 5만원 손실 한도 지갑 재생과 현행 운영·픽스터·9:1 비교는 하지 않는다. 이 실험 결과로 모델을 자동 채택하거나 추가 튜닝하지 않는다.
