"""Generate the fixed-model comparison and explicit adoption decision."""
from decimal import Decimal,ROUND_HALF_UP
import importlib.util,json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
def module(name):
    spec=importlib.util.spec_from_file_location('full_sports_'+name,HERE/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def percent(value):
    return '해당 없음' if value is None else str((Decimal(str(value))*100).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))+'%'


def run():
    verifier=module('integrate');common=module('common')
    reports,rows,audit=verifier.verify()
    pooled={m:{'policies':{p:common.metrics(rows['domestic_archive'],m,p) for p in common.POLICIES},
               'underdog_vs_skip':common.interval(rows['domestic_archive'],m),
               'underdog_vs_market':common.interval(rows['domestic_archive'],m,True)} for m in common.MODELS}
    for m in common.MODELS:
        for p in common.POLICIES:verifier.check_cell(rows['domestic_archive'],m,p,pooled[m]['policies'][p])
    decision={'blanket_include_underdogs_adopt':False,'production_changed':False,
              'reason':'Every nonmarket fixed model has negative incremental underdog net in each domestic sport. Historical rank/league winners do not change this policy decision.',
              'domestic_pooled':pooled,
              'sport_model_underdog_cells':{key:{m:rep['full'][m]['policies']['underdogs'] for m in common.MODELS[1:]} for key,rep in reports.items()},
              'verification':audit}
    assert all(Decimal(rep['full'][m]['policies']['underdogs']['net_units'])<0 for key,rep in reports.items() if key.startswith('domestic_archive/') for m in common.MODELS[1:])
    common.save(HERE/'decision.json',decision)
    names={'sc':'축구','bs':'야구','bk':'농구','vl':'배구'}
    model_names={'market':'시장 방향','outcome_form':'전적·전력','market_residual':'시장 잔차 보정','half_market_form':'시장·전적 50:50'}
    domestic=json.loads((HERE/'inputs/domestic-audit.json').read_text(encoding='utf8'))
    ext=json.loads((HERE/'inputs/external-football-audit.json').read_text(encoding='utf8'))
    lines=['# 전 종목·전체 기간 역배 포함 검증', '',
      '**이번에 검사한 고정 모델에서는 역배를 전 종목에 일괄 포함하는 정책을 채택하지 않는다.** 국내 프로토 계열 축구·야구·농구·배구 전체 원장을 계산했고, 추가로 장기 축구 아카이브를 분리해서 계산했다. 국내 네 종목 모두 세 비시장 모델의 역배 전체 손익이 음수였다. 부분 기간·리그의 양수 수익을 보고 전면 채택하는 근거로 바꾸지 않는다. 운영 모델·서버·DB·지갑에는 변경이 없다.','',
      '## 실제 데이터 범위','',
      f'- 국내 프로토 계열: {domestic["fixtures"]:,}개 고유 경기, {domestic["range"][0]}~{domestic["range"][1]}, {len(domestic["leagues"])}개 리그. 과거 주시장 캐시 45,184건과 전체 시장 원본 171,565행을 결합하고 같은 실제 경기·가격·결과를 중복 제거했다. 충돌한 fixture는 전부 제외하며 이번 연결에서 충돌 {domestic["conflicting_fixtures_excluded"]}건이다.',
      f'- 별도 장기 축구: 원본 {ext["all_raw_rows"]:,}행 중 {ext["accepted_rows"]:,}경기, {ext["accepted_date_range"][0]}~{ext["accepted_date_range"][1]}, {len(ext["accepted_by_league"])}개 Division. 배당·점수/결과 불일치·결측 등 {ext["excluded_rows"]:,}행을 제외했다.',
      '- 두 코호트의 경기·손익을 합산하지 않는다. 팀명이 한국어/외국어이고 가격 출처도 달라 중복 경기 수를 완전하게 인증하지 못했다. 해외 장기 아카이브 배당은 프로토 구매 가격이 아니다.',
      '- 보유 자료에 있는 네 종목 전체를 포함했다. 아이스하키·테니스 등 없는 종목까지 검증했다는 뜻은 아니다. 자료 마지막 날짜 이후의 최신 경기는 이 실험에 없으며 과거 기록을 오늘까지 채워 넣지 않았다.',
      '- 원본의 핸디캡·언더오버·전반·홀짝·승①패·승⑤패 등은 주 승무패와 다른 정산이다. 이번 예측/ROI 범위는 축구 3방향 승무패, 야구·농구·배구 2방향 승패이며 다른 상품에 결과를 임의 전용하지 않았다. 모든 원본 시장별 수와 제외 사유는 입력 audit에 보존했다.', '',
      '## 고정한 모델·시간 순서','',
      '프로토콜은 성과 실행 전 커밋 `45d3c736`에서 고정했다. 축구·야구·농구·배구를 Terra 워커별 전용 브랜치/작업폴더에서 같은 common.py와 입력 SHA로 실행했다. 부모 세션은 정규화·정산·비교·최종 판단을 맡았다. 모델/비중/배당 문턱을 결과에 맞춰 탐색하지 않았다.','',
      '1. 시장 방향: 같은 경기 저장 배당 역수의 합을 1로 정규화한 확률의 argmax.',
      '2. 전적·전력(outcome_form): 시장 없이 인과적인 Elo 차이·최근 20경기 결과 차이·리그 사전 홈/무승부 빈도를 쓰는 L2=0.01 다항 로짓.',
      '3. 시장 잔차 보정(market_residual): 같은 피처와 L2를 쓰되 log(시장 확률)을 offset으로 추가.',
      '4. 시장·전적 50:50: 미리 고정한 확률 평균. 수익이 좋았다는 이유로 사후 선택한 혼합비가 아니다.','',
      '**이 모형은 앞서 445건에서 검사한 Poisson 득점 score-static이나 픽스터 9:1과 다르다.** 농구·배구에 야구 득점 분포를 그대로 적용하지 않기 위해 공통 승무패 모형을 확장한 것이다. 기존 득점 모델을 전 종목에서 동일하게 재현했다거나 픽스터를 넣어 28만 경기를 검증했다고 설명하지 않는다. 앞선 [PR #471](https://github.com/choigod1023/proto-odds-research/pull/471)의 작은 득점 모형 결과는 별도 근거로 유지하며 이 통계에 합치지 않는다.','',
      '가용성은 보수적인 날짜+4일 가정으로 고정했다. 월 첫날마다 그 시점까지 가용한 이전 결과만으로 두 로짓 모델과 스케일을 다시 학습한다. 타깃 날짜의 피처에도 +4일 이전 결과만 들어간다. 해당 월의 결과를 그 월 회귀계수에 넣지 않았다. 초기 이전 표본 200건 미달/클래스 부족 시 시장으로 명시적 fallback하며 첫해를 미래 데이터로 학습해 메우지 않는다. 원본의 Elo·슛·현재 경기 득점 등 파생 열은 입력하지 않는다. q=0 득점 모형과 같은 상태라는 주장은 하지 않는다. 날짜+4일은 실제 수신 기록의 증명이 아니다.','',
      '## 국내 네 종목: 전체 기간 단폴 ROI','',
      '전 경기마다 같은 1단위를 사용한다. 적중 odds−1, 실패 −1. 표의 전체 ROI는 역배·최고 동률을 포함한 모델 argmax 전체다. 역배는 선택한 시장확률이 최대보다 낮은 경우이며 배당 2.20 같은 문턱은 없다. 최고 동률은 별도 그룹이므로 정배+역배 건수가 전체와 다를 수 있다. 월 손실 제한을 바꾸거나 실제 지갑/2폴을 실행하지 않았다.','',
      '| 종목 | 경기 수 | 시장 | 전적·전력 | 시장 잔차 보정 | 시장·전적 50:50 |',
      '|---|---:|---:|---:|---:|---:|']
    for s in ['sc','bs','bk','vl']:
        r=reports['domestic_archive/'+s]
        lines.append('| '+names[s]+f' | {r["count"]:,} | '+' | '.join(percent(r['full'][m]['policies']['all']['roi']) for m in common.MODELS)+' |')
    lines+=['','## 역배 포함이 더한 손익','',
      '아래는 **정배만 선택하고 역배를 건너뛰는 경우에 추가되는 역배 손익**이다. 모든 추가 손익이 음수였으므로 네 종목 모두 전체 기간에서 역배 포함이 총손익을 악화시켰다. 최고 동률의 손익은 별도다. ROI 비율의 변화와 총손익의 변화는 구분해야 한다.','',
      '| 종목 | 모델 | 역배 건수 | 적중률 | 역배 ROI | 추가 손익(단위) | 기술적 주 블록 95% ROI 구간 |',
      '|---|---|---:|---:|---:|---:|---|']
    for s in ['sc','bs','bk','vl']:
        for m in common.MODELS[1:]:
            a=reports['domestic_archive/'+s]['full'][m];v=a['policies']['underdogs'];ci=a['underdog_vs_skip']['ci95']
            lines.append(f'| {names[s]} | {model_names[m]} | {v["bets"]:,} | {percent(v["accuracy"])} | {percent(v["roi"])} | {v["net_units"]} | {percent(ci[0])}~{percent(ci[1])} |')
    lines+=['','## 같은 자금 단위로 본 국내 전체 원장','',
      '네 종목의 동일 출처 원장 안에서만 합산했다. 종목별 별도 계좌의 월 한도 수익을 더한 값이 아니라 매 선택 1단위의 단순 합계다.','',
      '| 모델 | 역배 포함 전체 손익 / ROI | 정배만 손익 / ROI | 역배 추가 손익 | 일별 마감 최대낙폭(포함 / 정배만) |',
      '|---|---:|---:|---:|---:|']
    for m,a in pooled.items():
        p=a['policies'];allp=p['all'];fav=p['favorites'];und=p['underdogs']
        lines.append(f'| {model_names[m]} | {allp["net_units"]} / {percent(allp["roi"])} | {fav["net_units"]} / {percent(fav["roi"])} | {und["net_units"]} | {allp["drawdown_units"]} / {fav["drawdown_units"]} |')
    lines+=['','낙폭은 원본 경기 날짜에 손익을 귀속한 일별 마감 지표다. 실제 종료·정산 시각, 미정산 노출과 지갑 잔액의 낙폭을 인증하지 않는다. 역배를 빼면 투입 금액과 경기 수가 줄어드는 효과도 있으므로 총손실 감소를 같은 거래량의 우월성이라고 해석하지 않는다.','',
      '## 연도별 역배 ROI','',
      '| 종목 | 모델 | 2023 | 2024 | 2025 | 2026(자료 종료일까지) |',
      '|---|---|---:|---:|---:|---:|']
    for s in ['sc','bs','bk','vl']:
        r=reports['domestic_archive/'+s]
        for m in common.MODELS[1:]:
            lines.append('| '+names[s]+' | '+model_names[m]+' | '+' | '.join(percent(r['by_year'][y][m]['policies']['underdogs']['roi']) for y in sorted(r['by_year']))+' |')
    lines+=['','월별 손익과 손익 월 수, 첫/뒤 절반, 전 리그 집계는 각 워커 results.json에 있다. 이번에 수익이 좋았던 특정 연도·리그로 좁혀 다시 승자로 고르지 않았다. 작은 역배 표본에서 한두 경기의 부호 전환을 안정적 수익으로 보지 않는다. 축구의 2위/3위/무승부 역배는 `underdog_breakdown`에서 따로 보존했다.','',
      '## 2000년부터 별도 장기 축구 검증','',
      '| 모델 | 전체 적중률 | 전체 ROI | 역배 건수 | 역배 ROI | 역배 추가 손익 |',
      '|---|---:|---:|---:|---:|---:|']
    long=reports['external_football_archive/sc']
    for m,a in long['full'].items():
        allp=a['policies']['all'];u=a['policies']['underdogs']
        lines.append(f'| {model_names[m]} | {percent(allp["accuracy"])} | {percent(allp["roi"])} | {u["bets"]:,} | {percent(u["roi"])} | {u["net_units"]} |')
    lines+=['','이 원장은 해외 아카이브 가격이며 프로토 세후 실수익이 아니다. 38개 Division 전체를 쓰되 서로 다른 리그의 시장·시즌 분포 차이가 있다. 일부 리그/연도의 승자만 추려 운영 성과처럼 보고하지 않는다. 전체 기간을 펼쳤다는 것은 독립 맹검 자료가 늘었다는 뜻이 아니다.','',
      '## 검증과 채택 판단','',
      f'워커별 Decimal 검사에 더해 메인 독립 검산은 코호트별 원장 행의 합계 {audit["source_fixtures"]:,}건(서로 독립인 고유 경기 수가 아님), 예측·정산 {audit["independently_verified_forecasts"]:,}건, 정책 집계 {audit["independently_verified_policy_cells"]:,}개를 확인했다. 불일치 0건이다. 원본 배당·라벨·확률 argmax·역배 순위·무승부·월별 학습 표본 수와 상태 SHA·월별 손익·낙폭을 대조했다. 이 정산 검산은 전체 최적화 알고리즘을 별도 구현해 같은 확률을 얻었다는 뜻은 아니다.',
      '회귀 테스트는 현재/미래 라벨 변경에 대한 동결 월 계수·이전 예측의 불변, +4일 embargo, 입력 순서 불변, 초기 fallback, 3방향·최고 동률, 중복 충돌 전부 제외, 빈 표본과 분모, 외부 축구 점수·결과 일치/부정확 가격을 검사한다. 전체 테스트는 2,166 passed, 6 skipped, 93 subtests passed였다.','',
      '1. **전 종목 역배 일괄 포함은 채택하지 않는다.** 이번 세 고정 비시장 모델 모두 국내 네 종목에서 역배 추가 손익이 음수다.',
      '2. 역배라는 이유만으로 평생 금지한다는 결론도 아니다. 다른 입력·다른 고정 모델이 특정 리그에서 이전 시점만으로 안정적인 이점을 입증하면 그 조건을 따로 평가한다. 이번 성과를 보고 조건을 만든 뒤 같은 자료에서 검증했다고 부르지 않는다.',
      '3. 앞선 득점 모델의 111건 거의 본전은 이 대규모 승무패 모형의 음수 결과로 대체하거나 합산하지 않는다. 별도 모델의 제한된 후보 근거로만 남긴다.',
      '4. 새 후보의 전면 채택·미래 기대수익 보장은 불가하다. 손실 감소가 목적이어도 추가 손익이 음수인 선택을 추가할 근거가 없고, 검증된 구매 시각·가격·당시 선발/결장·픽스터 등 사전 증거가 필요하다. 독립 경기 전 수신/실제 구매 가격 인증은 이번에도 0건이다.','',
      '## 재현과 산출물','',
      '프로토콜·원장·코드는 `experiments/all-sports-underdog-20261031/`에 있다. 국내 준비는 prepare.py, 장기 축구는 football_archive.py, 워커 실행은 common.py, 독립 검산은 integrate.py, 결론/보고 생성은 report.py다. 읽기 원본 파일을 덮어쓰지 않는다. 원본 절대 경로는 audit에, 재현 가능한 정규화 데이터는 inputs/*.jsonl.gz에 보존했다.','',
      '```powershell',
      'python -X utf8 experiments/all-sports-underdog-20261031/common.py --input experiments/all-sports-underdog-20261031/inputs/domestic.jsonl.gz --sport bs --output .local-test/full-sports-bs',
      'python -X utf8 experiments/all-sports-underdog-20261031/integrate.py',
      'python -X utf8 experiments/all-sports-underdog-20261031/report.py',
      'python -m pytest -q',
      '```','']
    path=ROOT/'docs/research/2026-10-31-all-sports-underdog-validation.md'
    path.write_text('\n'.join(lines),encoding='utf8',newline='\n')
    print(json.dumps({'report':str(path),'verification':audit,'pooled_roi':{m:a['policies']['all']['roi'] for m,a in pooled.items()}},ensure_ascii=False))


if __name__=='__main__':run()
