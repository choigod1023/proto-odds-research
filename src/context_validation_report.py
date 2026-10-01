"""Archive experiment inputs/results in a NEW private SQLite and export Markdown."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def percent(v):
    return '산출 불가' if v is None else f'{v*100:.2f}%'


def render(controls, soccer, mlb):
    lines = ['# 야구·축구 실험 결과 — 2026-09-09', '',
        '## 결론', '',
        '이번 실행에서 운영 가중치를 올릴 근거는 확보하지 못했다. 기존 득실점·변동성 대조 실험과 새 데이터 수집 검증을 구분한다. 운영 코드·추천 확률·DB·서비스는 변경하지 않았다.', '',
        '## 기존 기록 대조 실험', '',
        '2023년 이후 경기 아카이브에서 10개 리그를 각각 독립 처리했다. 최근 최대 20경기(최소 5경기)의 득점·실점 평균, 분산, 승률을 사용했다. 이는 이미 검토했던 팀 기록 접근의 재검증이며 새로운 xG·투수 모델이 아니다.', '',
        '리그별 날짜의 앞 60%는 학습, 다음 20%는 조정 가중치 선택, 마지막 20%는 평가로 나누었다. 경계마다 7일을 비우고, 경기 결과는 킥오프 24시간 이후에만 과거 피처에 사용할 수 있다고 가정했다. 이 가정은 실제 결과 수신시각을 대신하는 연구용 복원이다.', '',
        'Shin 시장 확률에 L2=32 다항 로지스틱 보정을 추가했다. 내부 검증에서 0/0.1/0.25/0.5/0.75/1 중 날짜별 오차의 표준오차 규칙으로 보수적인 혼합 가중치를 선택했다. 이전 실험에서 재사용한 자료이므로 독립적인 새 검증자료가 아니다.', '']
    for league, r in controls['leagues'].items():
        if r['status'] != 'evaluated':
            lines.append(f'- {league}: {r["status"]}.')
            continue
        m, s = r['market'], r['selected']
        a, b = r['market_policy'], r['selected_policy']
        lines.append(f'- **{league}**: 평가 {r["test_start"][:10]}~{r["test_end"][:10]}, {m["n"]}경기. '
                     f'전체 승패/승무패 적중률 {percent(m["hit_rate"])} → {percent(s["hit_rate"])}. '
                     f'내부 선택 가중치 {r["alpha"]}. 추천 부분집합 {a["hits"]}/{a["n"]} '
                     f'({percent(a["hit_rate"])}) → {b["hits"]}/{b["n"]} ({percent(b["hit_rate"])}).')
    lines += ['', '가중치 0은 해당 리그의 과거 결과를 보지 않았다는 뜻이 아니라, 내부 검증에서 시장에 추가할 보정이 선택되지 않았다는 뜻이다. 이때 평가 결과와 신뢰구간이 시장과 완전히 같은 것은 동일한 예측을 비교한 결과이며, 모든 가능한 피처가 무효임을 증명하지 않는다.', '',
        '### 추천 조건까지 적용한 비교', '',
        '실제 프론트의 finalRecommendedSelection과 dailyHighlightedSelections를 호출했다. 동일 마켓 최유력, 배당 2.2 미만, 1.5 이상 우선/없을 때 저배당 보조, 55% 이상 기본 리그·날짜별 3개 및 60% 이상 추가 조건을 따른다. 입력은 승패·승무패만이므로 언오버/핸디캡을 포함한 전체 홈페이지 재현은 아니다.', '',
        '아카이브 배당은 관측시각이 없어서 실제 T-30 가격이라고 확인할 수 없다. ROI는 해당 기록 가격에 대한 산술 진단이며 실현 가능한 투자수익의 증거가 아니다. 운영 승격은 항상 금지했다.', '']
    if 'EPL' in controls['leagues']:
        e = controls['leagues']['EPL']
        if e['status']=='evaluated':
            c = e['matched_policy']
            lines += [f'EPL은 내부 검증에서 가중치 {e["alpha"]}를 선택했지만 평가 전체 적중률과 원래 추천 부분집합 적중률이 하락했다. '
                      f'같은 KST 날짜·0.1 배당 구간의 수를 맞추면 {c["market"]["hits"]}/{c["market"]["n"]} → '
                      f'{c["selected"]["hits"]}/{c["selected"]["n"]}로 한 경기 차이다. '
                      f'전체 확률 Brier 개선량은 {e["paired"]["brier_gain"]:.6f}, '
                      f'날짜 단위 5,000회 부트스트랩 95% 구간 {e["paired"]["ci95_unadjusted"]}이다. '
                      '구간은 다중비교 보정 전이며, 소규모 부분집합 차이를 근거로 채택하지 않는다.', '']
    probe = soccer['fixed_probe']
    lines += ['## 축구: 실제 사전 xG·배당 연결', '',
        f'요청 범위 {soccer["requested_dates_kst"]}. K리그1 정산 경기 {soccer["counts"].get("settled_fixtures_in_window",0)}개 중 '
        f'{soccer["counts"].get("emitted",0)}개만 조건을 만족했다. 무승부를 제거하지 않았다. 결과 분포: {soccer["outcomes"]}.', '',
        '시즌 누적 홈/원정 xG·xGA를 사용했다. 경기별 슛 위치, npxG, 선수별 기여, 퇴장·점수상황 조정 데이터는 아니다. 영문 팀명은 명시적인 K리그1 교차표로 연결했으며 결과 점수로 팀을 추론하지 않았다.', '',
        'xG 배치 시작시각에 24시간을 더한 보수적 이용 가능 시각이 T-30 이전이어야 한다. 배당은 실제 타임스탬프가 T-30 이전이고 T-30 기준 35분 이내여야 한다. 뒤에 관측된 취소 상태가 이전 배당을 무효화한다. 24시간은 가정이라 검증된 수신시각이라는 표시는 하지 않는다.', '']
    for name, score in probe.get('metrics',{}).items():
        lines.append(f'- {name}: {round(score["accuracy"]*probe["n"])}/{probe["n"]} 적중 '
                     f'({percent(score["accuracy"])}), 3결과 합산 Brier {score["brier_sum"]:.6f}.')
    lines += ['', '위 수치는 학습 없는 고정식 점검이다. xG 포아송은 두 팀의 공격·상대 수비 평균을 기대득점으로 둔 단순 독립 모형, 혼합은 시장과 50:50이다. 이 점검의 시장 확률은 역배당 정규화로, 장기 대조 실험의 Shin과 다르다. 표본이 너무 작아 학습·가중치 선택·추천 성능 검증은 보류했다. 적중률 상승을 입증한 결과가 아니다.', '',
        '다른 축구 리그: 원본에 K리그2/J1/J2 스냅샷도 있으나 이번 어댑터는 검증된 교차표가 있는 K리그1만 처리했다. 이들을 합쳐 표본을 부풀리지 않았다. 유럽 리그의 최근 xG 결합 실험과 전 리그 독립 검증은 아직 완료되지 않았다.', '',
        '## 야구: MLB 투구 수집 파일럿', '',
        f'{mlb["start"]}~{mlb["end"]} 일정 {mlb["scheduled_games"]}경기 중 앞 {mlb["collected_games"]}경기만 수집했다. '
        f'실제 수집 날짜 분포 {mlb["collected_date_coverage"]}. 14일 전체 수집이 아니다.', '',
        f'- 투구 {mlb["pitches"]:,}개, 투수 ID {mlb["pitcher_id_present"]:,}개, 구속 {mlb["speed_present"]:,}개.',
        f'- 투수·경기 {mlb["person_game_rows"]}행, 중복 투구 {mlb["duplicate_events"]}개.',
        f'- HTTP 요청 {sum(mlb["requests"].values())}회, 오류 {len(mlb["errors"])}건. 통계 표본 단위는 투구가 아니라 경기다.', '',
        '수신시각과 응답 해시를 사설 SQLite에 보존했다. 과거 3일 불펜 투구수를 만들 때 대상 경기의 실제 선발·등판자를 사전 정보로 쓰지 않으며 같은 날·미래 경기를 제외한다. 박스스코어 투수별 투구수와 맞지 않으면 불완전 피드로 처리하고 workload를 0이 아닌 null로 유지한다.', '',
        '현재 원본 30경기의 60개 팀·경기는 공식 박스스코어 투구수와 모두 일치했다. 불펜 피처 372행 중 18행만 필요한 과거 범위를 충족했다. 이 단계에서는 투구 데이터로 모델을 학습하거나 적중률 개선을 검증하지 않았다. KBO·NPB는 장기 팀 기록 대조 실험만 실행했으며, 동일한 투구 단위 수집기는 아직 없다.', '',
        '## 저장과 다음 판정', '',
        '입력 피처·분석 결과·감사 파일은 운영과 분리한 SQLite에 보관하고, 보고서는 Markdown으로 내보낸다. MLB 원본은 별도 파일럿 SQLite에 있다. 실제 운영 DB에 연결하거나 자동 수집 일정을 변경하지 않았다.', '',
        '다음 단계는 MLB 파일럿 범위를 채운 뒤 과거 수신시각/선발 발표 이력을 갖춘 데이터와 배당을 연결하는 것, 축구는 리그별 전체 시즌 xG·배당·팀 ID의 확보 여부를 확인하는 것이다. 기간과 수집량을 늘려도 개선은 보장되지 않는다. 동일 추천 수·비슷한 배당 및 독립된 미래 평가에서 확률 오차와 적중률을 함께 확인하기 전에는 가중치를 바꾸지 않는다.', '',
        '## 자료와 재현', '',
        '- [MLB 일정 API](https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2025-06-01&endDate=2025-06-14)',
        '- [MLB 실제 경기 피드 예시](https://statsapi.mlb.com/api/v1.1/game/777678/feed/live)',
        '- [기존 축구 xG 공급원](https://footystats.org/south-korea/k-league-1)',
        '- 기존 games.csv, xg_snapshots.jsonl, odds_timeseries 원본 해시는 함께 저장한 manifest/audit와 SQLite에 보존한다.',
        '- 실행 진입점: context_archive_controls.py → league_context_validation.py; soccer_context_pilot.py → league_context_validation.py; mlb_pitch_pilot.py.',
        '- 상세 재현 명령과 테스트 결과는 같은 PR의 README.md를 참조한다.', '']
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-dir',type=Path,required=True)
    p.add_argument('--mlb-summary',type=Path,required=True)
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--summary',type=Path,required=True)
    args=p.parse_args()
    files=[args.run_dir/name for name in ('controls.json','controls.manifest.json','controls-result.json','soccer-rows.json','soccer-audit.json','soccer-result.json')]+[args.mlb_summary]
    targets=[args.database,args.report,args.summary]
    if len({x.resolve() for x in targets})!=3 or any(x.exists() for x in targets):
        p.error('output paths must be distinct and NEW')
    payloads={f.name:f.read_bytes() for f in files}
    control=json.loads(payloads['controls-result.json'])
    soccer=json.loads(payloads['soccer-audit.json'])
    mlb=json.loads(payloads[args.mlb_summary.name])
    report=render(control,soccer,mlb)
    compact=json.loads(json.dumps(control))
    for result in compact['leagues'].values():
        for key in ('market_policy','selected_policy'):
            if key in result:
                result[key].pop('picks',None)
    compact['soccer_fixed_probe']=soccer['fixed_probe']
    compact['mlb_pilot']=mlb
    for target in targets:
        target.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(args.database) as db:
        db.execute('CREATE TABLE artifacts(name TEXT PRIMARY KEY, received_at TEXT, sha256 TEXT, payload BLOB)')
        for name, data in payloads.items():
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?)',(name,datetime.now(timezone.utc).isoformat(),hashlib.sha256(data).hexdigest(),data))
        db.execute("CREATE TRIGGER immutable_update BEFORE UPDATE ON artifacts BEGIN SELECT RAISE(ABORT,'immutable'); END")
        db.execute("CREATE TRIGGER immutable_delete BEFORE DELETE ON artifacts BEGIN SELECT RAISE(ABORT,'immutable'); END")
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    args.report.write_text(report,encoding='utf-8')
    args.summary.write_text(json.dumps(compact,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(args.report.resolve())


if __name__=='__main__':
    main()
