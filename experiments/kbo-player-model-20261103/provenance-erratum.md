# 공식 배당 원본 바이트 출처 정정

이 문서는 이미 고정한 phase2/phase3 예측·모델·정산 결과를 바꾸지 않는 재현성 정정이다. 최초 계산은 데이터 워커 커밋 `9bcbd53bf95ce6c6961b5b88fd43877152d602b6`을 사용하던 **Windows 작업 파일의 CRLF 바이트** 373행을 읽었다. 그 작업 파일의 SHA256은 `d9fc2f04fabec1fa41d29521aeafdb96e676747e59723396f36ba5c88a9b8117`이었다. 그러나 당시 Git blob은 줄바꿈이 LF로 변환돼 SHA256이 `cb556b0fb182eb13de6d22a513b8a04952e5e22d6b31a6b6ca44f543edb647aa`였다. 따라서 과거 9bc의 Git blob 자체가 d9fc 원본 바이트였다는 주장은 정확하지 않다. 당시 계산기의 Git 비교는 줄바꿈을 정규화한 뒤 내용이 같음을 확인한 것이었다.

데이터 워커의 정정 커밋 **`3167b4b19c2f5877af754d3a1a41ee2a8761a3f2`**은 가격 JSONL에 `-text`를 적용해 원본 CRLF 바이트를 Git blob에 그대로 보존한다. 이 커밋의 blob과 현재 원천 작업 파일은 동일한 373행이며 SHA256은 둘 다 `d9fc2f04fabec1fa41d29521aeafdb96e676747e59723396f36ba5c88a9b8117`이다. 과거 실행을 다른 해시 `cb556...`의 데이터로 소급 변경하지 않았다. 공식 manifest와 응답 증거 목록은 Git 텍스트 줄바꿈 차이 때문에 JSON 내용으로 비교하고, 가격 파일 자체는 줄바꿈 변환 없이 SHA256과 Git blob의 바이트 일치를 모두 확인한다. 공식 응답 압축 파일은 압축을 해제한 원문 SHA를 증거 목록과 대조한다.

정정된 Git blob을 임시 파일에 **그대로** 저장한 다음 `checked()`와 overlay를 실행했다. 동결 예측 SHA `d841cd9549a9dab4f3c9612e2fbdea84b9f914b390da4f9c80c0d9e2b3e5d317`, 결과 SHA `24938f0f5489d22bbc76a830e2f48e2491da6687d3f4a5a2f9e8e8c376790d23`은 변경되지 않았고 재실행 결과와 기존 결과의 모든 바이트가 일치했다. 시각 대조 439경기·확장 683경기와 무승부 환급 10·16건도 같다. 보존된 [검증 파일](phase3/source-commit-verification.json)이 기존 9bc blob과 수정 3167 blob, 임시 파일 및 결과의 SHA를 구분한다.

```text
python -X utf8 experiments/kbo-player-model-20261103/verify_source_commit.py
python -X utf8 experiments/kbo-player-model-20261103/verify_phase3.py
python -m pytest -q tests/test_kbo_price_overlay_20261103.py tests/test_kbo_player_model_20261103.py tests/test_devig.py tests/test_market_residual_experiment.py
```

첫 명령은 Git 객체 원문을 임시 파일에 복사해 overlay를 재생하고 삭제하며, 원래 결과·예측 파일을 덮어쓰지 않는다. 원천 Git 저장소와 데이터 경로는 [handoff.json](handoff.json)에 있다. 공식 자료의 현재 수신시각은 과거 가격 관측·체결 증거가 아니며, 가격·정산 결과의 독립 감사는 별도로 필요하다. 운영·원장·월 손실 한도와 모델을 바꾸지 않았다.
