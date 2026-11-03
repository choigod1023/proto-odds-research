# KBO 선수 기반 득점 모델 인계

이 폴더는 연구용이다. 데이터 워커 출력이 아직 확보되지 않아 실제 학습·선정·정산을 하지 않았다. `protocol.md`와 추가 등록은 각각 `b12cf82a`, `61f3ec7d`, `9babf4e1`에서 고정했다. 2026년 데이터는 이미 검토된 진단 기간이며 전향 블라인드가 아니다. 실제 픽스터·현행 동결픽·9:1 원천이 연결되지 않아 비교 기능이나 성과를 가장하지 않는다.

## 입력 호환과 차단 조건

각 JSONL의 한 줄은 총괄 contract의 정확한 최상위 11개 필드다. `features`는 protocol에 열거한 11개 이름만 허용하며 `{value,available_at,availability_basis,source_id}`가 기본이고 과거 선수/팀·휴식 특징에는 `history_max_completed_at`과 `history_fixture_ids`가 추가로 있어야 한다. 과거 경기 완료시각이 없거나 같은 경기 ID가 이력에 들어 있으면 차단한다. `point_in_time_verified`는 각 특징과 그 source의 실제 수신이 판정 이전이어야 하며, `retrospective_unproven`은 누락 수신시각을 추정하지 않는다. 두 층은 별도 실행한다. 결측 특징은 생략하거나 null 값으로 전달한다. 당일 이전 경기 정보에는 실제 완료시각이 필요하다.

`sources`의 각 항목에는 `source_id,path_or_url,sha256,received_at,availability_basis`가 필요하다. source SHA는 해당 자료가 진짜 공식 자료이거나 가용시각이 사실임을 자동 증명하지 않으며 외부 감사가 필요하다. 구체적인 roster/선발 및 선수 통계가 사후 박스스코어인지 별도 감사한다. 입력 필드가 다른 경우 임의 변환하지 않고 매핑을 결과 전에 등록한다. 외부 파일을 수정하지 않는다.

`market`은 null 또는 contract의 `market_id,kind,selections,odds_observed_at,settlement_rule`이다. 비교 가능 가격은 해당 가격의 실제 증거 source를 가리키는 `source_id`도 있어야 하며, source `received_at`과 가격 `odds_observed_at`은 decision 이하여야 한다. KBO 2-way는 `kind=full_game_2way`, 키 `home,away`, `settlement_rule=draw_refund`; 3-way는 `full_game_3way`, 키 `home,draw,away`, `draw_selection`이다. 이 증거가 없으면 가격·ROI 비교를 제외하되 최종 점수가 있다면 전체 점수/3-way 로그손실 진단에는 포함한다. 동일 상품의 증거가 완전해도 사전 가격의 실제 체결 가능성은 자동으로 보장되지 않는다.

라벨은 `{fixture_id,status,home_score,away_score,completed_at,received_at,revision}` 및 시장 정산 시 `market_id,settlement_rule`이 있는 별도 JSONL이다. `status`는 `final,void,pending`. 회고 층에서 실제 수신시각이 없으면 `received_at=null`과 `availability_basis=retrospective_completion_assumption`을 명시하고 실제 완료시각 기준으로 7일 경계를 가정한다. 검증 층에서는 실제 수신시각을 요구한다. 이 가정은 당시 수신을 증명하지 않는다. 테스트 라벨을 검증·학습 입력에 넣으면 중단한다. 검증 전 등록대로 2024년까지 최소 100경기, 2025년 최소 30경기, 2026년 최소 30경기와 7일 경계가 없으면 중단한다. 결측/중복은 조용히 지우지 않는다.

## 두 단계 실행

아직 실행하지 않았다. 총괄이 자료의 해시·레이어·기회를 감사하고 각 파일을 고정한 후에만 사용한다. 먼저 2024~2025 라벨만 별도 파일로 고정한다. 아래 예시는 경로 자리표시자다.

```text
python -X utf8 experiments/kbo-player-model-20261103/run.py predict --rows ROWS.jsonl --development-labels DEVELOPMENT_LABELS.jsonl --output PREDICTIONS.json
```

예측은 `x` 모드로 쓰므로 덮어쓰지 않는다. 예측 파일을 커밋하거나 독립 저장소에 SHA256과 함께 고정해 검토한 뒤에만 테스트 정산을 수행한다. 실제 SHA256을 지정하며 코드와 feature 해시 변경도 차단한다.

```text
python -X utf8 experiments/kbo-player-model-20261103/run.py score --rows ROWS.jsonl --predictions PREDICTIONS.json --predictions-sha256 FIXED_SHA256 --test-labels TEST_LABELS.jsonl --output SCORES.json
```

시장/후보/팀 ablation은 같은 적격 경기와 가격으로 계산하고 상품별 결과를 별도로 표시한다. 두 팀 조건부 독립 포아송 가정, tail bound, 결측 및 미관측 팀을 보존한다. 무승부 환불은 ROI 분모에 베팅 원금을 포함한다. 월·최대낙폭은 결과 수신 순서가 아닌 경기일 마감의 단위 베팅 진단이다. 주 단위 구간은 작은 표본·시간 의존을 없애주지 않는다.

**현재 제한:** 결과 점수 API의 `final`은 외부 공식 원천인지 자동 인증할 수 없다. 가상 월 손실 5만원 중단 재생과 실제 지갑/운영픽 연결은 구현하지 않았다. 미정산 원금이나 동시 노출을 이 엔진의 일별 마감 단폴 손익으로 대체해서는 안 된다. 실제 가격·선발 명단·이전 완료 증거가 없으면 해당 관측을 검증된 운영 실적으로 분류하지 않는다.

합성 회귀 검증: `python -m pytest -q tests/test_kbo_player_model_20261103.py tests/test_devig.py tests/test_market_residual_experiment.py`. 실행기 도움말: `python -X utf8 experiments/kbo-player-model-20261103/run.py --help`.
