# KBO 2-way 하프라인 핸디캡 회고 검증

## 결과와 범위

기존 M0 다중시장 검증의 고정된 44경기를 동일 회차·날짜·홈/원정 ID·점수·일반 승패 배당으로 연결했다. 완료·비무효 상태와 하프라인, 2방향 배당, 조건 반영 점수와 결과 코드를 확인했다. 회차 내 상품은 각각 한 개씩 연결된다. 동률 선택은 제외하는 대신 이 고정 표본에 존재하면 실행을 중단한다.

| 동일 44경기, 가상 단위 투입 | 적중 | 환불 | 반환 | 순손익 | ROI |
| --- | ---: | ---: | ---: | ---: | ---: |
| 동결 M0 확률이 높은 쪽 | 29/44 | 0 | 38.17 | -5.83 | -13.25% |
| 동일 상품 최저 배당 | 29/44 | 0 | 38.17 | -5.83 | -13.25% |

44건에서 두 선택이 같았다. M0는 홈·원정 득점 평균을 독립 Poisson으로 해석하고, 홈 점수에 해당 홈 라인을 더한 결과의 승리 확률을 계산해 더 높은 쪽을 고른다. 시장 비교는 원본 상품의 최저 배당을 고른다. 재학습, ROI에 따른 조정, EV 문턱값은 없다. 배당은 소급 보존값이며 결과는 **실제 구매·체결 수익이 아니다**. 경기 전 예측·배당 수신과 구매 가능 시점은 확인되지 않았다. 표본은 선별됐으며 이미 살펴본 2026년 구간이므로 독립 블라인드 평가가 아니다. M0는 비교용이며 음의 ROI에 근거한 운영 채택이 아니다.

## 원래 무승부의 실제 행

2026-03-31 KST, 회차 `260040`, 홈 `SS`·원정 `OB`, 고정 fixture `20260331OBSS02026`:

- 일반 승패 `matchSeq=192`, `handi=0`, `mchScore="5:5"`, `gameResult="4"`, `protoStatus="4"`, `gameReject="0"`.
- HC `matchSeq=194`, `handi=2`, `winHandi=-2.5`, `loseHandi=2.5`, `drawAllot=0`, `mchScore="2.5:5"`, `gameResult="2"`, `protoStatus="4"`, `gameReject="0"`; 홈/원정 배당은 `2.81/1.26`.
- 홈 5점에 -2.5를 적용하면 2.5:5로 원정 적중이다. 코드 `2`는 조정 원정승에 대응하고, 일반 상품의 원래 무승부 코드 `4`와 다르다. 두 원시 행 전체는 [audit.json](audit.json)에 있다. 원본에 이미 손상된 한글 이름 바이트가 있어 임의 복원하지 않고 ID를 사용한다.

76개 회차 모두 압축 해제 본문의 SHA-256을 manifest와 비교했다. 완료·비무효 일반 승패 행과 같은 날짜·팀 ID로 연결된, 완료·비무효·무승부 배당 없는 HC 하프라인 행 45건에서 조정 점수가 모두 `원래 홈 + winHandi : 원정`과 일치했다. 음수 라인 28건 중 조정 홈승 `0` 10건·원정승 `2` 18건, 양수 라인 17건 중 홈승 `0` 12건·원정승 `2` 5건이다. 원래 무승부 HC 사례는 이 조건에서 1건이다. 이는 해당 아카이브의 조건에 맞는 행의 census이며, 모든 공식 상품 또는 시기의 census는 아니다. 코드 의미는 이 행들과 점수의 **경험적 대조**이며 공식 코드 사전은 확보하지 못했다.

[현행 세부규칙](https://www.sportstoto.co.kr/terms_allocation.php)은 조건을 반영한 홈 기준 승패를 별도 유형으로 규정하고 일반 승패에서 무승부 1.0배를 적용한다. [이전 세부규칙](https://www.sportstoto.co.kr/proto_rules.php)은 사전 조건이 주어졌을 때 조건 반영 결과를 적용한다고 명시한다. 해당 상품별 별도 공지와 전 시기의 규칙을 확인한 것은 아니다. 하프라인은 정수 점수에서 조정 무승부가 없으며, 공식 무효·미완료와 적용 기간 확인은 별도 문제다.

## 입력과 재실행

Python과 SciPy가 필요하다. 프로젝트 루트에서 다음 명령을 실행한다.

```text
python -m unittest tests.test_validate_m0_handicap -v
python scripts/validate_m0_handicap.py
```

스크립트는 [cohort.json](cohort.json)과 [audit.json](audit.json)을 생성한다. 각 경기의 모델 득점률, 확률, 선택, 배당, 결과 및 원본 본문 해시는 cohort에 있고, 완전한 원시 무승부 행과 모든 76개 원본 경로·압축/본문 해시는 audit에 있다. 원본 입력을 수정하지 않는다. `--manifest`, `--cohort`, `--forecast`, `--output`으로 위치를 지정할 수 있다. 입력 파일 SHA-256이 아래 값과 다르거나 누락된 원본이 있으면 중단한다.

- 기존 44경기: `C:/Users/user/Documents/Codex/market-outcomes-validation/experiments/m0-multimarket-20261105/cohort.json`; 파일 SHA-256 `a9c20e35be4446474de045a8b9fedda300f607ba00aacc7e8183e9c9a3a6ecbf`.
- 동결 예측: `C:/Users/user/Documents/Codex/market-outcomes-validation/experiments/m1-service-20261104/replay/replayed-709.json.gz`; 압축 파일 SHA-256 `a597ed066534cce92cfb12606d7f7e3edcc7591b3a6c1d17e7777c8b04e8b505`. 기존 보고서에 따르면 M0 학습 기간은 2023–25년이다.
- 원본 목록: `C:/Users/user/Documents/Codex/market-outcomes-validation/experiments/pregame-proto-prices-20261104/manifest-v2.json`; 파일 SHA-256 `f91800d7a7daf09762f66c6ae16cbafcd557bc9ca3b26f7b7940d2eacbfa4cc6`. 목록의 `source_roots`와 `body_file`을 조합한 원본 gzip 76개가 해당 경로에 실제로 필요하며, 저장소에 이 gzip들을 포함하지 않는다. 다른 환경에서는 동일 파일을 manifest에 명시된 경로에 배치해야 한다.
- 실제 무승부 원본: `C:/Users/user/Documents/Codex/kbo-official-odds-20261103/experiments/kbo-official-odds-20261103/evidence/detail-2026-40.body.gz`; 압축 해제 본문 SHA-256 `223aaed48d8ee1914fdad9d1a3d4f85801c02c096513283accd29f63aa81d0c4`, gzip SHA-256 `c3607a1476a8380112e5ec601d912939cac0097ba72e1f55a22b38e9166f36eb`.

실행은 76개 해시와 정확히 한 개의 매칭, 45행 및 실제 무승부 행의 값, 두 비교의 적중과 손익을 검증한다. 9개 단위 테스트는 양·음수 라인, 원래 무승부, 경계 점수, 잘못된 입력, 확률 대칭 및 선택 동률을 검증한다. 이 기록은 운영 코드 변경이나 배포가 아니다.
