# 2026-10-31 수집기 응답 개선 배포 확인

2026-10-31 00:42~00:44 UTC(09:42~09:44 KST)에 읽기 전용으로 한 차례 확인했다. 운영 generator 실행, 부하 시험, 배포, 재시작, DB·cron 변경은 하지 않았다. 이 기록은 해당 시점의 관측이며 장기 안정성이나 개선 효과를 입증하지 않는다.

## 병합·이미지·웹 빌드

- [PR #443](https://github.com/choigod1023/proto-odds-research/pull/443)은 2026-10-30 10:10:26 UTC, [PR #446](https://github.com/choigod1023/proto-odds-research/pull/446)은 10:17:14 UTC에 각각 병합됐다. #446은 연구 통합 PR이다.
- Fly 상태: `proto-odds-collector`, machine `784e666f002328`, region nrt, v82 started, image `deployment-01M6613ES1R276GZECMASE5BEW`, 마지막 갱신 2026-10-30 09:33:00 UTC. Fly releases에서 v82 complete. 확인 시점에는 #443 병합 이전의 이미지다.
- `/app/supervisor.py` SHA256 `342929482325033e35ac4e8e85eee15e21d1abb17b3ac7baf1a76b7117ded7a5`. 병합된 #443 supervisor Git blob SHA256 `944e3dd40dbbf5036d79ecce8e789bc28a24ccc3a89a9fe9a1e4ec259ce0efbd`와 다르다. `/data/repo` HEAD `f9058fb98e2bfaa1fd8dede0f5f7ef70c8cdcd77`이며 `/data/repo/src/artifact_responses.py` SHA256 `8ba5c10b6c43459da9dac7c0b13f4f3da456c32f904e25b95446afa85dbbdcbc`는 그 HEAD의 같은 파일과 일치한다. #443 파일 해시 `1beaaaec5f8d36c27108b8e9f4eab936657957f3b45c64aab47fdbbaff748fcb`와는 다르다. 각각의 현재 파일에서 `If-None-Match`, `get_response` 문자열 검색도 일치 항목 없이 종료 1이었다. SSH CLI는 출력 후 Windows 핸들 오류를 반환한 경우가 있다. 실행 이미지 및 데이터 checkout의 파일 증거를 기준으로 #443 서버 ETag는 미반영으로 판단한다. 실제 조건부 picks 요청은 하지 않았고, 운영 응답 개선 효과는 미검증이다.
- #443 병합 SHA `f411df3d655f7accaa34140b0cacf12027ed1a25` 대상 [build-site 작업](https://github.com/choigod1023/proto-odds-research/actions/runs/41038790886)은 성공했고 빌드 및 산출물 되커밋 단계도 성공했다. 로그에 생성 커밋 `affd73b`이 나타난다. 해당 커밋의 [Pages 작업](https://github.com/choigod1023/proto-odds-research/actions/runs/41038909008), #446 SHA 대상 [Pages 작업](https://github.com/choigod1023/proto-odds-research/actions/runs/41039631504)도 성공이다. 이는 웹 빌드·배포 작업 기록이며 수집기 이미지 배포를 뜻하지 않는다. 실제 브라우저에서 서비스되는 자산의 내용과 304 동작은 확인하지 않았다.

## 단일 HTTP 관측

- 00:42:38 UTC [health](https://proto-odds-collector.fly.dev/health): HTTP 200, status `ok`, 전체 수신 0.225초. `live_stored_at=2026-10-31T00:42:13.557320+00:00`, `odds_stored_at=2026-10-31T00:40:44.315837+00:00`, `odds_generated_at=2026-10-31T00:40:33+00:00`, `picks_stored_at=2026-10-30T10:17:23.995637+00:00`, `picks_generated_at=2026-10-30T10:13:55+00:00`. health ok는 picks 최신성까지 보증하지 않는다.
- 00:42:38 UTC [최근 목록](https://proto-odds-collector.fly.dev/api/matches?scope=recent): HTTP 200, 전체 수신 0.501초, 3,302,537 bytes, `generated_at=2026-10-30T10:13:55+00:00`, `view.revision=2026-10-30T10:17:23.995637+00:00|kbo:2026-10-31T00:37:15+00:00`, `view.day=2026-10-31`. 기본 picks 생성은 조회 약 14시간 28분 43초 전이고 revision에는 더 최근 KBO 시각이 포함된다. revision 갱신만으로 picks 생성도 새로 됐다고 판단할 수 없다. 이 응답에 ETag는 없었으나 #443의 대상은 이 목록이 아니라 `/api/picks`다.
- 이 단일 목록 응답은 이전 전체 picks 측정 39.7MB·8.893초와 경로·시점·본문이 달라 개선 전후 비교가 아니다. 지연 원인, 개별 경기 영향 및 재생성 실패 여부는 이 표본만으로 확정할 수 없다.

## 로그 확인의 한계와 후속 확인

`fly logs --no-tail`은 2026-10-31 00:31:39~00:42:51 UTC 범위를 반환했다. 오류·대기 관련 키워드로 24개 줄을 찾았으나, 로컬 콘솔의 CP949 인코딩 예외로 해당 줄 내용을 출력하지 못했다. 따라서 원천별 실패나 원인을 확인하지 못했고 실패 없음으로도 판단하지 않는다. 운영 코드 변경을 승인하기 전에 해당 시각의 로그를 UTF-8 환경에서 확인하고, picks 생성·저장·게시 실패와 대기 상태를 구분할 필요가 있다. 이후 배포가 별도 승인되면 실제 이미지·정적 자산 버전, 조건부 요청의 200/304 및 본문 보존, 최신 생성 시각을 확인한다.
