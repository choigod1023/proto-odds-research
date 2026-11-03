# 공통 API (작업·보고는 한국어)

common/core.py는 고정 원천과 기존 M0만 제공한다. 코드·데이터 계약 변경 권한은 총괄에 있다. 각 모델 워커는 자기 family 폴더만 수정한다.

- import: common 디렉터리를 sys.path에 넣고 import core. core.load() -> dict train(원래raw_features 포함2182), test_actual709, test_projected709, labels(개발구간만).
- core.development(data) -> train1451, validation731. core.final_training(data) -> train2182. core.normalize(row) -> 기존 M0 입력 필드만 깊은복사.
- core.legacy에는 원래 make_spec/design/fit/predict/scalar_features/adapter가 있다. M0 변경 없는 대조군은 core.fit_baseline, predict_baseline.
- core.baseline_reference(): development_model/development_predictions(2025 실제 명단), final_model/final_predictions(2026 실제 명단). 원본 고정 모형/예측이며 운영 모델이 아니다.
- core.metrics, core.choose는 2025 승패 결정 적중률 최대→승패 log loss 최소→완전 동률 baseline 우선→config_id 순이다. 순위를2026에맞춰바꾸지말것.
- 각 family/candidate.py는 CONFIGS(dict), fit(rows, labels, config_id), predict(model, raw_rows)를 구현한다. 예측은 기존 M0 predict와 같은 fixture_id/event_date/probabilities_3way/probabilities_2way 필드를 갖고 모든709개를 유지한다.
- 변경 없는 baseline은 CONFIGS에 넣지 않고 실행코드에서 공통 대조군으로 넣는다. raw_rows/공통baseline/입력파일은 in-place 변경하지 않는다.
- 개발 각 후보 모델/731예측/선택과 설정을 commit한 뒤 선택된 후보만2182재학습해 실제/예상709예측을 commit한다. distribution은 draw_hurdle도 사전 선언 진단으로 최종예측한다.
- 2026 label·배당 정산은 총괄이 모든 최종 예측을 고정한 뒤 한다. 워커는 연구소스에서 결과 원장을 읽거나 2026 ROI로 설정을 다시 고르지 않는다. 과거 이미열람한 표본이므로 블라인드라 주장하지 않는다.
- 공통 core 커밋을 새 격리브랜치에 cherry-pick 한 뒤 별도의 family 코드/검증/개발동결/최종예측커밋을 만든다. 공통파일 변경은 총괄에 보고한다. 같은과거연구를새모델로이름만바꾸지말고새로변한부분을명시한다.
- no production/DB/queue/schedule/wallet change; no merge/deploy/adoption. 기존 월손실5만원 모의원장은 유지, 이 실험의ROI는 단폴1단위다.
