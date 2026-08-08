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
    ("해외 배당", [sys.executable, "-u", "src/overseas_watch.py", "--loop", "900"]),
]

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
    ("데이터셋 재빌드", [sys.executable, "-u", "src/build_dataset.py"], True, 5400),
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
    ("가격분석 생성", [sys.executable, "-u", "src/generate_today.py"], False, 1800),
    ("픽 생성", [sys.executable, "-u", "src/generate_picks.py"], False, 1800),
    ("전마켓 픽 생성", [sys.executable, "-u", "src/generate_v2.py"], False, 1800),
    # ⚠️ today_combo 는 today.json·combo.json·loss_grades.json 을 읽는다.
    #    combo·loss_grades 는 무거운 쪽이라 최대 6시간 낡을 수 있지만,
    #    그건 과거 통계라 낡아도 값이 같다. 낡은 입력으로나마 도는 편이 낫다.
    ("오늘의 조합", [sys.executable, "-u", "src/today_combo.py"], False, 1200),
]

# 실시간 점수 — 무거운 PUBLISH 와 분리한다. CSV 를 안 읽고 API 만 때리므로 가볍다.
LIVE = [sys.executable, "-u", "src/live_scores.py"]
LIVE_EVERY = 180           # 3분
LIVE_PORT = 8080

PUSH_EVERY = 1800          # 30분마다 커밋·푸시
DAILY_EVERY = 86400
PUBLISH_EVERY = 3600       # 가벼운 산출물은 매시간
HEAVY_EVERY_N = 6          # 무거운 단계는 6번에 한 번 (= 6시간)


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%m-%d %H:%M:%S}] {msg}", flush=True)


def sh(args: list[str], cwd: Path | None = None, check: bool = False):
    return subprocess.run(args, cwd=cwd, check=check,
                          capture_output=True, text=True)


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
    # ⚠️ 2026-08-06: gc 가 한 번도 안 돌아 loose object 6,312개 = 2.67GiB 가 쌓여
    #    3GB 볼륨을 혼자 다 먹었다. 그때 packfile 은 421개에 겨우 2.53MiB 였다 —
    #    30분마다 찍는 CSV 스냅샷은 서로 거의 같아 델타 압축이 1000배로 듣는다.
    #    **압축만 하면 3GB 로 충분했다. 용량 문제가 아니었다.**
    #    git 기본 임계값 6,700 은 이 레포엔 너무 높아 6,312개에서 디스크가 먼저 터졌다.
    sh(["git", "config", "gc.auto", "500"], cwd=repo)
    # 백그라운드 gc 는 실패해도 로그에 안 남는다. 앞에서 돌게 한다.
    sh(["git", "config", "gc.autoDetach", "false"], cwd=repo)
    # 1gb 머신이라 pack-objects 가 88MB CSV 를 델타 압축하다 OOM(signal 9)으로 죽는다.
    # 실제로 복구 때 겪었고, 상한을 걸어야 통과한다.
    sh(["git", "config", "pack.threads", "1"], cwd=repo)
    sh(["git", "config", "pack.windowMemory", "24m"], cwd=repo)


def ensure_repo() -> bool:
    """볼륨에 레포가 없으면 클론, 있으면 최신화."""
    if (REPO / ".git").exists():
        log("레포 확인됨 — pull")
        _configure(REPO)
        r = sh(["git", "pull", "--rebase", "--autostash"], cwd=REPO)
        if r.returncode:
            log(f"pull 실패(계속 진행): {_mask(r.stderr)[:160]}")
        return True

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


def push_data() -> None:
    """수집 결과와 **사이트 산출물**만 커밋한다.

    소스 변경은 사람이 하는 것이므로 건드리지 않는다.
    ⚠️ `docs/data/` 를 빼먹으면 수집은 도는데 사이트는 안 바뀐다(2026-07-28 겪음).
    """
    r = sh(["git", "status", "--porcelain", *TRACKED], cwd=REPO)
    if not r.stdout.strip():
        return
    n = len(r.stdout.strip().splitlines())
    sh(["git", "add", *TRACKED], cwd=REPO)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    sh(["git", "commit", "-m", f"chore: 수집 데이터 자동 갱신 ({stamp})"], cwd=REPO)
    p = sh(["git", "push", "origin", "HEAD"], cwd=REPO)
    if p.returncode:
        # 원격이 앞서 있으면 rebase 후 재시도
        sh(["git", "pull", "--rebase", "--autostash"], cwd=REPO)
        p = sh(["git", "push", "origin", "HEAD"], cwd=REPO)
    global _push_fail_streak
    if p.returncode:
        _push_fail_streak += 1
        log(f"push 실패({_push_fail_streak}회 연속) — 파일 {n}개 · "
            f"{_mask(p.stderr).strip()[:200]}")
        # ⚠️ 예전엔 실패해도 이 한 줄이 전부였다. 그래서 2026-08-06 부터 39시간을
        #    매 주기 실패하는 동안 아무도 몰랐다. 연속 실패는 다른 사건이다 —
        #    일시적 네트워크가 아니라 디스크·토큰·rebase 충돌 중 하나다.
        if _push_fail_streak >= 3:
            log(f"🔴 push 가 {_push_fail_streak}회 연속 실패 — "
                f"디스크 여유 {_free_mb()}MB")
    else:
        if _push_fail_streak:
            log(f"push 복구됨 ({_push_fail_streak}회 실패 후)")
        _push_fail_streak = 0
        log(f"push OK — 파일 {n}개")

    # 압축은 push 성공 여부와 무관하게 한다. push 가 실패해 커밋이 쌓일 때가
    # 오히려 디스크가 제일 빨리 차는 상황이다.
    sh(["git", "reflog", "expire", "--expire=90.days", "--all"], cwd=REPO)
    sh(["git", "gc", "--auto"], cwd=REPO)


def run_looper(name: str, cmd: list[str]) -> None:
    """죽으면 다시 살린다. 즉시 재시작을 반복하지 않도록 뒤로 물러선다."""
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
    while True:
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
            tail = (r.stdout or "").strip().splitlines()[-1:] or ["(출력 없음)"]
            if r.returncode:
                err = (r.stderr or "").strip().splitlines()[-1:] or [""]
                # rc=-9 는 OOM 킬이다. 메시지가 비니 따로 짚어 준다.
                why = " (OOM 추정)" if r.returncode == -9 else ""
                log(f"{name} 실패(rc={r.returncode}){why} — {err[0][:140]}")
                if critical:
                    log("  필수 단계라 이번 주기 중단")
                    break
                continue
            log(f"{name} 완료 — {tail[0][:120]}")
        except subprocess.TimeoutExpired:
            log(f"{name} 타임아웃({tmo}s)")
            if critical:
                break
        except Exception as e:                        # noqa: BLE001
            log(f"{name} 예외: {type(e).__name__}: {e}")
            if critical:
                break


def run_publish() -> None:
    """산출물 갱신.

    가벼운 단계는 매시간, 무거운 단계는 6번에 한 번만 낀다.
    화면에서 자주 바뀌어야 하는 건 오늘의 픽·해설뿐이고 그 생성기들은
    발매 회차를 직접 긁으므로, 무거운 통계 재계산 없이도 새 값이 나온다.

    첫 실행을 3분 뒤로 둔 이유: 부팅 직후엔 수집기가 아직 한 바퀴를 안 돌아
    발매 회차 캐시가 비어 있을 수 있다(생성기가 '발매중 []' 로 끝난다).
    """
    time.sleep(180)
    n = 0
    while True:
        # ⚠️ 가벼운 단계는 games.csv 를 읽는다. 그 파일이 없으면(첫 부팅,
        #    또는 디스크 정리로 지워진 뒤) 무거운 쪽을 먼저 돌려야 한다.
        #    안 그러면 매시간 조용히 실패만 반복한다.
        missing = not (REPO / "data" / "processed" / "games.csv").exists()
        heavy = (n % HEAVY_EVERY_N == 0) or missing
        if missing and n:
            log("games.csv 가 없다 — 무거운 단계를 앞당겨 실행한다")

        log(f"=== 산출물 갱신 시작 ({'전체' if heavy else '가벼운 단계만'})")
        if heavy:
            _run_steps(PUBLISH)
        _run_steps(PUBLISH_LIGHT)

        try:
            push_data()                                # 만든 즉시 내보낸다
        except Exception as e:                         # noqa: BLE001
            log(f"publish push 예외: {type(e).__name__}: {e}")
        n += 1
        time.sleep(PUBLISH_EVERY)


def run_live() -> None:
    """3분마다 실시간 점수를 갱신한다. 실패해도 다음 주기에 다시 한다."""
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


def serve_live() -> None:
    """실시간 점수 JSON 하나만 내보내는 초소형 서버.

    git push(30분)로는 3분 주기 점수를 못 나른다. 그렇다고 3분마다 커밋하면
    하루 300커밋이라 레포가 망가진다. 그래서 이 파일만 직접 서빙한다.
    브라우저가 다른 도메인(사이트)에서 부르므로 CORS 를 열어 준다.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    live_path = REPO / "docs" / "data" / "live_scores.json"

    class H(BaseHTTPRequestHandler):
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            # 3분마다 바뀌므로 캐시를 길게 두면 실시간이 아니게 된다
            self.send_header("Cache-Control", "public, max-age=60")

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
                body = json.dumps({
                    "status": "ok",
                    "disk_free_mb": _free_mb(),
                    "live_mtime": live_mtime,
                }).encode()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if not self.path.startswith("/live_scores.json"):
                self.send_response(404)
                self._cors()
                self.end_headers()
                return
            try:
                body = live_path.read_bytes()
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


def main() -> int:
    log("=== 상시 수집 시작 ===")
    if not os.environ.get("GITHUB_TOKEN"):
        log("⚠️ GITHUB_TOKEN 없음 — 수집은 되지만 결과가 레포에 안 올라간다")
    if not ensure_repo():
        log("레포 준비 실패 — 60초 후 재시도하도록 종료")
        return 1

    # 재시작은 수집을 중간에 죽이므로 락이 남는다. 그러면 xg_watch 가
    # "이미 수집 중" 으로 최대 1시간을 건너뛴다(실제로 겪음).
    # 여기까지 왔다는 건 머신이 방금 떴다는 뜻이고, 그러면 도는 수집기는 없다.
    for lock in (REPO / "data" / "raw").glob("*.lock"):
        lock.unlink(missing_ok=True)
        log(f"남은 락 제거: {lock.name}")

    for name, cmd in LOOPERS:
        threading.Thread(target=run_looper, args=(name, cmd), daemon=True).start()
        time.sleep(5)          # 동시에 몰려 나가지 않게 살짝 엇갈려 띄운다
    threading.Thread(target=run_daily, daemon=True).start()
    threading.Thread(target=run_publish, daemon=True).start()
    threading.Thread(target=run_push, daemon=True).start()
    # 실시간 점수는 즉시 시작한다 — 사이트가 제일 먼저 필요로 하는 값이다
    threading.Thread(target=run_live, daemon=True).start()
    threading.Thread(target=serve_live, daemon=True).start()

    while True:                # 메인 스레드는 살아만 있으면 된다
        time.sleep(3600)
        log("생존 신호")


if __name__ == "__main__":
    raise SystemExit(main())
