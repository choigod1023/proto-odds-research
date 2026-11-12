import json,pathlib,collections,hashlib
P=pathlib.Path(__file__).resolve().parent
m=json.loads((P/'manifest.json').read_text(encoding='utf8')); c=m['coverage'];j=json.loads((P/'price-join.json').read_text(encoding='utf8')); probes=json.loads((P/'probe-results.json').read_text(encoding='utf8')); rows=[json.loads(x) for x in (P/'fixtures.jsonl').read_text(encoding='utf8').splitlines()]; excludes=collections.Counter(reason for x in rows if x['pregame'] for reason in x['pregame']['audit']['reasons']);months=collections.Counter(x['gameDate'][5:7] for x in rows);q=j['coverage']; pc=collections.Counter(x['duplicate_schedule_rows']>1 for x in rows)
text=f'''# MLB 공식 원천 연결 — 2026-11-03

상태: {m['status']}. 2026 정규시즌의 4월 1일~9월 30일 UTC 공식 일정 응답 {m['schedule_rows']}행에서 고유 gamePk {m['census_unique_gamePk']}개를 확인했다. gamePk 중복은 {m['duplicate_gamePk']}개다. 이 범위는 3월 경기를 포함하는 시즌 전체가 아니다. 결과와 무관하게 각 월 gameDate·gamePk 순서로 앞 17개씩 총 {c['selected']}개를 골랐고 나머지 {m['unselected_gamePk']}개는 미처리다. 월별 선정 수: {dict(months)}. 월초에 몰린 비확률 표본이므로 시즌 전체의 대표 표본으로 일반화하지 않는다. 고정 규칙은 커밋 `2341b78`에서 2026년 결과 검토 전에 확정했다.

경기 전 요청 원문 {c.get('pregame_raw',0)}/{c['selected']}, 공식 사후 boxscore 원문 {c.get('postgame_boxscore_raw',0)}/{c['selected']}, 양쪽 모두 {c.get('both_raw',0)}/{c['selected']}다. provider timestamp, 팀·gamePk·시작 시각, 투구·타수와 이후 이벤트 검사를 모두 통과한 경기 전 이벤트 재생은 {c.get('event_replay_pass',0)}/{c['selected']}이며 실패 사유별 건수는 {dict(excludes)}다. 중복 원문 시점변형이 있는 선정 경기 {pc.get(True,0)}개는 보수적으로 재생 검증에서 제외했다. 여러 실패 사유가 한 경기에 겹칠 수 있다. 세부 분모와 사유는 `fixtures.jsonl`에 있다.

경기 전 응답에서 양 팀 probable pitcher가 있는 경기 {c.get('both_probables',0)}개, 양 팀 battingOrder와 100~900 코드가 각각 9명이고 일치한 경기 {c.get('both_lineups_nine_and_code_match',0)}개다. 공식 사후 응답에서 양 팀 실제 선발을 `gamesStarted=1`로 식별한 경기 {c.get('both_postgame_starter_identified',0)}개이고, 양 팀 probable과 사후 실제 선발이 모두 일치한 경기는 {c.get('both_probables_match_actual',0)}개다. 경기 전 코드 순서와 사후 100~900 코드 순서가 양 팀 모두 일치한 경기는 {c.get('pregame_lineups_match_postgame_initial',0)}개다. 이 일치는 **사후 비교**이며 당시 공개나 최종 선발의 사전 확정을 증명하지 않는다. 102경기 전체가 사후 선발·타순과 일치하는 패턴은 필드별 과거 시점 재생 여부를 추가 확인해야 할 이유이며, 미래 정보 혼입이 없다는 증거가 아니다. probable은 계속 예정 투수다.

사후 boxscore의 최종 `battingOrder`와 코드 100~900 순서가 적어도 한 팀에서 다른 경기는 {c.get('postgame_final_battingOrder_differs_initial',0)}개다. 예를 들어 gamePk 822728 홈은 코드 `200`의 선수 671277 대신 `201` 선수 683083이 최종 `battingOrder`에 있다. 100~900을 시작 순서로 해석하는 것은 여기서 검사한 공식 응답의 경험적 해석이며, 별도의 MLB 필드 정의 문서를 확인한 것은 아니다. 최종 배열을 무조건 시작 명단으로 쓰지 않는다.

## timecode 검증과 증거 계층

[공식 일정 API](https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2026-04-01&endDate=2026-09-30&gameType=R), [공식 feed 예시](https://statsapi.mlb.com/api/v1.1/game/824940/feed/live?timecode=20260401_154000), [공식 boxscore 예시](https://statsapi.mlb.com/api/v1/game/824940/boxscore)를 직접 요청했다. 4·6·9월 3경기에서 T-35 요청은 투구와 대상 경기 타수가 각각 0이었고, T+120 요청에서는 경기 중 투구와 타수가 나타났으며 이후 타임스탬프 이벤트가 없었다. T+120의 타수는 사후 최종보다 적었다. 따라서 이 표본에서 이벤트·경기 누적 기록은 요청 시각에 따라 달라졌다. 전체 필드가 완전한 역사적 재생이라는 증명은 아니다.

세 경기 모두 T-180과 T-35 본문의 SHA가 같았다. gamePk 824940의 T-180은 13:15 UTC였지만 제공자 timestamp는 13:30:09 UTC여서 요청 이후였다. 특히 `metaData.timeStamp`가 선수 명단의 발표 시각을 입증하지 않는다. 세부 검산은 `probe-results.json`과 `verify_source.py`에 있다. 실제 원문은 2026-11-03에 수신했다. 당시 수신을 독립 인증할 수 있는 자료는 없어 strict PIT/전향 인증은 **0경기**다. 내부 해시는 저장 바이트 검증이지 독립적인 수신 증명이 아니다.

`feed/live`에는 gamePk·구단·선수 ID, probable, 타순과 경기 이벤트가 있다. boxscore에는 각 투수 ID·이닝·투구 수·삼진·볼넷 및 gamesStarted가 있고 대상 경기 기록은 `postgame`에만 정규화했다. gamePk 824940의 T+120 표본에는 pitchData 221건과 hitData 33건이 있었고 hitData에 발사 속도·각도 등이 있었다. 이는 표본의 수록 현황이지 모든 타구의 완전성 검증이 아니다. 별도 Statcast 일괄 추출은 하지 않았다. 향후에는 개별 원천의 추출 시각과 실제 수신 완료 시각을 별도 보존하고, 대상 경기 후 통계는 해당 경기 feature로 쓰지 않는다. 이전 경기 불펜 24/48시간 전수 이력은 이번 102개 표본만으로 재구성하지 않았다.

## 공식 배당 교집합

기존 두 워커의 Betman 원문 cache 76개 응답을 바이트 해시 검증 후 복사해 재사용했다. 선정 {q['selected']}경기 중 MLB `betId=2`, `betTypId=2`, `야구 승패`의 양쪽 가격, 시작 시각, 팀 ID·이름을 맞추고 유일한 상품을 선택한 다음 점수·결과를 대조해 {q.get('matched',0)}경기를 연결했다. 조건을 충족하는 유일한 후보가 없는 경기 {q.get('no_exact_verified_match',0)}개, 복수 상품·가격이 남은 경기 {q.get('ambiguous_multiple_markets_or_prices',0)}개다. 복수 가격에서는 유리한 가격을 고르지 않았다. 현재 원문 필드 `handi=21`은 해당 상품의 분류 코드로 기록했고, 별도의 핸디캡 가격을 혼합하지 않았다. `{c.get('retrospective_price_and_replay_pass',0)}`개는 이 배당 연결과 두 원문 및 이벤트 재생 검사를 함께 만족한다.

이 배당은 현재 수신한 회고 응답이다. 과거 관측 시각, T-30 가격, 해당 시각 구매 가능성이나 모든 특수 경기의 정산 규칙은 확인하지 못했다. Betman 경기 결과 대조는 신원으로 후보를 좁힌 뒤 수행했다. 66개를 운영 동결 픽이나 실제 체결 가격으로 부르지 않는다. 수익률·적중률·logloss·Brier·최대 낙폭·모델 비교는 계산하지 않았으며 각 지표는 미측정이다.

## 후속 가설과 재개

기존 시장 조건부 작성자 후보와 단순 24/48시간 불펜량 설계를 반복하지 않는다. 후속 가설 하나는 **사전 가용한 선발 투수의 이전 경기 평균 이닝과 전날·이틀 전 구원 투수별 투구 부담의 상호작용이 같은 시점 시장보다 추가 예측력을 주는가**다. 이는 앞선 불펜량·선발 정보와 겹치지만 두 변수의 사전 지정된 상호작용을 검증하는 차이가 있다. 적용하려면 모든 직전 완료 경기의 구원 투구 기록과 정확한 종료·수신 시각, 당시 가격 및 운영 동결 revision을 확보한 뒤 별도 사전등록과 시간 분할이 필요하다. 이번 작업은 피팅하지 않았다.

실제 HTTP 수신 기록·요청 파라미터·응답 상태·원문 SHA는 `evidence/*.meta.json`, 압축 원문은 해당 `.json.gz`에 있다. 압축을 풀면 원문 바이트가 된다. `manifest.json`은 전체 입력 해시와 수집 현황을 기록한다. 당초 최신 시즌을 2025년으로 잘못 선택해 24경기의 예비 원문을 받은 뒤 2026년 수집 전에 선정 규칙을 정정했다. 2025년 예비 자료는 보존하되 2026년 표본에 포함하지 않았다. 앞선 간이 탐색 4요청은 수신 로그가 없어 본 증거 집계에 포함하지 않는다.

재개 명령: `python -X utf8 experiments/mlb-official-information-20261103/collect.py capture`. 이미 해시 검증된 응답은 재요청하지 않는다. 실행 중 lock은 중복 실행을 막는다. 불완전한 캐시 쌍 또는 중단 표식은 자동 덮어쓰지 않으며 확인이 필요하다. 403/429 등 실패를 감지한 새 실행기는 실패를 보존하고 중단한다. 원문 검증: `python -X utf8 experiments/mlb-official-information-20261103/verify_source.py`, `python -X utf8 experiments/mlb-official-information-20261103/verify_integrity.py`; 단위 검사: `python -X utf8 -m pytest tests/test_mlb_official_source_20261103.py -q -p no:cacheprovider`. 독립 복제본 두 곳에서 `core.autocrlf=true`와 `false`로 각각 checkout한 621개 산출물의 Git blob 해시 불일치는 0이었다. 이 검사는 해당 시점 커밋 `cadc765`의 바이트 보존만 확인한다. 이 작업은 운영 모델·원장·자동화·배포를 변경하지 않았다.
'''
(P/'report.md').write_text(text,encoding='utf8',newline='\n')
print('report generated')
