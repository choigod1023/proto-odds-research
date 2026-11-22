# KBO 공식 가격 바이트 보존 수정: 독립 종료 감사

범위: 가격 PR #513 source commit `3167b4b19c2f5877af754d3a1a41ee2a8761a3f2`. 이전 source `9bcbd53`, phase3 모델 `5c797509`의 고정 결과 및 이 감사 PR의 이전 문서는 보존한다. 모델 워커의 진행 중인 후속 핀·정정 파일은 이번 단계에서 검토하지 않았다.

## Git 바이트 재현성

`git show 3167b4b1:experiments/kbo-official-odds-20261103/restored-prices.jsonl`의 원래 blob SHA-256은 `d9fc2f04fabec1fa41d29521aeafdb96e676747e59723396f36ba5c88a9b8117`이며 manifest와 일치한다. 373개 행·CRLF 373개다. `9bcbd53`의 blob은 LF SHA `cb556b0fb182eb13de6d22a513b8a04952e5e22d6b31a6b6ca44f543edb647aa`였고 원래 감사의 결함은 **그 과거 커밋에만 그대로 적용**된다. 새 커밋은 `.gitattributes`의 `/restored-prices.jsonl -text`로 해당 파일의 변환을 막고, 생성 시 CRLF를 명시한다. source의 `BYTE-PRESERVATION-CORRECTION.md`도 과거와 새 커밋을 분리해 설명한다.

독립 임시 Git 저장소 두 곳에 해당 `.gitattributes`와 새 blob 바이트를 넣고 각각 `core.autocrlf=true`와 `false`로 인덱스 및 `git checkout-index` 바이트를 검사했다. 양쪽 모두 SHA `d9fc…`가 보존됐다. 새 Git blob을 임시 파일에 쓰고 기존 `5c797509`의 `phase3_overlay.checked()`에 넘기자 원래 `SOURCE_HASH`로 통과했다. 이 검사는 기존 검증 함수에 대한 source 수준 검사이며 모델 워커의 새 통합 commit까지 검증했다는 뜻은 아니다.

## 원문과 수치 보존

이전 LF blob과 새 CRLF blob을 LF로 통일하면 정확히 같고, JSON 파싱된 373개 행도 순서·값 모두 동일하다. 이전 커밋과 새 커밋의 `evidence/` Git 객체 105개(원문 gzip 104개와 요청 기록)는 일치한다. 새 커밋을 임시 폴더에 꺼내 원천 `test_restore.py`를 실행해 공식 응답의 373행 비교를 포함한 **6개 테스트가 통과**했다. 네트워크 요청은 하지 않았다.

앞서 독립 작성한 overlay 감사 코드를 다시 실행해 7,854건의 Decimal 정산과 시장/팀 대비 주 단위 구간을 재확인했다. 엄격 시각 대조 439경기는 선수 245·시장 250적중, 확장 683경기는 선수 380·시장 398적중이다. 새 source의 373개 가격 행이 이전 계산에 쓰인 각 행과 같으므로 이 source 수정으로 기존 동결 결과 숫자가 바뀌지 않는다. 이는 모델 워커의 추후 새 결과물에 대한 감사는 아니다.

## 한계

가격 필드는 2026-11-03 사후 공식 응답에서 복원했으며 경기 전 T-30 관측·매수 가능성이나 최초 가격을 증명하지 않는다. 249건은 원본 경기 시작 시각을 대조하지 못했다. 2026년 확장 코호트에는 그중 244건이 포함된다. 추정 타순은 공식 확정 선수 명단이 아니다. 새 공식 명단 수집·모델 입력·성능은 이번 감사에서 다루지 않는다. 본 감사는 운영 채택·병합·배포나 지갑 거래를 허용하지 않는다.
