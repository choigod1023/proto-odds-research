# 상시 수집 서버 운영 (fly.io)

앱 `proto-odds-collector` · 리전 `nrt`(도쿄) · 머신 1대 · 볼륨 3GB

## 왜 서버로 옮겼나

수집기가 맥북에서 돌면 노트북을 덮는 순간 멈춘다. 이 프로젝트는 **경기 전 시점
값**이 생명인데(누수 방지), 지나간 배당은 다시 못 받는다. 놓친 시간대는 영구 손실이다.

**왜 GitHub Actions 가 아니라 fly.io 인가**
- Actions 러너는 **공유 IP** — 스크래핑 대상이 이미 차단했을 수 있다
- Actions cron 은 최소 5분이고 실제로 수십 분 밀린다 → 15분 배당 시계열이 뭉개진다
- 수집기가 이미 `--loop 초` 로 스스로 반복하므로 cron 자체가 불필요하다

## 무엇이 도는가

| 수집기 | 주기 | 대상 | 도쿄 IP 확인 |
|---|---|---|---|
| `snapshot.py` | 15분 | 와이즈토토 프로토 배당 | ✅ 88회차 273경기 |
| `info_watch.py` | 30분 | 네이버 KBO/MLB/NPB 선발 예고 | ✅ 212필드 |
| `player_info.py` | 30분 | MLB 공식 선발·ERA·부상·라인업 + KBO 최근 투수 지표 | ✅ 출처·갱신시각 포함 |
| `overseas_watch.py` | 15분 | BetExplorer 해외 배당 | ✅ 23건 |
| `xg_watch.py` | 하루 1회 | FootyStats K리그 xG | ✅ 팀 9 |

`deploy/supervisor.py` 가 반복 수집기를 자식 프로세스로 띄우고 죽으면 지수 백오프로
재시작한다. xg_watch 는 1회성이라 감독자가 주기를 잰다.
30분마다 `data/` 만 커밋해 GitHub 에 push → 레포에 남고 Vercel 이 자동 재배포.

## 코드는 이미지에 굽지 않는다

Dockerfile 은 `supervisor.py` 와 의존성만 담고, 실제 코드는 머신이 볼륨
(`/data/repo`)에 **git clone** 해서 돌린다. 그래서

- 코드 수정 → `git push` → `fly machine restart` 만으로 반영 (재배포 불필요)
- 데이터는 볼륨에 남아 재시작에도 생존

⚠️ 반대로 말하면 **push 하지 않은 로컬 커밋은 서버에 없다.** 실제로 이 함정에
한 번 빠졌다(새 `xg_watch.py` 가 서버에 없어 "출력 없음" 으로 끝남).

## 자주 쓰는 명령

로그 보기:
```bash
fly logs -a proto-odds-collector
```

코드 고친 뒤 반영 (push → 재시작):
```bash
cd ~/proto-odds-research && git push && fly machine restart -a proto-odds-collector --select
```

의존성이나 supervisor 를 고쳤을 때만 (레포 루트에서):
```bash
fly deploy -c deploy/fly.toml --ha=false
```

상태·비용 확인:
```bash
fly status -a proto-odds-collector
```

멈추기 / 되살리기:
```bash
fly machine stop -a proto-odds-collector --select
```

완전 삭제:
```bash
fly apps destroy proto-odds-collector
```

## GITHUB_TOKEN 설정 (필요)

토큰이 없으면 **수집은 되지만 결과가 레포에 안 올라간다.** 볼륨에만 쌓이므로
머신이 사라지면 같이 사라진다.

1. GitHub → Settings → Developer settings → Personal access tokens →
   **Fine-grained tokens** → Generate new token
2. Repository access: `choigod1023/proto-odds-research` 만 선택
3. Permissions → Repository permissions → **Contents: Read and write**
4. 생성된 토큰으로 아래 실행 (`<토큰>` 자리에 붙여넣기):

```bash
fly secrets set GITHUB_TOKEN=<토큰> -a proto-odds-collector
```

`fly secrets set` 은 자동으로 머신을 재시작한다. 이후 로그에서
`⚠️ GITHUB_TOKEN 없음` 경고가 사라지고 30분 뒤 첫 push 가 찍힌다.

## Gemini 해설 설정 (선택·무료 티어 가능)

경기 근거는 키 없이도 검증 템플릿으로 항상 표시된다. Google AI Studio에서 만든
Gemini API 키를 넣으면, 선발·최근 경기·날씨·공개 픽 사실은 그대로 둔 채 문장만
자연스럽게 편집한다. 기본 모델 `gemini-3.1-flash-lite`는
[공식 가격표](https://ai.google.dev/gemini-api/docs/pricing)상 무료 티어의 입력·출력이
무료이며, 무료 티어 입력은 Google 제품 개선에 사용될 수 있다.

```bash
fly secrets set GEMINI_API_KEY=<키> GEMINI_BILLING_TIER=free -a proto-odds-collector
```

한 주기 120회·하루 700회 상한과 문장 캐시가 있어 무료 요청 한도를 보호한다. 키가
없거나 한도 초과·API 실패·환각 검사 탈락이면 원문 템플릿으로 자동 복귀한다.

## 로컬 수집기는 끄고 쓴다

서버와 로컬이 동시에 같은 `data/` 를 쓰면 git 충돌이 난다.
이관 시점에 로컬 `snapshot.py`·`info_watch.py`·`overseas_watch.py` 는 종료했다.

## 비용

`shared-cpu-1x` 512MB 24시간 ≈ 월 $3.2, 볼륨 3GB ≈ 월 $0.45 → **월 $3.7 안팎**.

---

## 실시간 점수 (2026-07-31 추가)

`src/live_scores.py` 가 30초마다 네이버 스포츠 API 에서 KBO/MLB/NPB/K리그 점수를 받고, 진행 중인 야구는 현재 타자·투수·주자·카운트까지 받아
`docs/data/live_scores.json` 을 쓴다. 머신이 그 파일 하나를 직접 서빙한다:

```
https://proto-odds-collector.fly.dev/live_scores.json
```

**왜 git 이 아니라 HTTP 인가** — 산출물은 git push(30분)로 나르는데 점수는 3분마다
바뀐다. 3분마다 커밋하면 하루 300커밋으로 레포가 망가지고, 브라우저가 네이버 API 를
직접 부르는 건 CORS 로 막힌다. 그래서 이 파일만 예외로 뒀다.

⚠️ **키에 날짜를 반드시 넣을 것.** 팀 조합만으로 경기를 찾으면 MLB 3~4연전에서
어제/오늘 경기가 뭉개진다 — 정산 경기 55건 중 **37건이 어긋났었다.**
`live_scores.json` 의 `md`("07.31") 필드가 그래서 있다.

## 사이트 빌드 자동화

`.github/workflows/build-site.yml` — `web/**` 이 바뀐 push 에만 돌아 `docs/` 를 되커밋한다.
**데이터 갱신에는 돌지 않는다** — 앱이 JSON 을 런타임에 fetch 하므로 재빌드가 필요 없다.

⚠️ `npm run build` 의 prebuild 가 `docs/assets` 를 **먼저 지운다.** node_modules 없이
돌리면 번들이 사라진 채 실패한다. 로컬에서 빌드할 땐 `npm install` 을 먼저.

## 🕳 겪은 함정

- **메모리 512mb 는 부족했다.** PUBLISH 가 games.csv 25MB·bets.csv 30MB 를 pandas 로
  여러 번 읽는다. `Out of memory: Killed process` 로 7단계에서 죽었고, 당시 break
  로직 때문에 8·9단계까지 멈춰 사이트가 하루 넘게 낡았다 → 1gb 로 올리고,
  PUBLISH 단계에 critical 플래그를 둬 필수(데이터셋 재빌드) 외에는 continue 하게 했다.
- **`.claude/worktrees/` 를 커밋하면 안 된다.** gitlink(160000)로 잡혀
  `No url found for submodule path` 로 **GitHub Pages 배포가 통째로 실패한다.**
  .gitignore 에 넣어 뒀다.
