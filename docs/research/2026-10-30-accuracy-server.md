# 2026-10-30 운영 읽기 전용 점검

점검 시각 2026-10-30 18:40~18:43 KST (09:40~09:43 UTC). 한 차례의 제한된 관측이며 부하 시험이나 장기 가용성 측정이 아니다. 운영 코드·DB·스케줄·프로세스는 변경하지 않았다.

## 버전과 응답

- 원격 main `f5d4ff541d63c6855a9d7deaa0b97df41e2a9681` (사이트 빌드, 제목에 f9058fb). Fly `proto-odds-collector` machine 784e666f002328, v82 started, image `deployment-01M6613ES1R276GZECMASE5BEW`, 09:33 UTC 갱신. `/data/repo` HEAD는 PR #435 merge commit `f9058fb98e2bfaa1fd8dede0f5f7ef70c8cdcd77`. 실행 이미지의 `/app/supervisor.py` SHA256 `342929482325033e35ac4e8e85eee15e21d1abb17b3ac7baf1a76b7117ded7a5`는 main 동명 파일과 일치한다. `/data/repo/deploy/supervisor.py` 해시는 `31218911b781ae0f963eccd48096a457bac73214bb8e1fdd15313c5917e2570c`. 실행 이미지 전체나 실행 중인 모든 코드가 main과 동일하다는 증거는 없다. 데이터 checkout의 HEAD와 이미지 파일을 구분해야 한다.
- 공개 [웹](https://choigod1023.github.io/proto-odds-research/) HTTP 200, 0.278초. [health](https://proto-odds-collector.fly.dev/health) 200, status ok, 1.055초. 각각 단일 측정이다. health ok는 데이터 최신성이나 수집 성공을 보증하지 않는다.
- [최근 목록](https://proto-odds-collector.fly.dev/api/matches?scope=recent)은 09:42:38 UTC에 200, 0.512초, 2,984,580 bytes. generated_at 09:18:08 UTC, view revision 09:36:54.716483 UTC. revision은 원본 생성 시각과 다르다. [전체 picks](https://proto-odds-collector.fly.dev/api/picks)는 09:41:11 UTC에 200, 8.893초, 39,682,129 bytes. 각 수치는 클라이언트가 본 전체 수신 시간으로 네트워크/파싱 영향이 있고 단일 표본이다.
- [라이브](https://proto-odds-collector.fly.dev/api/live-scores) 09:41:16 UTC 200, 4.510초, 7,008,092 bytes, generated_at 09:41:06 UTC. 직후 health 기준 live 저장 09:41:11.914376, odds 저장 09:18:24.712384, picks 저장 09:36:54.716483, odds/picks 생성 09:18:08 UTC. 저장·revision과 원 데이터 생성 시각은 별개다. 09:42:38 목록의 생성 시각은 약 24분 30초 전이지만, 일정한 갱신 지연이나 장애 원인은 이 표본만으로 확정할 수 없다.

## 최근 로그와 자원

`fly logs --no-tail`이 반환한 범위는 09:30:56~09:40:39 UTC. 09:30:56 여유 470MB / 필요 512MB에서 보조 작업 849~1199초 대기, 09:38:50에도 배당·판정 우선 대기 36~261초. 09:38:46 KBO, 09:39:12 MLB, 09:39:38 NPB 외부 점수 API ReadTimeout(25초). 개별 오류이며 해당 수집 전체의 최종 실패 여부는 이 로그에서 확정하지 못했다. 09:32:55 SIGINT와 재시작은 v82 배포 시각과 인접하지만 원인을 단정하지 않는다. 관측 범위에 명시적 OOM 로그는 없으며, 과거 9월 사건을 이번 신규 OOM으로 세지 않는다. 로그 범위 밖 장애 여부는 알 수 없다.

09:34:59~09:37:12 경량 판정 단계 로그에서 peak RSS는 76,836→702,072 KiB, 133.8초에 124경기 처리 완료. 09:38:12 웹 응답 준비 build 57.90초, IPC 0.08초, 2,969경기. 이 값은 개별 프로세스 피크이며 시스템 전체 메모리 사용량이 아니다. 별도 현재 프로세스 RSS/HWM은 이미지에 ps/top이 없어 확보하지 못했다.

`/proc`의 제한된 세 번의 읽기에서 MemAvailable 590,036 / 543,336 / 535,780 KiB, CPU PSI some avg10 49.73 / 73.90 / 42.98%, memory PSI some avg10 0 / 0 / 0%, IO PSI some avg10 0.78 / 2.04 / 0.50%. 세 SSH 호출은 대략 09:41~09:42 UTC에 실행됐으나 각 샘플에 독립 타임스탬프를 남기지 못했고 정확히 10초 간격도 아니다. PSI는 대기 압력이지 CPU 사용률이 아니다. 읽기 출력 뒤 Windows CLI가 `The handle is invalid`를 반환했으므로 수치와 종료 오류를 함께 기록한다. 이 순간 표본으로 안정화를 주장할 수 없다.

## 분리 검토안과 후속 관측

모델 학습·자료 수집·대규모 응답 생성은 HTTP 요청 경로 밖의 제한된 배치로 실행하고, 완료 검증된 불변 버전 산출물을 원자적으로 게시한 뒤 서빙은 해당 산출물만 읽도록 검토한다. 원래 generated_at, revision, 실제 수신 시각과 T-30 동결 원장 및 마지막 정상 응답을 보존한다. 작업 우선순위·동시 실행 제한·배치 실패와 게시 실패를 분리해 계측한다. 기존 `deploy/web/README.md`의 미배포 edge 제안은 캐시 대체 응답의 무제한 노후화와 콜렉터 측 생성 부하를 명시한다. 별도 기계·비용·배포는 승인과 검증 전 실행하지 않는다.

후속 점검은 정규 작업 로그에서 최근 시간 범위의 완료·실패·대기 시간을 확인하고, `flyctl status -a proto-odds-collector`, `flyctl logs -a proto-odds-collector --no-tail`, `/health`, `/api/matches?scope=recent`, `/api/live-scores`의 시각을 비교한다. 정확한 시각을 붙여 `/proc/meminfo`, `/proc/pressure/{cpu,memory,io}`, 각 프로세스 `/proc/<pid>/status`의 VmRSS/VmHWM을 10초 간격 최대 3회 읽을 수 있는 도구를 마련한다. 요청 경로 학습 여부 및 전체 배포 SHA는 별도 코드·이미지 식별 확인이 필요하다.
