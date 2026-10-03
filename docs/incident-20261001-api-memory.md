# 2026-10-01 웹 갱신 장애 점검

## 운영에서 직접 확인한 사실

- 운영 checkout: `97ffc5da068dfa66b57c85cabaa9d56944288160`.
- Fly `proto-odds-collector`, machine `784e666f002328`, shared CPU 1 / RAM 1024MB.
- 이미지 `deployment-01M3GBHR81DQ0FBT4M9PC5FZ8Y`.
- 11:32 UTC 전후 Fly proxy에서 연결 시간초과와 `could not find a good candidate within 40 attempts at load balancing` 반복. 목록·실시간 점수·배당·추천 API가 영향받았다.
- 11:32:28 UTC 점수 수집은 180초 제한을 넘겼고 저장 체크포인트가 없었다.
- 11:33:11 UTC MemAvailable 69,304KB. CPU PSI some avg60=73.15, memory=39.28, I/O=41.56. PSI는 작업 대기 압박이지 CPU 사용률이 아니다. 주변 로그에서는 메모리 여유 0~9MB, 보조 작업 대기 약 5시간 20분도 관측했다.
- Pages HTML은 HTTP 200 / 0.310초였다. `/health`는 200 / 3.538초였지만 픽 생성 시각은 09:52:26 UTC, 점수 저장은 11:28:40 UTC였다. HTML 접근 가능과 데이터 갱신 성공은 별개다.
- **11:34:06 UTC(20:34:06 KST) 커널 OOM으로 Python PID645 종료**, anon-rss 387,760KB. Fly exit137은 11:34:12.941, 자동 재시작은 11:34:15.769 UTC. Fly의 oom_killed=false와 달리 커널 로그로 이번 OOM을 확인했다. PID645의 명령줄은 확보하지 못했으므로 프로세스 역할을 확정하지 않는다.
- 재시작 후 동일 checkout, MemAvailable 664,048KB. 이는 재시작 직후 값이며 해결 증거가 아니다.

점검자는 재시작·배포·증설·운영 DB 변경을 하지 않았다. 위 수치는 간헐 관측이며 전체 구간의 최대 메모리나 무장애를 입증하지 않는다.

## 코드에서 확인한 위험과 수정

기존 목록 워머는 SQLite TEXT 전체와 JSON 객체 그래프를 읽은 다음 불필요한 성능 이력을 제거했다. 기존 캐시를 유지한 상태에서 새 전체 객체를 만들므로 갱신 순간 메모리 피크가 커질 수 있다. 산출물 gzip 경로도 전체 문자열과 UTF-8 복사본을 만들었다. HTTP 서버는 요청마다 스레드를 만들고 소켓 대기 상한이 없었다. 이 경로들을 발견했지만 특정 경로가 이번 OOM의 단독 원인이었다는 프로파일은 확보하지 않았다.

수정 사항:

1. SQLite readonly blob과 읽기 트랜잭션으로 payload와 revision을 같은 스냅샷에서 읽는다. 스키마와 저장 포맷은 바꾸지 않는다.
2. ijson으로 경기 하나씩 읽어 상세를 압축하고 목록 카드만 남긴다. prediction_performance는 객체를 만들지 않고 건너뛴다. 실패 시 이전 뷰는 그대로 유지한다.
3. 산출물 gzip은 64KiB 단위로 읽는다.
4. HTTP 워커 8개 / backlog 8 / 소켓 I/O timeout 15초. 포화 시 best-effort 503 + Retry-After. CPU·DB 작업의 전체 실행 시간 제한은 아니다.

## 로컬 검증과 절충

- Python: `python -m pytest -q` — 871 passed, 58 subtests passed.
- 웹: `node --test --test-concurrency=1` — 238 passed.
- 웹 빌드: `npx vite build --outDir ../.validation-build` 성공. 운영 산출물은 수정하지 않았다.
- 실제 TCP 테스트: 포화, 읽기/쓰기 시간초과, 연결 종료, 핸들러 예외, 스레드 시작 실패 후 슬롯 반환.
- SQLite 테스트: 동시 writer 중 이전 snapshot 유지, stream 종료, 누락/잘못된 JSON, 이전 캐시 보존.
- 재현: `python scripts/benchmark_streamed_views.py`. Git의 고정 baseline `97ffc5da`와 현재 코드를 동일한 로컬 합성 DB(250경기, 경기별 선수 이력 160개)로 비교한다.
- Python 추적 메모리 피크 **66.08 → 2.43MiB (-96.33%)**, 압축 해제한 목록 JSON 동일.
- 추적 없이 처리 시간 **0.259 → 0.838초**. 추적 도구를 켜면 2.02 → 3.02초. 각 단일 측정이며 CPU 개선으로 해석하지 않는다.

이 수치는 합성 데이터의 Python 할당이며 OS RSS, C 라이브러리 메모리, 운영 최대치가 아니다. 스트리밍은 메모리를 줄이는 대신 파싱 비용이 늘었다. CPU·메모리 병목이 함께 있던 운영에서 배포 후 두 지표를 모두 확인해야 한다. 전체 프로세스 OOM 방지를 보장하지 않는다.

## 배포 전후 확인 사항

현재 구현/로컬 테스트와 운영 반영을 구분한다. `requirements.txt` 및 `/app/supervisor.py` 변경으로 **새 이미지 배포가 필요**하다. Git 병합만으로 적용되지 않는다.

승인 후 배포한다면 checkout·이미지·ijson import, 목록/상세 응답, DB와 목록 generated_at/revision 일치, 워머 완료 및 메모리/CPU 압박, 새 OOM/수집 실패를 확인한다. 캐시만 빠르게 반환하는 상태를 회복으로 판정하지 않는다.
