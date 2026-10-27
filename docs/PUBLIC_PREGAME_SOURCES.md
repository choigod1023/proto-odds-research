# 외부 분석 근거 연결

운영 수집·예측과 분리된 로컬 opt-in 경로다. 배포 스케줄이나 추천 확률을 변경하지 않는다.
PR #349의 records 형식 및 main의 pregame_evidence_gate와 연결한다.
PR #350의 선수 정보 기록은 별도 경로이며 이 PR이 대신 활성화하지 않는다.

## 연결 범위

- Sports Mole: Team News에서 결장·출전 제한·예상 선발·휴식·로테이션 문장을 추출한다.
- SportsGambler: 명시적인 Team News / Injuries / Starting Pitchers / Lineups 섹션만
  같은 형식으로 처리한다. 실제 로컬 접근 실패는 실패로 기록하며 우회하지 않는다.
- 와이즈토토·OLBG·라이브스코어: 원천 목록에는 등록하되 자동 수집은 비활성화했다.
  무료 전환 시점, 게시판 접근, 원문 사용 조건 및 개별 DOM 검증 전에는 연결 완료가 아니다.
- 네이버 카페 로그인, 유료 글, 차단 페이지를 우회하지 않는다.
- robots.txt가 200이 아니거나 수집을 금지하면 기사에 접근하지 않는다.
  robots 허용이 재배포·학습 이용 허가라는 뜻은 아니다. 반복 운영 전 약관 확인이 필요하다.

## 실행

명시한 기사 URL 1~5개만 처리한다. 문서당 1MB, 30문장, 문장당 1,000자 제한;
요청 timeout 및 기사 간 2초 간격을 둔다. 실패 시 무한 재시도하지 않는다.
새 출력 파일만 만들며 기존 스냅샷은 덮어쓰지 않는다.

```powershell
python scripts/collect_public_pregame.py --manifest local-articles.json --output capture-new.json
```

manifest 예시(경기 매핑은 실제 공식 일정과 대조한 경우에만 작성):

```json
[{"url":"https://www.sportsmole.co.uk/football/LEAGUE/preview/ARTICLE.html"}]
```

각 항목의 선택적 `fixture` 필드: `event_id`, `sport`(sc/bs/bk/vl), `league`,
`home`, `away`, `kickoff_at`, `prediction_at`, `source_team_names`(원문 팀명 두 개),
`mapping_reviewed: true`. 시각에는 시간대를 포함한다. 경기 ID만 임의로 넣지 않는다.
본문 제목에 두 팀명이 모두 있어야 하지만 이것만으로 같은 날짜의 경기임이 증명되지는
않으므로 리뷰된 정확한 일정 매핑이 필수다. 연전·홈/원정·대회·시각은 검토자가 대조한다.

연결된 `records`는 #349의 `--claims` 입력으로 사용할 수 있다.
`minutes_limit`, `rest`는 기존 #349 검증기에서 unsupported_fact로 남는다.
기존 kind로 억지 변환하지 않는다. 독립적 공식 확인과 선수/경기별 수동 검토 후에만
특징 인코딩 대상으로 삼는다. 현재 도구에는 자동 확률 보너스나 자동 학습 승격이 없다.

## 누수 및 중복 방지

- 최종 픽·배당 추천 문장과 We say/Prediction 섹션은 제외한다.
- 추출값은 사실 확정이 아닌 publisher claim이다. 부정문·의심·예상은 원문 그대로
  보존하며 `verification.confirmed=false`, `usable_for_model=false`로 둔다.
- 게시·수정·실제 관측 시각을 분리한다. 과거 글을 오늘 읽어 과거 경기 전 관측으로
  소급하지 않는다. 관측 <= 예측 <= T-30을 검사한다.
- 게시 메타데이터가 없거나 미래거나 48시간 넘게 오래되면 검토 대상으로 남긴다.
  48시간은 운영상 보수적 기준이지 통계적으로 검증된 최적값이 아니다.
- 문서/문장 해시를 보존한다. 같은 문장 여러 사이트를 독립 확인으로 세지 않는다.
  유사 문장·번역·재전재 탐지는 아직 수동 검토가 필요하다.
- 출력의 ROI 차이는 null이다. 수집 성공은 예측 개선이나 모의 운용 완료가 아니다.
- 월 손실 5만원, 회당 1만원, 2폴의 기존 모의 운용 정책은 변경하지 않는다.

원천: https://www.sportsmole.co.uk/robots.txt,
https://www.sportsgambler.com/robots.txt, https://www.olbg.com/robots.txt
