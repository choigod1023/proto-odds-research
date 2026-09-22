# #224 운영 적용 후 메모리 부족 재현

## 배포 확인

- #224 CI 성공 확인 후 병합. main: 9ccd3e7744bb02845e878c493c7d44b6c0b230a4.
- Fly proto-odds-collector / machine 784e666f002328, 1 shared CPU / 1024MB 유지.
- 이미지: deployment-01M33D7504988SZW128MAYMSNN.
- 새 프로세스 시작: 2026-09-22 01:56:34 UTC / 10:56:34 KST.
- SSH에서 checkout 커밋, zlib.compress 캐시, del previous_picks 적용을 직접 확인.
- 초기 summary HTTP 200 / 1.80초. 이것은 배당/픽 최신성 회복의 증거가 아니다.

## 실제 관측

추가 생성 작업은 실행하지 않고 /proc 상태를 약 10초 간격으로 36회 읽었다.
메모리 압박 중에는 샘플 간격도 늘어났으므로 순간 최대값을 전부 포착한 것은 아니다.
측정 프로세스 자체 RSS 약 9~16MB도 부하에 포함된다.

- 10:58:16 KST: MemAvailable 86,228KB. odds_live 217,380KB,
  live_scores 213,264KB, supervisor 161,224KB, migration 146,000KB 동시 실행.
- 과거 데이터 이관은 10:58:33에 완료. 정규 전체 계산은 10:58:36 시작.
- 11:00:10: MemAvailable 0KB. pickster_eval 282,724KB, odds_live 225,152KB,
  live_scores 196,660KB, supervisor 150,488KB 동시 실행.
- 11:00:33: 커널 OOM이 PID747 종료. supervisor 로그에서도 pickster_eval rc=-9 확인.
  커널의 종료 직전 anon-rss는 287,104KB.
- 11:02:27: MemAvailable 16,148KB. baseball_live_features 309,324KB,
  odds_live 244,268KB, live_scores 152,448KB, supervisor 152,812KB 동시 실행.
- 11:02:53: 커널 OOM이 PID761 종료. supervisor 로그에서도 무료 야구 feature rc=-9 확인.
  종료 직전 anon-rss는 325,760KB.

부팅 직전 10:56:32의 종료 로그는 새 버전의 OOM으로 집계하지 않았다.
종료된 프로세스 하나만 원인인 것은 아니다. 전체 사용량과 작업 중첩이 문제다.

## 확인한 코드 경로 및 후속 수정

1. pickster_eval._leaderboard는 마지막 순위표만 사용하는데 과거 전체를 읽었다.
   이제 기존 인덱스(stream, observed_at, id)로 역순 LIMIT 1 조회한다.
2. pickster_eval._latest_picks 및 baseball_live_features._latest_context는 전체 이력
   리스트를 만들고 최신값을 다시 추렸다. 이제 DB 커서에서 한 건씩 읽는다.
3. baseball_live_features._latest_crowd도 마지막 한 건만 DB 조회한다.
4. RuntimeDatabase.events의 fetchall 결과와 디코딩 객체가 동시에 쌓이던 구조를
   없앴다. 기존 events 반환형은 list로 유지하고 iter_events를 별도 제공한다.
5. 로컬 JSONL 경로도 줄 단위로 읽는다. 순서, 동률시 id 순서, 사전 관측 자격과
   최신 결과를 보존하는 회귀 테스트를 추가했다. 데이터 삭제/집계 기준 변경 없음.

## 남은 위험

- 이번 수정의 실제 RSS 감소 및 OOM 해소는 아직 운영 미검증이다.
- 픽별 최신 기록 자체, 날씨 전체 이력, 실시간 점수의 전체 picks 읽기,
  부팅 이관 작업의 동시성은 여전히 추가 최적화 후보다.
- 최신 조회/스트리밍만으로 모든 메모리 부족이 해결된다고 주장하지 않는다.
- 스트리밍 중 읽기 트랜잭션이 유지되므로 장시간 계산 시 WAL 증가도 관측해야 한다.
- #224만으로 1GB 운영이 안정적이라는 판정은 실패했다. 증설하지 않고 후속 수정의
  효과를 검증하되, 월 $6.54 증설 회피는 아직 확정된 절약액이 아니다.
- 30분 간격 감시는 계속된다. 같은 장애를 반복 알리지 않고 새 장애/의미 있는 변화만 보고한다.

## 테스트

Python 766 passed, 58 subtests passed. 웹 232개 테스트 및 프로덕션 빌드 통과.
후속 수정은 별도 PR이며 이 문서 작성 시점에는 운영에 적용하지 않았다.
