# 공개 픽스터 추가 연결: 2026-10-30

신규 원문 11건에서 투표 11행을 추출해 **이미 가격과 정산 자료가 있는 동결 경기 11건**에 연결했다. KBO 3건, NPB 4건, K리그1 2건, K리그2 2건이다. 신규 출처는 고리의 사이트 내 작성자 `호나우두` 한 명이다. 기존 입력과 출처·작성자가 다른 후보를 추가한 결과이며, 실제로 독립적인 사람의 의견이라고 인증한 결과는 아니다. ROI·결과 라벨을 조회하거나 모델을 학습하지 않았다.

## 부모 작업에 전달하는 계약

- 브랜치: `codex/rd-tipster-independent-20261030`. 부모의 PR424 의존 커밋 `8bb66e8bccd4ac68fe46a2b59e75147a84de9679` 위에서 작업했다. 개별 push·PR·merge·배포는 하지 않는다.
- 부모 프로토콜은 수정하지 않았다. 수집 전 동결한 `experiments/source-link-tipster-20261030/dedup-rules.json`에 부모 파일 SHA256과 동결 시각이 있다.
- 운영 코드·스케줄러·DB를 변경하지 않았다. 기존 `pickster_watch`, `public_tipster_votes`, `capture_tipster_forecasts`를 읽고 연구용 어댑터만 추가했다.
- `vote-schema.json`이 공통 스키마다. `selection`은 `home/draw/away`, `market`은 야구 등 2방향 경기의 `full_game_moneyline`, 축구의 `승무패`다. 기존 market 값은 동결된 주시장 방향 스키마에서 옮겼다는 근거를 별도 필드에 남겼다. 원문에서 직접 시장 이름을 재확인한 것으로 표현하지 않는다.
- `old-votes-normalized.jsonl.gz`와 `new-votes.jsonl.gz`를 같은 스키마로 제공한다. 누락된 원래 작성자·시각·해시를 새로 만들거나 다른 값으로 대체하지 않는다. 원래 두 해시 필드는 `original_document_sha256`, `original_source_sha256`에도 보존한다.

## 기존 투표 재구성 및 벡터 감사

원 입력 `two-leg-model-literature-20261030/experiments/two-leg-literature-20261030/inputs/votes.jsonl.gz`에는 597경기·635개 `raw_forecasts`가 있다. 숫자 `voters`를 작성자로 사용하지 않았다. 부모의 정확한 `retrospective_*` 사건 ID와 리그·팀·킥오프가 일치하는 **538경기·560투표**만 기존 투표로 내보냈다. 범위 밖 59경기·75투표는 이 파일에 포함하지 않는다.

중복 제거 전·후는 각각 560행이며, 기존 투표의 중복 제거 건수는 0이다. 기존 투표의 방향 비율을 다시 계산했을 때 동결된 `external_p`와 정확히 일치하는 경기는 534건이다. 다음 네 경기는 원문 투표가 1:1이지만 동결된 벡터는 한쪽 100%다.

- `20260401HTLG0`: 원문 `[0.5, 0.5]`, 동결 `[1.0, 0.0]`.
- `20260401OBSS0`: 원문 `[0.5, 0.5]`, 동결 `[1.0, 0.0]`.
- `20260401LTNC0`: 원문 `[0.5, 0.5]`, 동결 `[1.0, 0.0]`.
- `20260401KTHH0`: 원문 `[0.5, 0.5]`, 동결 `[0.0, 1.0]`.

`old-vector-audit.jsonl.gz`는 전체 538건의 원 벡터와 재구성 벡터를 보존한다. 불일치 4경기의 8투표에는 `eligible_for_union=false`를 붙였다. `old-votes-union-eligible.jsonl.gz`에는 벡터가 일치하는 534경기의 552투표가 있다. **이 flag는 기존 벡터 일치 여부만 뜻한다. 시간·해시 품질을 통과했다는 뜻이 아니다.**

기존 자료에는 게시 시각 주장이 없는 193행과 파싱할 수 없는 원 게시 시각 5행이 있다. 문서 해시가 없는 행은 19개다. 이 누락은 진단 tier와 `metadata_missing_reasons`에 보존했다. `retrospective_claimed_pregame`인 기존 투표는 362행이다. 부모는 시간·해시 기준을 추가로 적용해야 하며, 그 과정에서 표본이 달라지면 기존 벡터를 다시 확인해야 한다. 방향 비율을 보정된 확률이라고 부르지 않는다.

## 실제 수집과 연결

실제 기사 관측 시각은 **2026-10-30 05:26:14.542~05:29:41.741 UTC**다. 11건 모두 `retrospective_claimed_pregame`이며, 지금 수집한 문서가 과거 경기 전에 실제로 수신됐다는 증거는 없다. `published_at_claim`은 원문 날짜를 한국 시각으로 해석한 주장이고 `observed_at`과 별개다. 경기 시작 시각은 동결 경기에서 가져왔으며 원문에 없는 킥오프를 추측하지 않았다.

연결에는 리그, 명시된 두 팀, 게시 연도를 포함한 경기 날짜를 사용했다. 동일 날짜·동일 팀으로 두 경기가 잡히면 시간 증거 없이 선택하지 않는다. 점수는 연결에 사용하지 않았다. 수집한 11문서의 연결 실패·모호한 경기·신규 중복은 각각 0건이다. 전방 수집이 검증된 투표와 미연결 전방 후보는 이번 수집에서 0건이다. 이 수치는 검색된 모든 글의 전수 연결률이 아니다. 동결 경기와 맞는 제목을 우선 수집한 결과다.

야구는 `예상 승리팀`이라는 명시적 표기를 읽었다. `승1패`, 핸디캡, 언더오버 및 분석 문장의 분위기는 방향 추출에 사용하지 않았다. 축구는 `베팅 요약 및 추천`의 명시적 승무패 또는 첫 줄 팀 승패만 읽고 이후 전반전 섹션을 제외했다.

실제 원문 URL:

- KBO: [LG–키움, 9월 8일](https://www.goli.tv/board/analysis/294079), [삼성–KIA, 9월 8일](https://www.goli.tv/board/analysis/294080), [NC–롯데, 9월 8일](https://www.goli.tv/board/analysis/294083).
- K리그1: [포항–서울, 9월 20일](https://www.goli.tv/board/analysis/295095), [인천–대전, 9월 20일](https://www.goli.tv/board/analysis/295094).
- K리그2: [성남–화성, 9월 20일](https://www.goli.tv/board/analysis/295097), [안산–충북청주, 9월 20일](https://www.goli.tv/board/analysis/295089).
- NPB: [지바롯데–라쿠텐, 9월 16일](https://www.goli.tv/board/analysis/294761), [라쿠텐–니혼햄, 9월 5일](https://www.goli.tv/board/analysis/293852), [지바롯데–세이부, 9월 1일](https://www.goli.tv/board/analysis/293465), [세이부–니혼햄, 8월 26일](https://www.goli.tv/board/analysis/292894).

기존과 신규를 합친 중복 감사 대상은 571행이며, 동일 문서 해시·동일 기사 본문 해시·사이트 내 작성자 기준으로 제거된 행은 0개다. 벡터 불일치 경기만 제외하면 563행이다. 기존 기사 본문을 이번에 다시 수집하지 않았으므로, 원문 해시 표현이 다른 기존·신규 문서 사이의 복제 여부를 본문으로 전부 검사한 것은 아니다. 작성자 이름이 같다는 이유로 다른 사이트 계정을 합치지 않으며, 사이트가 다르다는 이유만으로 독립성도 확정하지 않는다. 부모가 old/union 방향과 고정 9:1을 평가한다.

## 접근 실패와 해시의 한계

Python 직접 HTTP 및 자격 증명이 없는 설정 프록시 요청은 연결 오류로 끝났다. 고리는 브라우저에서 공개 글을 읽을 수 있었다. [아톰티비](https://www.attv24.com/sports_analysis)는 브라우저에서도 연결 리셋이 났고, [사용자 지정 SPA](https://xn--950bo4em5v.co/#/community/analysis)와 [Named](https://named.com/)도 추가 원문을 확보하지 못했다. [라이브스코어](https://www.livescore.co.kr/) 루트는 읽혔지만 확인한 링크에 프리미엄 분석이 있어 진행하지 않았다. 로그인·결제·인증 우회는 하지 않았다. 검색의 `9월8일`은 오래된 연도만 반환했고, 실제 2026년 제목의 `09월08일` 표기로 수정한 과정도 기록했다.

새 기사에 저장한 것은 선택한 기사 DOM의 UTF-8 바이트다. `document_hash_kind=article_dom_utf8`이며 `http_response_sha256=null`이다. **DOM 해시를 서버 HTTP 응답 원문 해시로 표시하지 않는다.** 원문 서버 응답 헤더·수정 시각·과거 수신 시각은 확보하지 못했다. `collection-method.json`에 정확한 선택자와 직렬화·해시 방식을 기록했다.

외부 로컬 캐시는 `C:/Users/user/AppData/Local/Temp/rd-tipster-independent-20261030`다. 기사 DOM·실패 응답·원 관측 로그를 이 디렉터리에 보존하며 Git에는 넣지 않는다. `document-inventory.jsonl.gz`에는 URL·실제 관측 시각·게시 시각 주장·해시·연결 상태만 넣었다.

수집을 종료했다. 명시적으로 실행한 페이지·검색 요청은 추정 46회, 브라우저 정책에 막혀 네트워크에 나가지 않은 탐색까지 포함하면 50회 이하다. `PublicCache`는 영속 90회 상한, 연결/읽기 timeout, 응답 크기 상한, 요청 중 재시도·리다이렉트 자동 추적 금지를 구현한다. **브라우저의 이미지·스크립트 등 부속 요청 수는 측정하지 못해 전체 wire HTTP 요청이 90회 이하임을 보증할 수 없다.** 상세 회계와 이 한계는 `public-request-audit.json`에 있다.

## 재현과 검증

전용 작업 트리에서 실행:

```powershell
python scripts/rd_tipster_source_links.py --cache-dir C:/Users/user/AppData/Local/Temp/rd-tipster-independent-20261030 --build --old-input C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/two-leg-model-literature-20261030/experiments/two-leg-literature-20261030/inputs/votes.jsonl.gz --old-document-metadata C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/experiments/tipster-year-20261028/korean/documents.json --old-document-metadata C:/Users/user/Documents/ChatGPT/sportstoto-worktrees/tipster-year-validation-20261028/experiments/tipster-year-20261028/overseas/documents.json
python -m pytest tests/test_rd_tipster_source_links.py tests/test_public_tipster_votes.py -q
```

결과: **73 passed**. 실제 캐시에서 신규 11행을 파싱·연결했고, 기존 560행을 다시 집계해 534개 벡터 일치와 4개 불일치를 확인했다. 파서, 사이트별 작성자 구분, 복제 본문, 중복 문서, 작성자 의견 충돌, 전반전 혼입, 누락 연도·작성자·해시, 실제 관측/게시 주장 구분, 더블헤더 모호성, 엄격한 킥오프 비교, 자격 증명 거부, 영속 요청 예산과 캐시 재사용을 검사했다. 전방 HTTP 수집 어댑터의 캐시 테스트에는 합성 응답만 사용했으며 실제 전방 수집 성공으로 세지 않았다.

결과 파일과 입력의 SHA256은 `manifest.json`에 있다. `*-raw.jsonl.gz`는 중복 제거 전 행, `*-dedup-audit.jsonl.gz`는 행별 판단을 보존한다. 원문 전체나 이미지, 기사 분석 문장은 커밋하지 않는다. 연구 월 손실 제한 OFF 요청을 바꾸지 않았고, 이번 하위 작업은 어떤 수익 평가도 수행하지 않았다.

## 후속 감사: canonical URL·본문·작성자 별칭

사용자의 추가 지시에 따라 ROI 확인 전에 `dedup-amendment.json`으로 동일 문서 판정 규칙을 보강했다. 원래 동결 규칙과 부모 프로토콜은 그대로 보존했다. **추가 네트워크 요청은 0회다.**

고리 URL의 `http/https`, `www` 유무, 마지막 슬래시, `?page=1/2` 및 fragment 차이를 없애고 `https://www.goli.tv/board/analysis/ID`로 비교했다. 기존 동결 560투표뿐 아니라 범위 밖 자료를 포함한 원 입력 **635투표·403개 canonical URL**과 대조했다. 신규 11개 기사 ID는 모두 이 집합에 없었고, 신규 문서 해시와 원 입력의 `document_sha256` 또는 `source_sha256`이 같은 사례도 0건이다. 따라서 확인 가능한 원 입력 기준으로 **기존 기사 재수집은 0건, 새 문서 투표 기록은 11건**이다. 새로 검증된 독립적 사람의 의견이 11개라는 뜻은 아니다.

동일 경기의 기존 출처는 KBO·K리그의 `golfmasterj.tistory.com / 마스터제이`, NPB의 `www.tonyspicks.com / Ron Crawford`다. 기존 수집의 로컬 `korean/documents.json`(725문서)과 `overseas/documents.json`(679문서)에 있는 `units/nodes`를 읽어 11쌍의 본문을 비교했다. 유니코드·공백을 정규화한 전체 추출 텍스트 해시 일치 0쌍, 80자 이상 문단의 정확한 일치 0건이다. 5단어 구간의 일치 건수도 원문 없이 해시·건수로 기록했다. 이 자료는 원 HTTP HTML 전체가 아니므로 일부 복제·번역·구간 누락을 배제하는 검증으로 해석하면 안 된다.

새 작성자 `호나우두`와 기존 작성자 사이의 공개 계정 ID·별칭 연결 근거는 입력에서 확인하지 못했다. 사이트가 다르거나 표시명이 다르다는 이유로 독립성을 확정하지 않는다. `canonical-novelty-summary.json`의 `verified_independent_human_opinions=0`은 독립성이 검증되지 않았다는 뜻이며, 복제라고 판정했다는 뜻도 아니다.

`deduplicate`는 canonical URL 또는 문서/본문 해시로 연결되는 행을 전이적으로 묶으며, 같은 사이트 작성자는 추가로 한 표만 허용한다. **새 행을 먼저 제거해 연결 고리를 잃지 않도록 기존·신규의 중복 제거 전 원 행 전체로 합집합을 구성한다.** 연결된 성분에서 방향이 충돌하면 모든 행을 제외한다. 같은 URL인데 작성자 표시명이나 래퍼 해시가 달라진 경우도 예외가 아니다. 원래 기존 비교군은 별도로 보존하며, 합집합에서의 기권을 이유로 기존 비교군의 투표나 스테이크를 소급 삭제하지 않는다.

현재 실자료에서는 canonical URL 중복, 해시 중복, 같은 URL의 방향 충돌, 전이적 중복이 모두 0건으로 기존 560행+신규 11행=571행이 유지된다. `new-votes.jsonl.gz`는 이 전체 합집합에서 살아남은 신규 행이며, 전체 합집합은 `union-votes-normalized.jsonl.gz`에 있다. 원 행은 `old-votes-raw.jsonl.gz`, `new-votes-raw.jsonl.gz`에 그대로 남아 있다. 부모가 중복 판정을 다시 수행할 때도 이 원 행을 사용해야 중간 연결 고리를 보존할 수 있다. 실제 후기 수집의 11건 모두 회고 주장 tier인 점과 기존 벡터 불일치·시각/해시 누락은 바뀌지 않았다.

행별 근거는 `canonical-novelty-audit.jsonl.gz`, 요약은 `canonical-novelty-summary.json`, 전체 연결 성분과 충돌 결정은 `union-dedup-audit.jsonl.gz`에 있다. 새 테스트는 쿼리·호스트·래퍼 변경, 같은 URL의 방향 충돌, URL→해시의 전이적 연결, 제거될 행이 연결 고리인 통합 시나리오, 하나의 문서가 여러 경기를 다루는 경우를 검증한다.
