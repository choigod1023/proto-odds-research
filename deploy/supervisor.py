"""fly.io 상시 수집 감독자 — 노트북을 덮어도 데이터가 끊기지 않게.

왜 필요한가
------------
이 프로젝트는 **경기 전 시점 값**이 생명이다(누수 방지). 그런데 지나간 배당은
다시 못 받는다. 노트북에서 돌리면 덮는 순간 그 시간대 데이터는 영구 손실이다.

무엇을 하는가
-------------
1. 오래 도는 수집기 3개를 자식 프로세스로 띄우고, 죽으면 다시 살린다
   (스크립트가 이미 `--loop 초` 를 지원하므로 cron 이 필요 없다)
2. 하루 1회짜리(xg_watch)는 여기서 주기를 재서 호출한다
3. 주기적으로 결과를 GitHub 에 push → 레포에 남고 Vercel 이 자동 재배포

데이터는 어디 있나
------------------
fly 머신의 파일시스템은 재시작하면 날아간다. 그래서 **볼륨**(/data)에 레포를
클론해 두고 거기서 돌린다. 볼륨은 재시작에도 남는다. 다만 볼륨은 그 머신에만
붙어 있으므로, **git push 가 진짜 백업**이다. 둘 다 한다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(os.environ.get("REPO_DIR", "/data/repo"))
REMOTE = "https://github.com/choigod1023/proto-odds-research.git"

# (이름, 명령) — 전부 `--loop 초` 로 스스로 반복한다
LOOPERS = [
    ("배당 스냅샷", [sys.executable, "-u", "src/snapshot.py", "--loop", "900"]),
    ("선발 예고", [sys.executable, "-u", "src/info_watch.py", "--loop", "1800"]),
    # MLB 공식 예정 선발·시즌 투수 지표·부상 상태, KBO 최근 12선발 지표,
    # NPB.jp 공식 선발·양대 리그 순위, J.LEAGUE.jp J1/J2 공식 순위,
    # FIBA·Volleyball World·네이버 농구/배구 선수·팀 기록.
    # 선발 변경이 잦은 경기 직전에도 화면이 한 시간 낡지 않도록 예고와 같은 주기다.
    ("선수·팀 정보", [sys.executable, "-u", "src/player_info.py", "--loop", "1800"]),
    # 무료 공식·공개 원천으로 시즌/상대 성적, K-BB/9, 최근 팀 득실과
    # 실제 타선 공개 여부를 변경 이벤트로 저장한다.
    ("무료 야구 컨텍스트", [sys.executable, "-u", "src/baseball_context_watch.py",
                       "--loop", "1800"]),
    ("해외 배당", [sys.executable, "-u", "src/overseas_watch.py", "--loop", "900"]),
    # 공개 HTML만 저빈도로 읽는다. 첫 관측은 baseline으로 두고 이후에 관측한
    # 미결 픽만 전향적으로 검증한다.
    ("공개 픽스터", [sys.executable, "-u", "src/pickster_watch.py", "--loop", "900"]),
    # API 키가 필요 없는 Open-Meteo 예보 변경 이력을 보존해 T-24h/T-6h
    # 당시 정보로 백테스트할 수 있게 한다.
    ("무료 날씨", [sys.executable, "-u", "src/weather_watch.py",
                 "--league", "all", "--loop", "21600"]),
    # 실시간 배당 — 화면 배당이 한 시간씩 낡지 않게 한다(아래 serve_live 가 서빙).
    # 2026-08-13 실측: 화면 배당 231건 중 73건(32%)이 원천과 달랐다.
    ("실시간 배당", [sys.executable, "-u", "src/odds_live.py", "--loop", "60"]),
    # odds_live가 수집 직후 경량 시장 판정까지 같은 데이터로 연쇄 갱신한다.
    ("실시간 추천", [sys.executable, "-u", "src/recommendation_refresh.py", "--loop", "300"]),
]

# 재시작 직후 9개 수집기가 한꺼번에 메모리를 잡으면 1GB 머신에서도 커널 OOM이
# generate_v2를 죽인다. 화면 판정에 필요한 가벼운 수집기를 먼저 띄우고, 선수·날씨·
# 픽스터 보강은 판정 게시 뒤로 분산한다.
LOOPER_START_DELAYS = {
    "선발 예고": 30,
    "실시간 추천": 45,
    "해외 배당": 90,
    "무료 야구 컨텍스트": 180,
    "선수·팀 정보": 240,
    "공개 픽스터": 300,
    "무료 날씨": 360,
}

# 하루 1회짜리 — FootyStats 는 연속 요청을 막으므로 자주 찍을 이유도 없다.
# 리그를 넷으로 늘린 이유는 판정 시점 때문이다: K리그1 만이면 필요 표본(441경기)에
# 2027년 8월에나 닿는데, 넷을 합치면 2026년 11월이다. 자세한 계산은 xg_watch.py.
DAILY = [
    ("K리그1 xG", [sys.executable, "-u", "src/xg_watch.py", "kleague1"]),
    ("K리그2 xG", [sys.executable, "-u", "src/xg_watch.py", "kleague2"]),
    ("J1 xG", [sys.executable, "-u", "src/xg_watch.py", "j1"]),
    ("J2 xG", [sys.executable, "-u", "src/xg_watch.py", "j2"]),
]

# 산출물 생성 — 수집만 하고 갱신을 안 하면 사이트가 어제 값에 멈춘다.
# 실제로 2026-07-27 이후 픽이 안 돌아 7/28 에 7/24 경기가 예정으로 떠 있었다.
# 순서가 중요하다: 원본 → 데이터셋 → 산출물.
PUBLISH = [
    # ⚠️ 이 단계가 없어서 **산출물 자동 갱신이 한 번도 작동하지 않았다.**
    #    와이즈토토 아카이브(data/raw/wisetoto/*.html.gz)는 .gitignore 대상이라
    #    머신의 clone 에 안 딸려온다. 그래서 build_dataset 이 매 주기
    #    "캐시가 비어 있습니다"(rc=1)로 끝났고, 뒤 단계가 전부 break 됐다.
    #    사이트는 내가 로컬에서 돌려 push 할 때만 갱신되고 있었다.
    #    · 캐시된 회차는 대기 없이 continue 하므로 2회차부터는 몇 초에 끝난다
    #    · 첫 실행만 553회차 × 2.5초 ≈ 23분 (타임아웃 1800초 안)
    #
    # 세 번째 칸은 **critical** 이다. True 면 실패 시 이번 주기를 중단한다.
    # ⚠️ 예전에는 전 단계가 실패하면 무조건 break 였다. 그래서 7단계(손실등급)가
    #    OOM 으로 죽자 8·9단계까지 같이 멈춰, loss_grades·combo·today_combo 가
    #    하루 넘게 낡은 채로 사이트에 나갔다(2026-07-31 발견).
    #    한 단계가 깨져도 **그 산출물을 읽지 않는 단계는 돌아야 한다.**
    # 네 번째 칸은 **제한 시간(초)** 이다.
    # ⚠️ 전부 1800초로 두었더니 '데이터셋 재빌드' 가 타임아웃으로 죽었고(01:53:16),
    #    critical 이라 뒤가 전부 멈췄다. 로컬에선 2분 19초인데 머신에서 30분을 넘긴다 —
    #    shared-cpu-1x 를 수집기들과 나눠 쓰기 때문이다. 무거운 단계에 시간을 더 준다.
    ("아카이브 수집", [sys.executable, "-u", "src/collect.py",
                  "2023", "2024", "2025", "2026"], False, 2400),
    # 유일한 필수 단계 — games.csv·bets.csv 를 만든다. 뒤가 전부 이걸 읽는다.
    # ⚠️ 5400초로는 모자랐다. 2026-08-08 실측: 559회차 중 약 460회차까지 가고
    #    타임아웃 — 남는 건 언제나 맨 뒤, 즉 **올해**다. 그래서 최근폼이 통째로
    #    비었고 해설 249건 중 56%가 "이번 시즌 기록이 충분히 쌓이지 않았다"였다.
    #    (잘린 파일이 그대로 남던 문제 자체는 build_dataset 의 원자적 교체로 막았다.)
    #    무거운 주기가 6시간(21600s)이므로 9000초는 충분히 들어간다.
    ("데이터셋 재빌드", [sys.executable, "-u", "src/build_dataset.py"], True, 9000),
    # 정보 시차 결합 — 표본이 쌓이는 걸 눈으로 보려고 갱신한다.
    # 원본에서 매번 다시 계산하므로 실패해도 뒤에 영향이 없다.
    ("정보시차 결합", [sys.executable, "-u", "src/info_lag.py"], False, 1200),
    # 손실 축소 등급표 — 이 프로젝트의 최종 산출물. 사이트가 읽는다.
    ("손실등급 갱신", [sys.executable, "-u", "src/loss_filter.py"], False, 2400),
    # ⚠️ combo 는 bets.csv 를 읽는다. 데이터셋 재빌드 뒤에 와야 한다.
    ("조합표 갱신", [sys.executable, "-u", "src/combo.py"], False, 2400),
]

# 가벼운 단계 — **매시간** 돈다.
#
# 왜 나눴나 — 예전엔 아홉 단계가 한 덩어리로 6시간마다 돌았다. 그런데 화면에서
# 실제로 자주 바뀌어야 하는 건 오늘의 픽·해설뿐이고, 그걸 만드는 생성기들은
# **발매 중인 회차를 직접 긁는다**(generate_v2 가 snapshot._fetch·find_live_rounds 를
# import 한다). 즉 무거운 단계 없이도 새 값이 나온다.
# 반대로 무거운 쪽(아카이브·데이터셋·손실등급·조합표)은 과거 이력 통계라
# 한 시간 만에 달라질 게 없는데, shared-cpu-1x 를 수집기들과 나눠 쓰느라
# 한 바퀴에 30분을 넘긴다. 이걸 매시간 돌리면 수집기가 굶고 OOM 위험만 커진다.
#
# ⚠️ 이 단계들은 games.csv 를 읽으므로, 그 파일이 없으면 무거운 쪽을 먼저 돌려야 한다.
#    (run_publish 가 그때는 강제로 HEAVY 를 낀다)
PUBLISH_LIGHT = [
    ("픽스터 전향판정", [sys.executable, "-u", "src/pickster_eval.py"], False, 300),
    ("무료 야구 feature", [sys.executable, "-u", "src/baseball_live_features.py"], False, 300),
    # 홈페이지가 실제 읽는 전마켓 판정을 먼저 만든다. 캐시된 LLM 해설은 재사용하되
    # 새 API 호출은 뒤의 보강 단계로 미뤄 수치 판정 게시를 막지 않게 한다.
    ("전마켓 빠른 판정", ["env", "LLM_MAX_CALLS=0", sys.executable, "-u",
                    "src/generate_v2.py"], False, 1800),
    ("가격분석 생성", [sys.executable, "-u", "src/generate_today.py"], False, 1800),
    # ⚠️ today_combo 는 today.json·combo.json·loss_grades.json 을 읽는다.
    #    combo·loss_grades 는 무거운 쪽이라 최대 6시간 낡을 수 있지만,
    #    그건 과거 통계라 낡아도 값이 같다. 낡은 입력으로나마 도는 편이 낫다.
    ("오늘의 조합", [sys.executable, "-u", "src/today_combo.py"], False, 1200),
]

DATABASE_BOOTSTRAP = [sys.executable, "-u", "src/migrate_runtime_db.py", "--critical"]
DATABASE_MIGRATE = [sys.executable, "-u", "src/migrate_runtime_db.py"]

# 느린 레거시 산출물과 새 LLM 호출은 빠른 판정이 직접 서빙된 다음 실행한다.
# 둘 중 하나가 늦거나 실패해도 홈페이지의 배당·판정 시각은 이미 갱신돼 있다.
PUBLISH_ENRICH = [
    ("레거시 픽 생성", [sys.executable, "-u", "src/generate_picks.py"], False, 1800),
    ("LLM 해설 보강", [sys.executable, "-u", "src/generate_v2.py"], False, 1800),
    ("LLM 반영 조합", [sys.executable, "-u", "src/today_combo.py"], False, 1200),
]

# 실시간 점수 — 무거운 PUBLISH 와 분리한다. CSV 를 안 읽고 API 만 때리므로 가볍다.
LIVE = [sys.executable, "-u", "src/live_scores.py"]
LIVE_EVERY = 30            # 30초
LIVE_PORT = 8080
ANONYMOUS_BETS_PATH = Path(os.environ.get("ANONYMOUS_BETS_PATH", "/data/anonymous_bets.jsonl"))
_anonymous_bets_lock = threading.Lock()
# 전체 재계산과 일일 xG 수집은 각각 메모리를 크게 쓴다. 재시작 15분 뒤 두 작업이
# 정확히 겹쳐 OOM을 만들지 않도록 서로 한 번에 하나만 실행한다.
_pipeline_lock = threading.Lock()

PUSH_EVERY = 1800          # 30분마다 커밋·푸시
DAILY_EVERY = 86400
PUBLISH_EVERY = 1800       # 선발·최근 흐름을 반영한 전체 판정은 30분마다
HEAVY_EVERY_N = 12         # 무거운 단계는 12번에 한 번 (= 6시간)


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%m-%d %H:%M:%S}] {msg}", flush=True)


def _clear_stale_locks() -> None:
    """재시작으로 소유 프로세스가 사라진 수집·판정 잠금을 모두 제거한다."""
    raw = REPO / "data" / "raw"
    if not raw.exists():
        return
    for lock in raw.rglob("*.lock"):
        lock.unlink(missing_ok=True)
        log(f"남은 락 제거: {lock.relative_to(raw)}")


def sh(args: list[str], cwd: Path | None = None, check: bool = False):
    return subprocess.run(args, cwd=cwd, check=check,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def _mask(s: str) -> str:
    """토큰이 로그로 새 나가지 않게. git 은 실패 메시지에 URL 을 그대로 싣는다."""
    tok = os.environ.get("GITHUB_TOKEN", "")
    return (s or "").replace(tok, "***") if tok else (s or "")


def _auth_remote() -> str:
    """토큰을 URL 에 끼워 넣는다. 토큰 자체는 로그에 절대 찍지 않는다."""
    tok = os.environ.get("GITHUB_TOKEN", "")
    if not tok:
        return REMOTE
    return REMOTE.replace("https://", f"https://x-access-token:{tok}@")


def _configure(repo: Path) -> None:
    """커밋 신원과 토큰이 박힌 remote URL 을 붙인다.

    ⚠️ 클론할 때만 하면 안 된다. 볼륨에 이미 (토큰 없이) 클론된 레포가 남아
    있으면 재시작해도 설정이 안 붙어 push 가 인증 실패로 죽는다. 실제로 그랬다.
    → 부팅할 때마다 무조건 다시 붙인다. 토큰이 갱신돼도 이때 반영된다.
    """
    sh(["git", "config", "user.name", "proto-collector"], cwd=repo)
    sh(["git", "config", "user.email", "collector@users.noreply.github.com"], cwd=repo)
    sh(["git", "remote", "set-url", "origin", _auth_remote()], cwd=repo)
    # 운영 정본은 SQLite이고 Git 파일은 호환 export다. fetch 직전에 자동 GC가 뜨면
    # 1GB 머신에서 pack-objects가 부팅을 수분간 막아 HTTP가 완전히 내려간다.
    # 부팅/push 경로에서는 GC를 금지하고 별도 유지보수 창에서만 실행한다.
    sh(["git", "config", "gc.auto", "0"], cwd=repo)
    # 1gb 머신이라 pack-objects 가 88MB CSV 를 델타 압축하다 OOM(signal 9)으로 죽는다.
    # 실제로 복구 때 겪었고, 상한을 걸어야 통과한다.
    sh(["git", "config", "pack.threads", "1"], cwd=repo)
    sh(["git", "config", "pack.windowMemory", "24m"], cwd=repo)


def ensure_repo() -> bool:
    """볼륨에 레포가 없으면 클론, 있으면 최신화."""
    if (REPO / ".git").exists():
        log("레포 확인됨 — 최신 main과 수집 데이터 동기화")
        _configure(REPO)
        return _sync_existing_repo()

    log(f"레포 클론 → {REPO}")
    REPO.parent.mkdir(parents=True, exist_ok=True)
    r = sh(["git", "clone", "--depth", "50", _auth_remote(), str(REPO)])
    if r.returncode:
        log(f"클론 실패: {_mask(r.stderr)[:220]}")
        return False

    _configure(REPO)
    log("클론 완료")
    return True


TRACKED = ["data/", "docs/data/"]      # 수집 원본 + 사이트가 읽는 산출물

_push_fail_streak = 0                  # push 가 몇 주기째 연달아 실패하고 있나


def _free_mb() -> int:
    """볼륨 여유 공간(MB). 못 재면 -1."""
    try:
        return shutil.disk_usage(REPO.parent).free // (1024 * 1024)
    except OSError:
        return -1


def _snapshot_delta_paths(snapshot: str, base_ref: str,
                          cwd: Path | str | None = None):
    """공통 조상 이후 수집기 쪽에서 실제로 바뀐 데이터 경로만 찾는다.

    `data/` 전체를 snapshot에서 복원하면 원격 main에 새로 추가된 정적 설정까지
    삭제된다. 수집기가 만든 delta만 최신 코드 위에 다시 얹어야 한다.
    """
    repo = cwd or REPO
    base = sh(["git", "merge-base", snapshot, base_ref], cwd=repo)
    if base.returncode:
        return [], base
    changed = sh([
        "git", "diff", "--name-only", "-z", base.stdout.strip(), snapshot,
        "--", *TRACKED,
    ], cwd=repo)
    if changed.returncode:
        return [], changed
    return [path for path in changed.stdout.split("\0") if path], changed


def _restore_snapshot_delta(snapshot: str, paths: list[str],
                            cwd: Path | str):
    """최신 main 작업트리에 수집기 delta만 복원한다. 삭제도 그대로 재현한다."""
    for path in paths:
        exists = sh(["git", "cat-file", "-e", f"{snapshot}:{path}"], cwd=cwd)
        if exists.returncode == 0:
            restored = sh(["git", "checkout", snapshot, "--", path], cwd=cwd)
        else:
            restored = sh(["git", "rm", "-q", "--ignore-unmatch", "--", path], cwd=cwd)
        if restored.returncode:
            return restored
    return subprocess.CompletedProcess([], 0, "", "")


def _sync_existing_repo() -> bool:
    """시작 전에 최신 코드와 볼륨의 최신 수집 데이터를 충돌 없이 합친다.

    이 시점에는 아직 수집 스레드가 시작되지 않았으므로 tracked 데이터 체크포인트를
    만든 뒤 main을 옮겨도 안전하다. untracked 캐시·대용량 파생 파일은 건드리지 않는다.
    """
    # 이전 재시작이 충돌 중 끊겼어도 새 동기화를 시작할 수 있게 상태부터 정리한다.
    sh(["git", "rebase", "--abort"], cwd=REPO)
    sh(["git", "merge", "--abort"], cwd=REPO)
    staged = sh(["git", "add", "--all", "--", *TRACKED], cwd=REPO)
    if staged.returncode:
        log(f"시작 체크포인트 add 실패: {_mask(staged.stderr)[:160]}")
        return False
    changed = sh(["git", "diff", "--cached", "--quiet"], cwd=REPO)
    if changed.returncode:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        committed = sh(["git", "commit", "-m",
                        f"chore: 재시작 전 수집 데이터 체크포인트 ({stamp})"], cwd=REPO)
        if committed.returncode:
            log(f"시작 체크포인트 commit 실패: {_mask(committed.stderr)[:160]}")
            return False
    snapshot_result = sh(["git", "rev-parse", "HEAD"], cwd=REPO)
    if snapshot_result.returncode:
        return False
    snapshot = snapshot_result.stdout.strip()
    fetched = sh(["git", "fetch", "origin", "main"], cwd=REPO)
    if fetched.returncode:
        log(f"시작 fetch 실패: {_mask(fetched.stderr)[:160]}")
        return False
    delta_paths, delta_result = _snapshot_delta_paths(snapshot, "origin/main", REPO)
    if delta_result.returncode:
        log(f"시작 데이터 delta 계산 실패: {_mask(delta_result.stderr)[:160]}")
        return False
    switched = sh(["git", "checkout", "-B", "main", "origin/main"], cwd=REPO)
    if switched.returncode:
        log(f"시작 main 전환 실패: {_mask(switched.stderr)[:160]}")
        return False
    restored = _restore_snapshot_delta(snapshot, delta_paths, REPO)
    if restored.returncode:
        log(f"시작 데이터 복원 실패: {_mask(restored.stderr)[:160]}")
        return False
    sh(["git", "add", "--all", "--", *TRACKED], cwd=REPO)
    changed = sh(["git", "diff", "--cached", "--quiet"], cwd=REPO)
    if changed.returncode == 0:
        return True
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    committed = sh(["git", "commit", "-m",
                    f"chore: 재시작 수집 데이터 복구 ({stamp})"], cwd=REPO)
    if committed.returncode:
        return False
    pushed = _push_remote_main()
    if pushed.returncode:
        log(f"시작 데이터 push 실패: {_mask(pushed.stderr)[:160]}")
        return False
    log("최신 main 동기화와 수집 데이터 복구 완료")
    return True


def _publish_snapshot_on_remote(snapshot: str):
    """최신 원격 코드 위에 수집 데이터 스냅샷만 다시 얹어 push한다.

    PR 병합과 수집기 커밋이 엇갈리면 데이터 파일에서 rebase 충돌이 난다. 실행 중인
    수집 저장소를 reset 하면 동시에 쓰이는 새 스냅샷을 잃을 수 있으므로, 임시
    worktree에서 origin/main + 수집 데이터만 합친다.
    """
    parent = Path(REPO).parent
    target = Path(tempfile.mkdtemp(prefix="collector-publish-", dir=parent))
    shutil.rmtree(target)
    added = False
    try:
        delta_paths, delta_result = _snapshot_delta_paths(snapshot, "origin/main", REPO)
        if delta_result.returncode:
            return delta_result
        made = sh(["git", "worktree", "add", "--detach", str(target), "origin/main"],
                  cwd=REPO)
        if made.returncode:
            return made
        added = True
        # 원격 main에 새로 추가된 정적 자료는 그대로 두고 수집기에서 실제로
        # 변경된 경로만 복원한다.
        restored = _restore_snapshot_delta(snapshot, delta_paths, target)
        if restored.returncode:
            return restored
        staged = sh(["git", "add", "--all", "--", *TRACKED], cwd=target)
        if staged.returncode:
            return staged
        changed = sh(["git", "diff", "--cached", "--quiet"], cwd=target)
        if changed.returncode == 0:
            return subprocess.CompletedProcess([], 0, "", "")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        committed = sh(["git", "commit", "-m",
                        f"chore: 수집 데이터 충돌 복구 ({stamp})"], cwd=target)
        if committed.returncode:
            return committed
        return sh(["git", "push", "origin", "HEAD:main"], cwd=target)
    finally:
        if added:
            sh(["git", "worktree", "remove", "--force", str(target)], cwd=REPO)
        shutil.rmtree(target, ignore_errors=True)


def _push_remote_main():
    """원격 main에 push하고, rebase 충돌이면 데이터 스냅샷으로 안전하게 복구한다."""
    push = ["git", "push", "origin", "HEAD:main"]
    result = sh(push, cwd=REPO)
    if not result.returncode:
        return result

    snapshot_result = sh(["git", "rev-parse", "HEAD"], cwd=REPO)
    if snapshot_result.returncode:
        return snapshot_result
    snapshot = snapshot_result.stdout.strip()

    fetched = sh(["git", "fetch", "origin", "main"], cwd=REPO)
    if fetched.returncode:
        return fetched

    rebased = sh(["git", "rebase", "--autostash", "origin/main"], cwd=REPO)
    if rebased.returncode:
        sh(["git", "rebase", "--abort"], cwd=REPO)
        log("rebase 충돌 — 최신 main 위에 수집 데이터 스냅샷만 다시 게시한다")
        return _publish_snapshot_on_remote(snapshot)
    return sh(push, cwd=REPO)


def push_data() -> None:
    """수집 결과와 사이트 데이터만 커밋해 원격 main에 보낸다."""
    r = sh(["git", "status", "--porcelain", *TRACKED], cwd=REPO)
    if not r.stdout.strip():
        return
    n = len(r.stdout.strip().splitlines())
    sh(["git", "add", *TRACKED], cwd=REPO)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    committed = sh(["git", "commit", "-m", f"chore: 수집 데이터 자동 갱신 ({stamp})"], cwd=REPO)
    if committed.returncode:
        log(f"데이터 커밋 실패 — {_mask(committed.stderr).strip()[:200]}")
        return

    p = _push_remote_main()
    global _push_fail_streak
    if p.returncode:
        _push_fail_streak += 1
        log(f"push 실패({_push_fail_streak}회 연속) — 파일 {n}개 · "
            f"{_mask(p.stderr).strip()[:200]}")
        if _push_fail_streak >= 3:
            log(f"🔴 push 가 {_push_fail_streak}회 연속 실패 — "
                f"디스크 여유 {_free_mb()}MB")
    else:
        if _push_fail_streak:
            log(f"push 복구됨 ({_push_fail_streak}회 실패 후)")
        _push_fail_streak = 0
        log(f"push OK — 파일 {n}개")

    sh(["git", "reflog", "expire", "--expire=90.days", "--all"], cwd=REPO)
    # git gc는 서비스 부팅·수집과 분리한다. 여기서 실행하면 push 스레드가 아니라
    # 다음 재시작의 fetch까지 maintenance lock에 묶일 수 있다.
def run_looper(name: str, cmd: list[str], initial_delay: int = 0) -> None:
    """죽으면 다시 살린다. 즉시 재시작을 반복하지 않도록 뒤로 물러선다."""
    if initial_delay:
        log(f"{name} 시작을 {initial_delay}s 분산")
        time.sleep(initial_delay)
    backoff = 30
    while True:
        log(f"{name} 시작")
        try:
            rc = subprocess.run(cmd, cwd=REPO).returncode
        except Exception as e:                        # noqa: BLE001
            log(f"{name} 예외: {type(e).__name__}: {e}")
            rc = -1
        log(f"{name} 종료(rc={rc}) — {backoff}s 후 재시작")
        time.sleep(backoff)
        backoff = min(backoff * 2, 900)


def run_daily() -> None:
    # 재시작 직후 shared CPU를 화면 판정에 먼저 양보한다. 이전에는 xG 네 리그가
    # 첫 generate_v2와 겹쳐 판정 한 건을 만드는 데도 7분 넘게 걸렸다.
    time.sleep(900)
    while True:
        with _pipeline_lock:
            for i, (name, cmd) in enumerate(DAILY):
                if i:
                    # 리그를 연달아 긁으면 FootyStats 가 429 로 막는다(실제로 겪음).
                    # 하루 1회짜리라 서둘 이유가 없으니 사이를 넉넉히 둔다.
                    time.sleep(300)
                log(f"{name} 실행")
                r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
                tail = (r.stdout or "").strip().splitlines()[-1:] or ["(출력 없음)"]
                log(f"{name} 완료 — {tail[0][:120]}")
        time.sleep(DAILY_EVERY)


def _run_steps(steps: list) -> None:
    """단계 목록을 순서대로 실행한다. critical 이 실패하면 거기서 멈춘다."""
    for name, cmd, critical, tmo in steps:
        log(f"{name} 실행")
        try:
            r = subprocess.run(cmd, cwd=REPO, capture_output=True,
                               text=True, timeout=tmo)
            # ⚠️ 예전엔 마지막 한 줄만 찍었다. 그래서 생성기가 끝에 남기는 요약
            #    (예: LLM 덧씌우기의 호출·캐시적중·누적비용)이 그 뒤에 다른 print 가
            #    한 줄이라도 있으면 통째로 묻혔다 — 로그를 넣어 놓고 못 보고 있었다.
            #    돈이 걸린 정보는 안 보이면 없는 것과 같다. 꼬리 3줄을 남긴다.
            lines = (r.stdout or "").strip().splitlines()
            tail = lines[-3:] or ["(출력 없음)"]
            if r.returncode:
                err = (r.stderr or "").strip().splitlines()[-1:] or [""]
                # rc=-9 는 OOM 킬이다. 메시지가 비니 따로 짚어 준다.
                why = " (OOM 추정)" if r.returncode == -9 else ""
                log(f"{name} 실패(rc={r.returncode}){why} — {err[0][:140]}")
                if critical:
                    log("  필수 단계라 이번 주기 중단")
                    break
                continue
            log(f"{name} 완료 — {tail[-1][:120]}")
            for extra in tail[:-1]:
                if extra.strip():
                    log(f"    {extra[:120]}")
        except subprocess.TimeoutExpired:
            log(f"{name} 타임아웃({tmo}s)")
            if critical:
                break
        except Exception as e:                        # noqa: BLE001
            log(f"{name} 예외: {type(e).__name__}: {e}")
            if critical:
                break


def _run_publish_cycle(n: int) -> None:
    """한 번의 산출물 갱신 주기를 실행한다."""
    missing = not (REPO / "data" / "processed" / "games.csv").exists()
    heavy = (n % HEAVY_EVERY_N == 0) or missing
    if missing and n:
        log("games.csv 가 없다 — 무거운 단계를 앞당겨 실행한다")

    log(f"=== 산출물 갱신 시작 ({'전체' if heavy else '가벼운 단계만'})")
    # 재시작 첫 주기는 전체 재계산이어서 길게는 수십 분 걸린다. 기존
    # games.csv가 있으면 화면용 산출물을 먼저 갱신·게시해 stale 경고부터 푼다.
    if heavy and not missing:
        log("전체 재계산 전 화면 산출물을 먼저 갱신한다")
        _run_steps(PUBLISH_LIGHT)
        push_data()
    if heavy:
        _run_steps(PUBLISH)
    _run_steps(PUBLISH_LIGHT)
    push_data()
    _run_steps(PUBLISH_ENRICH)
    push_data()


def run_publish() -> None:
    """산출물 갱신.

    가벼운 단계는 30분마다, 무거운 단계는 12번에 한 번만 낀다.
    화면에서 자주 바뀌어야 하는 건 오늘의 픽·해설뿐이고 그 생성기들은
    발매 회차를 직접 긁으므로, 무거운 통계 재계산 없이도 새 값이 나온다.

    첫 실행은 수집기들이 엇갈려 뜬 뒤 1분만 기다린다. 발매 회차는 생성기가 원천에서
    직접 확인하고, 빈 응답이면 기존 게시본을 보존하므로 3분을 막아 둘 이유가 없다.
    """
    time.sleep(60)
    n = 0
    while True:
        try:
            with _pipeline_lock:
                _run_publish_cycle(n)
        except Exception as e:                         # noqa: BLE001
            log(f"publish push 예외: {type(e).__name__}: {e}")
        n += 1
        time.sleep(PUBLISH_EVERY)


def run_live() -> None:
    """30초마다 실시간 점수와 야구 상황을 갱신한다. 실패해도 다음 주기에 다시 한다."""
    while True:
        try:
            r = subprocess.run(LIVE, cwd=REPO, capture_output=True,
                               text=True, timeout=180)
            if r.returncode:
                err = (r.stderr or "").strip().splitlines()[-1:] or [""]
                log(f"실시간 점수 실패(rc={r.returncode}) — {err[0][:120]}")
            else:
                head = (r.stdout or "").strip().splitlines()[:1] or [""]
                log(f"실시간 점수 — {head[0][:110]}")
        except Exception as e:                        # noqa: BLE001
            log(f"실시간 점수 예외: {type(e).__name__}: {e}")
        time.sleep(LIVE_EVERY)


def validate_anonymous_bet(value: object) -> dict:
    """개인 식별자 없이 분석에 필요한 최소 베팅 통계만 허용한다."""
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("invalid schema")
    legs = value.get("legs")
    if not isinstance(legs, list) or not 1 <= len(legs) <= 20:
        raise ValueError("invalid legs")
    clean_legs = []
    for leg in legs:
        if not isinstance(leg, dict):
            raise ValueError("invalid leg")
        game_no = str(leg.get("game_no", ""))
        odds = float(leg.get("purchase_odds", 0))
        if not game_no.isdigit() or len(game_no) > 8 or not 1.0 <= odds <= 1000:
            raise ValueError("invalid game or odds")
        def short(key: str, limit: int) -> str:
            return str(leg.get(key, ""))[:limit]
        clean_legs.append({
            "game_no": game_no, "sport": short("sport", 12), "league": short("league", 40),
            "market": short("market", 30), "label": short("label", 30),
            "choice": short("choice", 30), "purchase_odds": round(odds, 3),
        })
    stake_band = str(value.get("stake_band", ""))
    allowed_bands = {"under_5000", "5000_9999", "10000_49999", "50000_99999", "100000_plus"}
    if stake_band not in allowed_bands:
        raise ValueError("invalid stake band")
    combined = value.get("combined_odds")
    combined = round(float(combined), 3) if combined is not None else None
    if combined is not None and not 1.0 <= combined <= 100000:
        raise ValueError("invalid combined odds")
    return {
        "schema_version": 1, "collected_day": datetime.now(timezone.utc).date().isoformat(),
        "source": "receipt_ocr", "round": int(value["round"]) if value.get("round") is not None else None,
        "combo_size": len(clean_legs), "combined_odds": combined,
        "stake_band": stake_band, "legs": clean_legs,
    }


def store_anonymous_bet(value: object, path: Path = ANONYMOUS_BETS_PATH) -> dict:
    clean = validate_anonymous_bet(value)
    line = json.dumps(clean, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _anonymous_bets_lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    return clean


def serve_live() -> None:
    """실시간 점수 JSON 하나만 내보내는 초소형 서버.

    git push(30분)로는 3분 주기 점수를 못 나른다. 그렇다고 3분마다 커밋하면
    하루 300커밋이라 레포가 망가진다. 그래서 이 파일만 직접 서빙한다.
    브라우저가 다른 도메인(사이트)에서 부르므로 CORS 를 열어 준다.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    live_path = REPO / "docs" / "data" / "live_scores.json"
    odds_path = REPO / "docs" / "data" / "live_odds.json"
    recommendation_path = REPO / "docs" / "data" / "today_combo.json"
    picks_path = REPO / "docs" / "data" / "picks_v2.json"

    class H(BaseHTTPRequestHandler):
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            # 30초마다 바뀌므로 브라우저·프록시 캐시를 짧게 유지한다
            self.send_header("Cache-Control", "public, max-age=5")

        def do_OPTIONS(self):                          # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):                              # noqa: N802
            if self.path.rstrip("/") in ("", "/health"):
                # ⚠️ "ok" 만 뱉으면 안 된다. 2026-08-06 에 볼륨이 꽉 차 수집이
                #    전부 멈췄는데도 이 엔드포인트는 39시간 내내 200 "ok" 였다.
                #    (기존 파일 덮어쓰기는 새 블록이 안 필요해 계속 성공했다.)
                #    "프로세스가 살아 있나"만 답하는 헬스체크는 이런 죽음을 못 잡는다.
                #    감시견(.github/workflows/watchdog.yml)이 판단할 수 있게 숫자를 낸다.
                try:
                    live_mtime = int(live_path.stat().st_mtime)
                except OSError:
                    live_mtime = 0
                def mtime(path: Path) -> int:
                    try:
                        return int(path.stat().st_mtime)
                    except OSError:
                        return 0

                def generated_at(path: Path) -> str | None:
                    try:
                        value = json.loads(path.read_text(encoding="utf-8")).get("generated_at")
                    except (OSError, json.JSONDecodeError, AttributeError):
                        return None
                    return str(value) if value else None

                body = json.dumps({
                    "status": "ok",
                    "disk_free_mb": _free_mb(),
                    "live_mtime": live_mtime,
                    "odds_mtime": mtime(odds_path),
                    "picks_mtime": mtime(picks_path),
                    "odds_generated_at": generated_at(odds_path),
                    "picks_generated_at": generated_at(picks_path),
                    "database_path": os.environ.get("PROODD_DB_PATH"),
                    "database_mtime": mtime(Path(os.environ.get(
                        "PROODD_DB_PATH", "/data/proodd.sqlite3"))),
                }).encode()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            # 점수·배당·판정·조합을 직접 서빙한다. git push(30분)와 Pages 배포를
            # 기다리게 하면 수집은 살아 있는데 화면만 낡는 시간이 생긴다.
            served = {
                "/live_scores.json": live_path,
                "/live_odds.json": odds_path,
                "/today_combo.json": recommendation_path,
                "/picks_v2.json": picks_path,
            }
            target = served.get(self.path.split("?")[0].rstrip("/"))
            if target is None:
                self.send_response(404)
                self._cors()
                self.end_headers()
                return
            try:
                body = target.read_bytes()
            except OSError:
                self.send_response(503)
                self._cors()
                self.end_headers()
                return
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):                             # noqa: N802
            if self.path.split("?")[0].rstrip("/") != "/anonymous-bets":
                self.send_response(404); self._cors(); self.end_headers(); return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 32768:
                    raise ValueError("invalid body size")
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                store_anonymous_bet(value)
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                body = b'{"ok":false,"error":"invalid_payload"}'
                self.send_response(400); self._cors()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            body = b'{"ok":true}'
            self.send_response(201); self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):                     # 접근 로그로 로그를 덮지 않는다
            pass

    try:
        ThreadingHTTPServer(("0.0.0.0", LIVE_PORT), H).serve_forever()
    except Exception as e:                             # noqa: BLE001
        log(f"실시간 서버 종료: {type(e).__name__}: {e}")


def run_push() -> None:
    # 첫 push 는 빨리 한다. 30분을 기다리면 그 사이 재시작이 겹칠 때
    # 볼륨에만 있던 수집분이 밖으로 못 나간다. 2분이면 수집기들이 한 바퀴 돈다.
    time.sleep(120)
    while True:
        try:
            push_data()
        except Exception as e:                        # noqa: BLE001
            log(f"push 예외: {type(e).__name__}: {e}")
        time.sleep(PUSH_EVERY)


def run_database_migration() -> None:
    """Import 267만 historical odds rows without blocking HTTP/collectors."""
    result = sh(DATABASE_MIGRATE, cwd=REPO)
    if result.returncode:
        log(f"과거 배당 DB 이관 실패: {(result.stderr or result.stdout)[-220:]}")
    else:
        log(f"과거 배당 DB 이관 완료 — {(result.stdout or '').strip()[-220:]}")


def main() -> int:
    log("=== 상시 수집 시작 ===")
    # 레포 동기화가 checkout을 교체해도 운영 원본 DB는 건드리지 못한다.
    os.environ.setdefault("PROODD_DB_PATH", "/data/proodd.sqlite3")
    if not os.environ.get("GITHUB_TOKEN"):
        log("⚠️ GITHUB_TOKEN 없음 — 수집은 되지만 결과가 레포에 안 올라간다")
    if not ensure_repo():
        log("레포 준비 실패 — 60초 후 재시도하도록 종료")
        return 1

    migration = sh(DATABASE_BOOTSTRAP, cwd=REPO)
    if migration.returncode:
        log(f"DB 이관 실패: {(migration.stderr or migration.stdout)[-220:]}")
        return 1
    log(f"DB 준비 완료 — {(migration.stdout or '').strip()[-220:]}")

    # 재시작은 수집을 중간에 죽이므로 락이 남는다. 그러면 xg_watch 가
    # "이미 수집 중" 으로 최대 1시간을 건너뛴다(실제로 겪음).
    # 여기까지 왔다는 건 머신이 방금 떴다는 뜻이고, 그러면 도는 수집기는 없다.
    _clear_stale_locks()

    # HTTP와 실시간 점수를 먼저 연다. LOOPERS는 각 5초씩 순차 기동하므로 서버를
    # 뒤에 두면 정상 부팅이어도 1분 넘게 503이 난다. DB artifact의 직전 값은 이미
    # 있으므로 서버는 즉시 응답하고 새 점수는 백그라운드에서 교체하면 된다.
    threading.Thread(target=serve_live, daemon=True).start()
    threading.Thread(target=run_live, daemon=True).start()

    for name, cmd in LOOPERS:
        threading.Thread(target=run_looper,
                         args=(name, cmd, LOOPER_START_DELAYS.get(name, 0)),
                         daemon=True).start()
        time.sleep(5)          # 동시에 몰려 나가지 않게 살짝 엇갈려 띄운다
    threading.Thread(target=run_daily, daemon=True).start()
    threading.Thread(target=run_publish, daemon=True).start()
    threading.Thread(target=run_push, daemon=True).start()
    threading.Thread(target=run_database_migration, daemon=True).start()
    while True:                # 메인 스레드는 살아만 있으면 된다
        time.sleep(3600)
        log("생존 신호")


if __name__ == "__main__":
    raise SystemExit(main())
