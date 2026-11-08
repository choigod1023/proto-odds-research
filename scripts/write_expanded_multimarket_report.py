"""Render the expanded study's recorded computations as a concise Korean report."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/expanded-multimarket-study-20261105"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def pct(x):
    return f"{x*100:.2f}%" if x is not None else "미산출"


def main():
    source = read(OUT / "source-manifest.json")
    baseline = read(ROOT / "experiments/expanded-market-baselines-20261105/audit.json")
    frozen = read(OUT / "frozen-model-comparison-expanded.json")
    strict = read(OUT / "frozen-model-comparison-strict.json")
    later = read(OUT / "frozen-model-comparison-later-only.json")
    overall = baseline["primary"]["overall"]["all"]
    lines = ["# 2026-11-05 언더오버·핸디캡 확대 검증", "",
        "기존 44경기를 반복한 결과와 구분해 공식 원문·기간·종목을 확대했다. 확대 결과에서도 M0를 언더오버·핸디캡의 우위 모델로 채택할 근거는 확인하지 못했다. 낮은 배당의 선택은 적중률이 높아도 이 표본에서 손실이었다.", "",
        "## 범위와 실제로 수행한 비교", "",
        f"공식 원문은 147회차에서 {source['source_round_count']}회차로 늘었다. 압축 해제 본문 SHA256, 요청·응답 회차, 표 구조를 검증했다. 같은 본문의 중복 {source['duplicate_captures']}개를 제거했고 회차별 다른 본문 충돌은 {len(source['excluded_round_variants'])}개였다. 원본 상품 행은 독립 경기로 세지 않았다.", "",
        f"시장 기준선은 고유 경기 {overall['fixtures']:,}개, 경기·시장 대표 선택 {overall['n']:,}건이다. 적격 경기 시각은 {overall['kickoff_min']}~{overall['kickoff_max']}. 같은 경기에서 언더오버와 핸디캡이 모두 적격이면 두 선택이다. 원본 220회차에는 2025년 107회차도 있지만 구형 상품 ID가 없는 행은 경기 범위를 입증하지 못해 이 주 분석 성적에 포함하지 않았다.", "",
        "KBO 동결 M0/M1 득점률이 있는 상품에서는 같은 경기·기준점·가격·공식 원점수로 실제 모델 비교를 했다. 축구·농구에서는 같은 상품의 동결 선수 모델 예측을 확인하지 못했으므로 시장 기준선만 계산했다. 이를 전체 종목의 M0·픽스터·9:1 비교 완료라고 해석하면 안 된다.", "",
        "## KBO 동결 모델 비교: 공식 경기와 상품 ID 연결", "",
        "기존 공식 ID 예측 470건 외에 형식이 다른 예측 239건을 원래 예측 입력·공식 KBO 일정·Betman 시작 시각으로 대조했다. 날짜·팀·시간이 유일하게 맞은 237건을 연결하고 시각이 다른 2건을 제외했다. 점수와 승패는 경기 식별에 사용하지 않았다. 상품과 정산까지 통과한 추가 표본은 언더오버 153경기, 핸디캡 159경기다. 전체 동결 모델 비교는 고유 368경기, 대표 상품 721건이다.", "",
        "각 모델은 고정 계수의 득점 회귀와 독립 Poisson/Skellam 분포다. `actual`은 실제 타순으로 학습한 사양 이름이고, 당일 운영 예측이나 경기 전 수신 증명이 아니다. `projected` 사양도 같은 보존 입력에서 계산한 회고 비교다. 어느 모델도 이번 결과에 맞춰 재학습하지 않았다.", ""]
    for market, title in (("total", "언더오버"), ("handicap", "핸디캡")):
        data = frozen["markets"][market]
        lines += [f"### {title}: {data['date_min']}~{data['date_max']}", ""]
        for model, label in (("m0_actual", "M0 actual"), ("m0_projected", "M0 projected"),
                             ("m1_projected", "M1 projected"), ("market", "시장 최저 배당")):
            result = data["models"][model]
            m = result["all"]
            lines.append(f"- {label}: {m['hits']}/{m['n']} 적중, 적중률 {pct(m['hit_rate'])}, ROI {pct(m['roi'])}, 순손익 {m['net_units']:+.2f}단위, 최대 낙폭 {m['max_drawdown_units']:.2f}단위.")
        lines += ["", "확률 진단(이 표본의 기술적 계산):", ""]
        for model in ("m0_actual", "m0_projected", "m1_projected", "market"):
            d = data["models"][model]["diagnostics"]
            lines.append(f"- {model}: 선택 평균 확률 {pct(d['mean_selected_probability'])}, 실제 적중률 {pct(data['models'][model]['all']['hit_rate'])}, 이진 Brier {d['binary_brier']:.4f}, log loss {d['log_loss']:.4f}. 시장 확률은 역배당을 정규화한 참조값이며 실제 확률 인증이 아니다.")
        lines += ["", "시장 대비 ROI 차이의 날짜 군집 재추출 기술적 95% 구간:", ""]
        for model in ("m0_actual", "m0_projected", "m1_projected"):
            result = data["models"][model]
            p = result["paired_with_market"]
            lo, hi = p["descriptive_day_cluster_bootstrap95"]
            lines.append(f"- {model}: {p['roi_difference']*100:+.2f}%p, 구간 [{lo*100:+.2f}, {hi*100:+.2f}]%p. 시장과 다른 선택 {result['changed_picks']}건.")
        lines += ["", f"기간 절반 구분일은 {data['descriptive_split_date']}다. 날짜를 기준으로 나눴으며 미관측 외부 검증은 아니다. 앞·뒤 기간별 적중률·ROI와 M0 대비 차이는 동봉 JSON에 있다.", ""]
    proof = frozen.get("independent_baseline_crosscheck", {})
    lines += [f"두 분석 코드에서 겹치는 {proof.get('matched_exact_products', 0)}개 대표 상품의 기준점·배당·원점수·시장 방향이 일치했다. 모델 공통 표본 제외는 {len(frozen['excluded_common_cohort'])}건이었다.", "",
        "초기 공식 ID 표본의 핸디캡 209경기에서는 모두 ±2.5 상품의 2.5점을 받는 쪽을 골랐으므로 M0와 시장 선택이 전부 일치했다. 확대 후에도 368경기 중 366경기에서 일치했다. 다른 두 경기에서는 시장 선택만 맞았다. M0에 배당을 넣어서 복제한 것은 아니며, 선택 일치가 확률의 일치나 독립적인 예측력 증명은 아니다.", "",
        "### 기간을 넓혔을 때 달라진 점", ""]
    for market, title in (("total", "언더오버"), ("handicap", "핸디캡")):
        early = strict["markets"][market]
        late = later["markets"][market]
        for label, group in (("공식 ID 기존 구간", early), ("추가로 연결한 후기 구간", late)):
            result = group["models"]
            n = result["market"]["all"]["n"]
            lines.append(f"- {title} {label} {group['date_min']}~{group['date_max']}, {n}경기: " + "; ".join(
                f"{m} 적중률 {pct(result[m]['all']['hit_rate'])}, ROI {pct(result[m]['all']['roi'])}"
                for m in ("m0_actual", "m0_projected", "m1_projected", "market")) + ".")
    lines += ["",
        "후기 언더오버에서 M1 projected는 +0.27단위, ROI +0.18%로 거의 본전이었다. 전체에서는 -28.51단위, ROI -8.08%다. 시장보다 ROI가 6.32%p 높지만 날짜 군집 재추출 구간은 -7.89~+20.16%p로 0을 포함한다. M0 대비 ROI 차이는 +7.11%p(구간 -4.06~+17.70%p)다. M1의 언더오버 확률 오차는 시장보다 커서 이 결과를 보정된 기대수익률로 쓰기 어렵다.", "",
        "전체 상품 적격성 검사에서 시장별 대표 512건을 확인했다. 언더오버와 핸디캡 각각 134건은 원문 기준점과 양쪽 배당이 모두 0, 결과 코드 4, 상품 점수 없음이어서 제외했다. 각각 10건은 유일한 공식 원점수를 확보하지 못했고, 언더오버는 동률 배당 15건을 더 제외했다. 선택 규칙을 사후에 바꿔 뒤 회차의 상품을 고르지 않았다.", "",
        "구형 상품 유형을 추론한 보조 표본은 언더오버 264경기, 핸디캡 274경기로 따로 보존했다. 언더오버 M0는 적중률 47.73%·ROI -17.57%, 시장은 51.89%·-13.48%; 핸디캡은 둘 다 63.50%·-14.16%다. 기존 44경기는 별도 재현했으며 합쳐서 새로운 독립 검증으로 세지 않았다.", "",
        "## 축구·야구·농구 시장 기준선", "",
        "아래 값은 해당 상품의 가장 낮은 배당 방향을 매번 1단위씩 선택한 회고 성적이다. 선수 모델·픽스터 모델·현행 투표 정책의 성적이 아니다.", ""]
    for sport, title in (("SC", "축구"), ("BS", "야구"), ("BK", "농구")):
        for market, market_title in (("total", "언더오버"), ("handicap", "핸디캡")):
            m = baseline["primary"]["sport_market"][sport + "|" + market]
            lines.append(f"- {title} {market_title}: {m['wins']:,}/{m['n']:,} 적중, 적중률 {pct(m['hit_rate'])}, ROI {pct(m['roi'])}, 최대 낙폭 {m['max_drawdown']:.2f}단위.")
    lines += ["", "### 자주 보는 리그", ""]
    leagues = baseline["primary"]["league_market"]
    for code, title in (("SC001", "K리그1"), ("SC003", "K리그2"), ("BS001", "KBO"),
                        ("BS004", "NPB"), ("BS002", "MLB"), ("BK001", "KBL"), ("BK002", "NBA")):
        found = []
        for market, market_title in (("total", "언더오버"), ("handicap", "핸디캡")):
            match = [v for k, v in leagues.items() if k.split("|")[1] == code and k.endswith("|" + market)]
            if len(match) == 1:
                m = match[0]
                found.append(f"{market_title} {m['n']:,}건 / 적중률 {pct(m['hit_rate'])} / ROI {pct(m['roi'])}")
        lines.append(f"- {title}: " + ("; ".join(found) or "적격 표본 없음"))
    lines += ["", "## 정산과 통계의 한계", "",
        "- 대표 상품은 결과와 무관하게 같은 물리 경기·시장 안에서 회차, 상품 번호 순으로 먼저 고정했다. 대표가 부적격이면 후속 상품으로 바꾸지 않았다. 전체 상품 민감도는 별도 파일이고 비독립이다.",
        "- 원래 일반 경기 점수로 정산했다. 야구 원경기 무승부의 일반 승패 환불 코드 4를 핸디캡·언더오버 환불로 잘못 확장하지 않았다. 원점수와 상품 정산 코드 일치를 요구했다.",
        "- 지원하지 않는 부분 경기·0.25 단위·정수 2선택 핸디캡·범위 불명 상품·동률 선택·모호한 경기 연결은 제외했다. 확정된 정상 완료 표본의 환불은 0건으로, 모든 실제 베팅의 무효율을 뜻하지 않는다.",
        "- 가격 원본은 사후에 보관했다. 파일 해시와 2026년 11월 수신 시각은 과거 경기 전 구매 가능 가격을 입증하지 않는다. 월별·리그별 자료가 전체 일정의 무작위 표본도 아니다.",
        "- 2023~2025는 M0/M1의 학습 기간이다. 2026년도 이미 확인했던 회고 결과다. 날짜 군집 구간은 기술적 불확실성이며 여러 번의 연구·원천 누락·미래 분포 변화까지 해결하지 않는다.",
        "- KBO 최대 낙폭은 날짜·경기 ID 순서이며 같은 날의 실제 정산 순서를 입증하지 않는다. 전체 시장 원본도 경기 시작 시각 순으로 계산한 기술적 손익 경로다.",
        "- 픽스터 최초 관측 원장 14,608행은 승패 유형뿐이었다. 정확한 totals/handicap 라인·방향·시각을 연결할 적격 표본은 확인하지 못했다. 축구·농구 intake에도 model_prediction은 없었다. 이 모델들을 이름만 붙여 비교표를 채우지 않았다.",
        "- 월 손실 5만원 모의 원장과 실제 서비스 정책은 변경하지 않았다. 1단위 고정 연구 ROI를 월 수익 약속으로 해석하면 안 된다.", "",
        "## 다음 결정", "",
        "M0를 언더오버·핸디캡 주력으로 전환할 근거는 없다. M1 projected는 언더오버의 우선 후속 검증 후보다. 전체 적중률과 ROI가 비교군 중 가장 좋았고, 후기 손실도 줄었으나 기간별 우열이 바뀌고 ROI 차이의 기술적 구간에 0이 포함된다. 핸디캡에는 일관된 개선이 없다. 별도 동결 구간에서 확률과 가격을 함께 저장하고 재검증한 뒤 운영 전환을 판단한다.", "",
        "우선 보완할 것은 2025년 구형 상품 범위를 확인할 원천, 축구·농구의 시점 보존 모델 예측, 정확한 라인까지 보존한 경기 전 픽스터 원문이다. 기존 경기로 가중치만 무한 탐색하면 독립 검증 표본은 늘지 않는다.", "",
        "## 실행 근거", "",
        "- `source-manifest.json`: 220회차 경로·본문 해시·중복 제거 정책.",
        "- `frozen-model-comparison-expanded.json`: 고정 모델별 동일 상품 성적·날짜 구간·군집 재추출·코드 간 교차 검증.",
        "- `frozen-model-comparison-strict.json` 및 `frozen-model-comparison-later-only.json`: 원래 공식 ID 표본과 새로 연결한 후기 표본을 각각 보존.",
        "- `../expanded-kbo-identities-20261105/`: 공식 경기 ID 연결 근거, 원문 해시와 시간 불일치 제외 사유.",
        "- `../expanded-kbo-markets-20261105/`: 경기별 원점수·선택·상품 ID·일반 점수 출처 및 구형 민감도.",
        "- `../expanded-market-baselines-20261105/`: 11,407개 대표 상품, 전체 상품 민감도, 월·리그·제외 사유.",
        "- `../../docs/research/2026-11-05-expanded-market-study-protocol.md`: 사전에 고정한 비교 규칙.",
        "- 모델 정의: `../../research/m1_service/model.py`, `../../research/m1_service/frozen-models.json`, `../../research/m1_service/contract.json`.",
        "", "관련 공식 규칙: [스포츠토토 세부규칙](https://www.sportstoto.co.kr/terms_allocation.php), [프로토 규칙](https://www.sportstoto.co.kr/proto_rules.php). 과거 시즌별 상품 공지와 구매 시점 증명은 별도다.", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
