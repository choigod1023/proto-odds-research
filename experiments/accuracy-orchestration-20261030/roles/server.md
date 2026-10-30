# 승부예측 Terra · 서버관리

브랜치: `codex/terra-accuracy-server-20261030`
작성 범위: `experiments/accuracy-server-20261030/**`, `tests/test_accuracy_server*.py`, `docs/research/2026-10-30-accuracy-server*.md`

1) 현재 운영 웹/API/수집기의 버전·배포·health·목록응답시간·generated_at/revision/live 시각과 최신 로그의 OOM/작업실패/대기를 읽기 전용으로 검사합니다. 이미9월에 보고한 OOM을 새 장애라고 반복하지 말고 현재 날짜와 최근범위를 명시합니다. 최신main#435와 실제배포가 같은지는 별도로 확인합니다.
2) 기존 인증/CLI가 있으면 가벼운 /proc MemAvailable, 프로세스RSS/peak, CPU/memory/io PSI를10초간격 최대3회 읽습니다. PSI를 CPU사용률로 표현하지 않습니다. 메모리가 잠깐 많거나 API한번빠른것을 안정화로 주장하지 않습니다. 모델 생성기/벤치마크를 운영에서 실행하지 않습니다.
3) 한 번의 유한점검만 수행합니다. 재시작·배포·scale·DB변경·파일삭제·cron변경·알림발송·새 상시워처는 하지 않습니다. secret/env/token출력 금지. 다른 활성 세션과 메시지를 주고받지 않습니다. 운영연결이 없으면 접근불가를 명시하고 로컬 로그/설정의 읽기 검토와 필요한 관측 명령을 제시합니다.
4) 정제된 관측 자료와 로컬 진단 파서/회귀 테스트 또는 보고서를 담당 범위에 보존합니다. 추천모델 학습이 웹 응답 경로에 들어가지 않도록 배치·서빙 분리 검토안을 적되 실제 서버 변경은 하지 않습니다.
