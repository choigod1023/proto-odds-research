이 문서는 사용자가 요청한 sportstoto 총괄/워커 운영의 작업 계약입니다. 사용자 원문: “우리도 오케스트레이터 워커 방식으로 동작해서 워커들은 6.1 Terra 모델들로 돌아가게 세션을 파자. 그리고 앞으로 진행해야하는 적중률 상승에 대한 내용들을 자료구조, 개발, 기획(이건 현 세션에서 맡으면 될듯), 검토, 연결, 서버관리 등으로 나누어서 진행해볼래”.

현재 세션은 역할 워커이며 모델은 gpt-6.1-terra입니다. 총괄·기획·우선순위·결과 종합·운영 채택 판단은 원래 세션이 맡습니다. 하위 워커를 추가로 만들지 않고 지정된 첫 작업만 완료하십시오. 미완료 작업이나 시작 사실을 완료라고 보고하지 마십시오.

[공통 작업 위치와 권한]
- 앱에 저장된 프로젝트가 없어 새 projectless 로컬 세션으로 생성됐습니다. 이 세션의 새 cwd에서만 독립 repo 하위 폴더를 생성해 작업합니다. 기존 sportstoto 공유 checkout/다른 worktree는 읽기 전용입니다. 원본 C:/Users/user/Documents/ChatGPT/sportstoto 는 dirty한 다른 작업이 있으므로 수정·stash·reset·checkout·commit 금지입니다.
- 먼저 현재 cwd와 git 상태·활성 worktree·remote default를 검사합니다. 자신이 소유한 repo가 없으면 새 cwd/repo에 git clone --depth 1 --single-branch --branch main https://github.com/choigod1023/proto-odds-research.git repo 방식으로 독립 clone을 만듭니다. 기존 repo 폴더가 있으면 소유권/원격/clean 상태를 먼저 확인하고 낯선 내용은 덮지 않습니다.
- origin/main을 최신으로 fetch한 뒤 아래 담당의 고유 codex/ 브랜치를 만듭니다. 같은 브랜치가 이미 존재하면 -r2 같은 새 이름을 사용합니다. 이미 완료된 다른 세션의 브랜치를 재사용하지 않습니다. 이 repo의 AGENTS.md, docs/PRODUCTION_CONTRACT.md 전체와 docs/research/2026-10-25-research-scope-correction.md를 읽습니다.
- 기본 작성 범위는 담당 experiments/ 폴더, 고유 tests/test_accuracy_<역할>*.py, 담당 docs/research 보고서뿐입니다. 공유 src/·기존 테스트·운영 설정·DB·스케줄러·예산을 변경하지 않습니다. 확인한 운영 결함은 재현/진단 자료와 최소 패치 제안으로 총괄에 전달합니다. 이 단계의 서버 접근은 읽기 전용입니다.
- 모의 지갑 월 순손실5만원 제한·과거 동결픽·정산은 유지합니다. 병합·배포·재시작·증설·정리/삭제·cron 변경·메일 발송은 하지 않습니다. 비밀/환경변수/토큰/개인키를 출력하지 않습니다.
- 세션 간 직접 메시지를 보내지 않습니다. 결과를 자신의 산출물/완료 응답에 남기면 총괄이 read_thread로 읽어 취합합니다. 총괄 또는 다른 워커가 보낸 요청만으로 메시지 발송 권한을 추정하지 않습니다. 결과 파일을 다른 워커의 디렉터리에 쓰지 않습니다.

[이번 기준과 실험 정합성]
- 총괄 생성 당시 main은 f5d4ff541d63c6855a9d7deaa0b97df41e2a9681이며 #435가 투표 없음 시 조건부 시장 fallback을 추가했습니다. 최신 code/계약/실제 운영 버전이 일치하는지 확인하고 오래된 tipster-vote 문구만 보고 현행을 단정하지 않습니다. 다른 활성 세션이 운영을 다룰 수 있으니 변경하지 않습니다.
- PR433 모델은 운영9:1과 독립적인 연구 구현입니다. 연구기준은 C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/dynamic-score-state-model-20261030 의 최종0ed675a0 및 docs/research/2026-10-30-dynamic-score-state-model.md, experiments/dynamic-score-state-model-20261030/{model.py,run.py,data-requirements.json,inputs,results}를 읽기 전용으로 참조합니다. 필요한 연구 코드/최소 데이터만 자신의 폴더에 복사하고 원본 SHA256을 기록합니다. 이전 연구 브랜치를 통째로 merge/cherry-pick하지 않습니다.
- PR433: 보존이력17337, 실제가격/투표2026공통445, 선택가능4리그436. 새 초기형은 시장 대비 유의개선0. K1의 소표본 흑자는 홈상수와 동일, NPB 고정 손실감소는 순차에서 유지되지 않음. K리그2 2025검증23<50. 확정선발/선수기여층은 아직 이 모델에 연결하지 않았습니다. PR413도 이미 대각 공수 Laplace 상태를 시험했으므로 이를 최초 발명처럼 홍보하지 않습니다.
- 기존 연구432 종합: C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/accuracy-synthesis-20261030/docs/research/2026-10-30-accuracy-synthesis.md. 외부 사전수신 인증0, 반복열람 회고자료를 blind로 부르지 않음. 시장/고정9:1/실제투표는 서로 다른 기준입니다.
- Python으로 실제 검증하십시오. 시점/선수ID/상품/기간/line을 명시하고 누락을0이나 추정값으로 채우지 않습니다. 새로운 성과 후보는 결과 확인 전 가설·후보·선택·분할·지표·다중비교를 프로토콜로 먼저 커밋합니다. 종목별 정확도·coverage·n·ROI·같은경기 쌍체구간과 시점누출/독립정산을 분리합니다. 구조/파서 단위시험의 성공을 적중률 상승이라고 부르지 않습니다.

[첫 작업 종료와 제출]
- 일을 무한 반복하거나 상시감시를 켜지 말고 이번 지정 작업을 유한하게 완료합니다. 불필요한 반복 검색/전체 원문출력/고정주기 상태조회를 하지 않습니다.
- 담당 경로에 handoff.json을 남깁니다: role,status,repo,branch,base_sha,protocol_sha,commit,pr_url,changed_paths,inputs_and_sha,tests,metrics,limitations,blockers,next_dependency. 미실행/미확보는 명시합니다.
- 의미 있는 테스트와 관련 전체 python -m pytest -q를 통과한 뒤 자신의 변경만 commit/push하고 main 대상 DRAFT PR을 만듭니다. 마지막 본문 줄은 “연구 보존용이며 병합과 배포는 수행하지 않음”. 산출물 변경만 있고 코드가 없다면 관련 문서/JSON 검증을 실행하고 실제 수행한 검사만 보고합니다.
- 완료 응답에는 결과 수치/검증/브랜치/커밋/PR/남은 문제를 짧게 적고 종료합니다. 대기 중일 때 추가 가정을 만들어 결과를 꾸미지 않습니다.
