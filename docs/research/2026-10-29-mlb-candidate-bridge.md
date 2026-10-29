# MLB 동결 후보 추론 연결 — 2026-10-29

동결 주 후보 `linear:l2=10`의 경기 전 추론을 `src/mlb_frozen_candidate.py`로
분리했다. 기존 모델·후보 순위·계수·스케일·선택 규칙을 변경하거나 재학습하지 않았다.
운영 추천, 수집기, 원장 기록, KBO, 정책, 배포는 이 변경 범위에 포함하지 않는다.
부모가 구현한 `mlb_candidate_feed.py`의 실제 축약 입력 형태를 직접 받는다.

## 공개 API와 파일 경계

```python
from mlb_frozen_candidate import load_mlb_artifacts, infer_mlb_candidate

# 호출자가 MLB 여부/입력 적격 여부를 확인한 후 배치에서 한 번 로드한다.
model, prior_tables = load_mlb_artifacts(2026)
result = infer_mlb_candidate(
    player_game["research_candidate_input"],
    decision_at=as_of,
    matchup={"league": "MLB", "gamePk": game_pk, "home": home, "away": away,
             "start_at": kickoff},
    odds={"home": home_odds, "away": away_odds,
          "observed_at": market_observed_at, "source": market_source,
          "snapshot_id": snapshot_id},
    model=model, prior_tables=prior_tables,
)
```

정확한 시그니처는
`infer_mlb_candidate(snapshot, *, decision_at, matchup, odds, model, prior_tables)`다.
이 함수는 파일·네트워크·DB·환경변수·현재 시계를 읽지 않고 입력도 변경하지 않는다.
`feature_row(snapshot, tables)`와 `predict_features(features, stage_model)`도 순수 계산이다.
후자는 23개 동결 선형 특성만 받으며 추가 비선형 특성을 만들지 않는다.
후보 추론에 결과·정산·현재 경기 점수는 사용하지 않는다.

선택적인 `infer_mlb_candidate_local(snapshot, *, decision_at, matchup, odds)`는
기본 입력 검사 후에만 해당 시즌 artifact를 로드한다. 잘못된 리그/시점/타순 및
지원하지 않는 연도는 artifact를 읽기 전에 거부한다. 로더는 정해진 이름 두 개만
읽고 개별 파일 상한 2 MB, manifest 16 KiB, 캐시 2시즌을 적용한다.
실제로는 모델 약 5 KB, 한 시즌 prior 약 275–282 KB다. 반환값 복사로 캐시 변조를
막는다. 모듈 import 때 데이터를 읽거나 NumPy·SciPy·연구 파이프라인을 import하지 않는다.

## 부모 수집기가 저장하는 정확한 현재 입력

```python
{
  "version": "mlb-candidate-feed-v1", "status": "captured",
  "source_kind": "official_live_completed_http", "gamePk": 822696,
  "start_at": "2026-08-13T20:05:00+00:00",
  "feed_start_at": "2026-08-13T20:05:00+00:00",
  "snapshot_at": "2026-08-13T19:00:00+00:00",
  "observed_at": "2026-08-13T19:05:00+00:00",  # HTTP 본문 수신 완료
  "cutoff_at": "2026-08-13T19:05:00+00:00",
  "source_url": "https://statsapi.mlb.com/api/v1.1/game/822696/feed/live",
  "response_sha256": "<응답 원문 64자리 소문자 SHA256>",
  "source_status": "Warmup",
  "starter_identity_kind": "official_published_probable_pitcher",
  "starter_final_confirmed": False,
  "lineup_identity_kind": "official_boxscore_batting_order",
  "sides": {
    "home": {
      "starter": {"id": 123, "pitching": {
        "battersFaced": 0, "strikeOuts": 0, "baseOnBalls": 0,
        "outs": 0, "earnedRuns": 0}},
      "lineup": [  # 정확한 battingOrder 순서의 서로 다른 9명
        {"id": 456, "batting": {
          "plateAppearances": 0, "strikeOuts": 0, "baseOnBalls": 0,
          "atBats": 0, "hits": 0, "hitByPitch": 0,
          "sacFlies": 0, "totalBases": 0}},
        # 나머지 8명
      ]
    },
    "away": {"starter": "<같은 구조>", "lineup": "<같은 구조의 9명>"}
  }
}
```

위 시각과 ID는 형식 설명용이다. 실제 전향 수집 기록이라는 주장이 아니다.
선발은 `gameData.probablePitchers`의 **공식 발표 probable pitcher**이며 최종 확정된
실제 선발이라고 부르지 않는다. 이 신원과 `starter_final_confirmed=False`를 출력과
입력 해시에 보존한다. 타순은 공식 boxscore battingOrder다. `name`이나 추가
`batting/pitching` 필드는 있어도 무방하며 사용하지 않는 필드는 해시에 넣지 않는다.

각 통계는 해당 선수가 그 feed의 `seasonStats`에 가진 위 원시 카운트다.
현재 경기 `stats`, ERA/타율 문자열, 예상 라인업, 선수 이름만 있는 schedule 자료로
대체하지 않는다. 명시적 0은 유효하다. 필드 누락·음수·비유한 값은 거부한다.
처음 출전한 선수의 prior가 없는 경우에는 원래 모델의 EB population prior와
missing flag를 그대로 사용한다. 선수가 없거나 타순이 부족한 경우를 채우는 것이 아니다.

부모 수집기의 계약은 `codedGameState ∈ {S,P}`, pitch event 없음, 정확한 9인
battingOrder, probablePitchers 신원, 실제 수신 완료 시각 검사다. 이 모듈은
그 계약의 `version/status`를 확인하고 타순·필수 통계·시각·hash 형식을 다시 검사한다.
원문이 없는 축약 payload에서 원문 SHA256의 진위나 HTTP 수신을 독립 인증하지는 않는다.
이 gate를 모든 종류의 play event 부재 또는 실제 최종 선발 확정으로 과장하지 않는다.

`snapshot_at <= observed_at <= decision_at <= start_at−30분`과
`feed_start_at == start_at`을 요구한다. 부모 수집기는 수신 시각이 T30보다 엄격히
이전이어야 하는 더 강한 조건을 이미 적용한다. 배당 관측 시각도 decision_at 이하여야
한다. matchup의 gamePk는 반드시 일치하고 home/away는 반드시 존재해야 한다.
수집 payload가 팀명을 생략하면 검증된 gamePk에 부모가 연결한 정규화 팀명을 받는다.
payload에 팀명이 있거나 matchup에 start_at이 있으면 일치를 추가 검사한다.
timezone 없는 시각은 추정하지 않는다.

별도 직접 입력 형태인 `official_live_pregame`도 받는다. 이 형태는 `url`,
`collected_at`, `status`, home/away 및 `no_actual_plays=True`,
`lineups_announced=True`의 명시적 증거를 요구한다.

## 출력과 실패 처리

`status=available`이면 `probability_home/away`, `selection=home|away`,
`selected_odds`, `model_stage`, `training_cutoff_at`, `features`, `feature_names`,
`model_artifact_hash`, `prior_artifact_hash`, `input_hash`, `feature_hash`, `evidence`,
`time_evidence`, `odds_time_verified`, `feed_start_verified`를 반환한다.
선택은 홈 확률 0.5 이상이면 홈이며 동률도 홈이다. 배당은 확률 계산에 들어가지 않는다.

항상 `research_only=True`, `result_used=False`, `validated_edge=False`,
`operating_model_reproduced=False`, `affects_operating_selection=False`,
`affects_operating_probability=False`다. `input_hash`는 실제 사용한 시각·출처·선수
통계·타순·배당·경기 신원을 묶으며, 부모의 공통 batch/input hash를 대신하지 않는다.
결과가 추가돼도 추론과 해시는 바뀌지 않는다. 이는 점수/결과가 포함된 불투명한
입력 전체를 인증한다는 의미가 아니다.

결측이면 `status=unavailable`, `reasons=[구체적 최초 원인]`, 선택·확률·배당은
`None`이다. 예: `home:nine_announced_starters_required`,
`away:<id>:pitching:outs:missing_or_invalid`,
`collected_at:after_decision_or_before_snapshot`, `decision_at:after_live_T30`,
`priors:wrong_season`, `model:not_frozen_artifact`.
이전 예측, 시장확률, 예상 라인업으로 복귀하지 않는다. 수집기가 반환한 unavailable
metadata도 후보가 available인 것으로 바꾸지 않는다.

## 동결 artifact와 계산

읽기 전용 원천은 형제 작업 트리 `baseball-information-stepchange-20261029`의
`scripts/mlb_lineup_nonlinear.py` 및
`experiments/baseball-information-stepchange-20261029/mlb/`다.
`build_artifacts.py --source <원천 mlb 폴더>`는 동결 JSON에서 주 후보 계수만 복사한다.
연구 모듈 전체를 import하지 않고 기존 `number/load_tables` 함수 두 개만 AST로
분리해 prior·정확한 population means를 계산한다. 훈련·튜닝·재선택을 실행하지 않는다.
2024/2025년 모든 선수의 필요한 prior 열만 남겼으므로 현재 2026년 새 matchup에도
사용할 수 있다. 2023 prior나 훈련용 원시 자료는 런타임에 필요하지 않다.

- `before2025`: 2024년 296경기, 마지막 훈련 경기 시작
  `2024-09-29T19:10:00+00:00`. 2025년 추론에 사용한다.
- `before2026`: 위 자료 + 2025년 validation 300경기 = 596경기, 마지막 훈련 경기 시작
  `2025-09-30T22:08:00+00:00`. 2026년 추론에 사용한다.
- 2024년 또는 2027년 이후 추론은 지원하지 않는다. 다른 prior를 묵시적으로 읽지 않는다.

배팅 순서 가중치, 이전 시즌 EB strength 150/300, 당해 150/100, 투수 ERA의 실제
outs 분모·30이닝 shrinkage, missing flags, 23개 홈−원정 차이를 그대로 계산한다.
표준화 후 각 특성을 ±6, logit을 ±40으로 clip하고 sigmoid를 적용한다.
기존 NumPy의 가중치 정규화 분모 `9.000000000000002`도 보존했다.
곱셈·합산의 순서 차이만 binary64 허용 오차로 검사한다.

canonical SHA256은 저장 원장의 관례와 같은 UTF-8 JSON
`ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False`다.
source 파일 해시는 원래 source 검증처럼 비 gzip 파일의 CRLF를 LF로 정규화한다.
원래 frozen JSON과 source manifest의 hash를 lineage에 남긴다. payload 자체의 hash,
manifest와 코드의 고정 hash를 모두 확인하므로 변조 후 hash만 다시 계산해 새 계수를
끼워 넣을 수 없다.

- 모델: `1ec251d3c7265c606cc0f4dcb11a8c37568ba0f80a0b7594e41ca405f0f9f917`
- 2024 prior: `c9229570b4b27ee4c0616a95612c4e7b4af59b4b4b2a638b36b988be36c302bf`
- 2025 prior: `fd2f4b727c1aa68075ecf2cc134c07e60391f7e941a0812b7f61aca2a9ef7d21`

## 보관 자료 전체 재현과 기간별 결과

아래 명령은 외부 경로나 네트워크 없이 동작한다.

```powershell
python -X utf8 -m pytest tests/test_mlb_frozen_candidate.py -q -p no:cacheprovider
python -X utf8 data/model_candidates/mlb/replay.py
```

`parity-fixtures.jsonl.gz`는 원래 채택된 310경기의 축약 원시 입력, 별도 expected 특성,
별도 labels를 보존한다. replay는 모든 선택을 먼저 확정한 후에만 labels로 정산한다.
`verification.json`에 월별·구간별 적중, 손익, ROI, 최대 낙폭, logloss, Brier를 기록한다.
기존 `results.json`의 모든 동일 구간 지표와도 1e−12 이내로 일치했다.

- 원래 116경기 + 외부 194경기, 총 310경기 모두 재현했다.
- 고정 원래 특성을 넣은 확률 오차 최대 `1.1102230246251565e-16`.
- 축약 원시 입력으로 다시 계산한 특성 오차 최대 `1.6653345369377348e-16`,
  최종 확률 오차 최대 `1.1102230246251565e-16`.
- 선택 310/310, 저장된 원화 정산 310/310 완전 일치. 동결 stage도 모두 일치했다.
- 외부 요청 230 중 제외 36경기는 모두 snapshot/cutoff/start 시각 검사 실패다.
  제외 경기에 후보 대신 baseline을 넣어 채택 표본에 합치지 않았다.
- 부모의 실제 `mlb_candidate_feed.extract`에도 보관된 raw feed 중 T30 조건을
  만족하는 3개를 오프라인으로 넣었다. 반환 payload를 그대로 추론해 확률 최대 오차
  0이었다. 과거 cutoff를 수신 시각으로 가정한 호환성 검사이며 실제 전향 수신 증거가 아니다.

각 경기 1만 원 단폴, 원래 저장 가격과 Decimal ROUND_HALF_UP 원화 정산 기준이다.

- 원래 116경기: 후보 52적중, −283,000원, ROI −24.40%; **연구 시장90:투표10**
  baseline 63적중, −154,800원, −13.34%. 시장만은 −14.43%였다.
- 같은 표본 2026-07-01 이전 84경기: 후보 −18.15%, 연구9:1 −4.68%.
  이후 32경기: 후보 −40.78%, 연구9:1 −36.09%.
- 외부 채택 194경기: 후보 109적중, −136,000원, −7.01%; **시장만 있는 baseline**
  109적중, −254,700원, −13.13%. 이 표본에는 역사적 픽스터 투표가 없다.
- 같은 표본 2026-08-10 이전 77경기: 후보 +1.52%, 시장 −14.58%.
  이후 117경기: 후보 −12.62%, 시장 −12.17%.

외부 표본의 일부 기간 개선을 운영 수익성 또는 일관된 우세로 해석하지 않는다.
전체 후보 ROI는 두 표본 모두 음수이며 기간에 따라 상대 우열도 달라진다.

## 시점 증거의 한계와 부모에게 남는 작업

역사적 snapshot은 `official_historical_timecode_replay`와 원래 `accepted=True`를
명시해야 한다. `snapshot_at <= cutoff_at <= decision_at < start_at`은 확인하지만,
후일 수집한 `collected_at`을 과거의 수신 증거로 소급하지 않는다. old116은 T10 자료이며
feed_start_at이 저장되지 않은 116건은 `feed_start_verified=False`로 남긴다.
배당의 실제 관측/수신 시점은 310건 모두 입증하지 못했으므로 `observed_at=None`,
`time_scope=archived_unverified_time`, `odds_time_verified=False`로 둔다. 기존
`quote_decision_at`도 배당의 실제 HTTP 수신 시각으로 바꾸지 않는다.
이 예외는 순수 과거 수치 재현에만 쓰며 live 입력에는 허용하지 않는다.

이전 시즌 MLB/Savant 자료는 완료된 시즌만 사용하지만 후일 다운로드한 자료다.
당시 저장된 선수별 통계 원본이나 수정 이력까지 입증하지 않는다. live 수집의 T30
타순·카운트·경기 ID/시각 증거가 없으면 available로 바꾸지 않는다.

부모에게 남는 일은 실제 player collector 실행에서 새 HTTP 완료 payload를 얻어
공통 입력/batch identity와 함께 후보 출력을 append-only 연구 원장에 기록하고,
같은 운영 revision·가격·공식 정산과 연결하는 것이다. 이 작업은 그 연결을 대신하지 않는다.
부모가 확인한 현재 선택 표본 4개는 야구가 아니며, 이 연구의 9:1·시장 baseline을
실제 운영 학습 모델로 간주할 수 없다. MLB 후보와 실제 운영 모델의 동일 경기 성과가
검증됐다고 주장하지 않는다. PR 병합, 운영 채택, 추천 전환, 배포 완료도 이 문서의 결론이 아니다.
