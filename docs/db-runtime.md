# DB 기준 운영과 요청 시 CSV 내보내기

`PROODD_DB_PATH`가 설정된 운영 환경에서는 DB가 유일한 데이터 원본이다.
CSV/JSON이 없거나 DB보다 오래돼도 파일로 대체하지 않는다. 설정이 없는 개발
환경에서만 기존 파일 fixture를 지원한다.

예측 수식은 Python이 실행하지만 입력 조회, 확정 결과, 계산 산출물, 사전 예측
원장은 SQLite에서 읽고 쓴다. 수식을 SQL로 옮기거나 검증되지 않은 모델을
승격시키는 변경은 아니다. 독립 연구/백테스트 스크립트는 요청해서 내보낸
파일로 실행할 수 있으며 운영 수집 주기에 포함되지 않는다.

## 저장 범위

- `documents`: 선수·선발·팀 매핑·상세 기록·모델·캐시 및 `archive:연도:회차` 원본 HTML.
- `event_records`: 선발 발표, 해외 배당, 날씨, xG, 공개 픽스터, 경기 근거와 익명 통계.
- `dataset_revisions` / `dataset_rows`: 경기행, 베팅 선택지, 경기장 기준정보와 계산된 피처.
- `match_results`: 확정된 일반 승패/승무패 스코어. 핸디캡 점수를 전적으로 쓰지 않는다.
  조회 시 회차 재발매 중복을 제거하고 더블헤더는 구분하며 충돌하는 스코어는 제외한다.
  결과 정정·무효화와 관측 시각도 보존한다. 과거 시점 조회에는 그때까지 관측된
  결과만 허용한다. 관측 시각이 없는 옛 CSV는 이관 시점부터 사용 가능하다고
  보수적으로 기록하며, 이관 전 시점의 완전한 재현을 주장하지 않는다.
- `prediction_records`: 기존의 변경 불가 사전 예측·정산 원장.
- `artifacts`: API가 제공하는 경기, 배당, 점수, 추천, 등급표.

경량 갱신기도 DB 전적을 붙인다. 이미 원장에 저장된 예측의 입력은 바꾸지 않고
새 전적은 `team_form_display`로 분리한다. 경기 시작 후 새 사전 예측을 만들지 않는다.
DB 이력 미연결, 팀명 매칭 실패, 해당 시즌 표본 없음은 다른 안내로 표시한다.

## 배포 및 기존 데이터 이관

저장소 PR 병합만으로 실행 중인 서버 코드나 기존 데이터가 이관되지는 않는다.
특히 `/app/supervisor.py`도 변경되므로 서버 이미지와 worker checkout을 함께
검증해야 한다. 다른 작업의 dirty checkout을 reset/stash하거나 전체 서버를
임의로 재시작하지 않는다. 깨끗한 release checkout과 조율된 전환이 필요하다.

1. SQLite 온라인 backup API 또는 검증된 볼륨 백업으로 복구 지점을 만든다.
   운영 중인 DB의 본체 파일만 복사하면 WAL 데이터가 빠질 수 있다.
2. `python src/migrate_runtime_db.py --inventory`로 파일 크기를 확인한다.
   DB, 인덱스, WAL, 백업, 임시 SQLite 데이터셋이 공존할 여유 공간이 필요하다.
   압축 HTML은 DB에서 더 커질 수 있으므로 gzip 크기만으로 용량을 판단하지 않는다.
3. 동일 DB 경로를 지정하고 `python src/migrate_runtime_db.py --all-sources`를 실행한다.
   기존 운영 DB가 있으면 낡은 CSV 데이터셋으로 덮어쓰지 않는다. 기존 gzip/CSV/JSON은
   삭제하지 않는다. 이관은 재실행 가능하다. 현재 등록되지 않은 연구 자료도
   `legacy/data/...` 이름으로 보관한다.
4. `python src/export_runtime.py --list`로 이관 목록을 확인한다. 파일 존재가 아니라
   DB 데이터셋 행 수, 확정 결과 이력, 예측 원장 수와 최근 시각을 확인한다.
5. 새 코드로 전환 후 `/api/picks`의 전적·예측 원장과
   `/api/today-recommendations`의 `source_generated_at`을 확인한다.

운영 수집기는 CSV/JSON/JSONL을 자동 내보내거나 데이터 커밋을 main에 push하지
않는다. 사이트는 DB API를 직접 읽는다. **Git 저장소는 DB 백업이 아니다.**
백업·보존 정책은 별도로 운영해야 한다. 기존 파일 삭제는 이관 검증 후 별도 작업이다.

## 필요할 때 CSV 생성

서버에서 같은 `PROODD_DB_PATH`를 사용하거나 `--db /절대경로/proodd.sqlite3`를 지정한다.

```sh
python src/export_runtime.py --list
python src/export_runtime.py --kind dataset --name processed_games --output exports/games.csv
python src/export_runtime.py --kind dataset --name processed_bets --output exports/bets.csv
python src/export_runtime.py --kind predictions --output exports/predictions.csv
python src/export_runtime.py --kind matches --output exports/confirmed-observations.csv
python src/export_runtime.py --kind odds --output exports/odds.csv
python src/export_runtime.py --kind events --name starter_announcements --output exports/starters.csv
python src/export_runtime.py --kind artifact --name picks_v2 --output exports/picks.csv
```

`matches`는 출처·회차별 확정 관측을 내보내므로 재발매 중복도 감사할 수 있다.
앱 계산용 실제 경기 조회는 `RuntimeDatabase.match_history()`에서 중복을 제거한다.
중첩 객체·배열은 CSV 셀 안 JSON으로 보존한다. Excel 수식으로 실행될 수 있는
문자열에는 안전 접두사를 붙인다. 내보내기는 DB 읽기 전용 스냅샷을 사용하며
기존 출력 파일은 거절한다. 의도한 덮어쓰기에만 `--overwrite`를 추가한다.

## 회귀 검증

```sh
PYTHONPATH=src python -m pytest -q
cd web
npm ci
npm test
npx vite build --outDir ../site-preview
```

핵심 검사는 CSV가 없는 DB에서 확정 결과 → 최근 전적 → 결정 생성 → DB 예측 원장
→ API 산출물까지 이어지는 경로, 실패한 데이터셋 교체의 전체 롤백,
과거 파일 재유입 금지, 시작 후 예측 소급 생성 금지, 명시적 CSV 내보내기이다.
