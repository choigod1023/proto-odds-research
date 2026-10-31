# 공식 확인 사실의 연구용 입력 경계

공식 확인 선발 변경·결장·라인업 변경과 이전 24/48시간 불펜 투구 수를 연구용 입력으로 정규화합니다. 학습된 선수 효과 모델은 없으므로 후보 확률을 계산하거나 보너스를 더하지 않습니다. `build_research_envelope`는 원래 기준 확률과 전달받은 기존 input-content/freeze 해시를 보존하고 별도의 연구 봉투 해시를 만듭니다. 기존 예측·정산을 수정하지 않습니다.

`extract_feature`와 `build_research_envelope`는 순수 함수입니다. 숨겨진 현재 시각, 네트워크, DB와 파일 쓰기는 사용하지 않습니다. 경기의 canonical/official ID·리그·시각·팀과 선수 ID 매핑, 동일 전체 경기 승패 상품과 완전한 배당 벡터, claim 및 공식 자료의 수신 시각 ≤ 판정 시각 ≤ T-30, 공식 확인 및 신원 확인 플래그를 검사합니다. 확정 선발과 probable을 구별합니다. 정의하지 않은 필드는 거부해 타깃 현재 점수·미래 결과가 입력되지 않게 합니다. 기존 값이 없으면 null과 `earlier_value_unavailable`을 보존합니다.

불펜은 호출자가 완전성을 확인했다고 표시한 이전 경기 일정과, 경기별 종료·수신 시각이 판정 전인 공식 박스스코어의 실제 투구 수만 합산합니다. 타깃 경기·진행 중 경기·누락 박스스코어·누락 선수 투구 수는 거부합니다. 이전 경기 근거가 없으면 0을 만들어내지 않습니다. 이 함수는 제공된 일정의 완전성이나 원문 바이트를 자체 검증하지 않습니다.

`to_adapter_input`은 총괄이 지정한 `fact-delta-inputs-v1` 형태를 반환합니다. 핵심 검사를 통과한 사실이 있으면 `ready`/`research_eligible=true`, 사실이 없으면 `baseline_only`, 인지된 부적합 입력은 `rejected`입니다. `ready`는 데이터 경계 통과를 뜻할 뿐 학습 완료나 성능 우위를 뜻하지 않습니다. 정규화 사실과 같은 상품 가격을 feature로, 원문 URL·해시·게시 주장·수신 시각 및 기존 동결 해시를 source evidence로 보존합니다. semantic SHA는 경기·상품·사실 내용·가격·확인 상태·스키마를 반영하고 단순 수신 시각·revision 변경은 제외합니다.

**신뢰 경계:** ID registry, 공식 확인 플래그, 실제 수신 시각과 SHA는 호출자가 전달한 주장입니다. 이 어댑터는 원문 바이트나 외부 증인을 조회하지 않으며 진위를 독립 인증하지 않습니다. 따라서 `external_receipt_verified`는 항상 false입니다. 원문을 직접 확보하고 식별·수신 사실을 검증하는 역할은 수집·연결 계층에 남습니다. 대상 스포츠의 공식 명칭을 임의 ID로 치환하지 않으며 미등록 리그는 거부합니다.

## 로컬 실행

예시는 전부 합성 값입니다. collector가 만든 같은 형식의 로컬 JSON은 `facts`, `registry`, `baseline`, `market`, `workloads`를 담아 전달합니다. `facts` 각 항목은 공통 bundle 필드를 그대로 사용하며 `before_value`·`after_value`의 타입은 예제와 `src/fact_delta_inputs.py`의 kind별 검사에 맞아야 합니다. 수집된 실제 bundle이나 선수 모델은 아직 이 작업에서 확보하지 않았습니다.

```powershell
python -X utf8 experiments/fact-delta-research-adapter-20261031/run_fixture.py experiments/fact-delta-research-adapter-20261031/example-bundle.json --input-id example-1 --adapter-inputs work/adapter-inputs.json
```

총괄의 `src.research_dispatch`와 통합한 작업 공간에서 `--manifest work/manifest.json`도 실행했습니다. 실제 어댑터와 대기열을 연결하는 합성 통합 검사 3개에서 준비된 입력의 발행·같은 자료 중복 억제·수신 시각만 바뀔 때의 억제·가격 변화 발행·예고 선발/늦은 수신 거부·기준 확률 보존을 확인했습니다. 어댑터·대기열·통합 선택 검사 합계 63개가 통과했습니다. 입력·출력 경로는 서로 달라야 합니다. 이는 실제 선수 정보의 인증이나 자동 수집 배포·ROI 검증이 아닙니다.

독립 stdlib 검사기는 합성 예시 봉투의 SHA와 기준 확률·기존 해시 보존을 확인합니다. 회귀 테스트는 확정/예상, 지연·naive 시각, 신원·상품/불완전 가격, 결과 필드 주입, 이전 박스스코어 누락, 입력 가용성 및 semantic fingerprint를 검사합니다. 자료 구조 검증은 적중률이나 ROI 향상 검증이 아닙니다.
