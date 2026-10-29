"""Render Korean Markdown report from the frozen MLB research artifacts."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'experiments/baseball-league-retune-20261029/mlb'


def main():
    r=json.loads((FOLDER/'results.json').read_text(encoding='utf8'))
    v=json.loads((FOLDER/'verification.json').read_text(encoding='utf8'))
    lines=['# MLB 득점 생성 모형 재검증 — 2026-10-29','',
        '새230경기에서 검증 규칙으로 동결한 primary는 시장보다 나빴습니다. 시장124/230·ROI -16.852174%, primary122/230·-18.334783%입니다. 다른 동결 head가 일부 좋아도 primary를 교체하지 않습니다. 운영 채택·미래 수익의 근거가 되지 않습니다.','',
        '이 평가는 여러 선행 연구에서 이미 본 회고 집합입니다. 2023학습→2024선택→2023+24 재학습→2026평가 절차를 지켰지만, 진정한 미관측 실험으로 부를 수 없습니다. 모든 경기에 단폴1만원을 동일하게 걸고 월 손실 제한을 적용하지 않았습니다. 무승부는 환불합니다.','',
        '## 입력과 시점','',
        '- 보존가격: 2023년2,228경기, 2024년2,251경기. 정확한 매수 시각은 인증되지 않았습니다. 한국 표시 날짜00:00와 공식 예정 시작-10분 중 이른 시각을 학습/검증 cutoff로 썼습니다.',
        '- 득점률 학습: 가격 유무와 무관한 공식2023년2,464경기. 최종2023+24년4,933경기(2024년2,469경기). 2025·26 결과로 계수를 학습하지 않았습니다. 다만 판단 시점 이전2025·26 완료 결과는 당시 팀 이력을 계산하는 데 씁니다.',
        '- 공식 완료 이력9,873경기. 완료 상한을 만들 수 없거나 재개된17경기와 보존 공식 최종 결과에 없는1경기는 이력에서 제외했습니다. 평가 목표 경기 제외는0건입니다.',
        '- 새230: 2026-07-30~08-23, 실제 보존 quote의 decision_at까지 완료된 경기만 특징에 반영. 픽스터가 없어 시장 단독 대조입니다. 이를9:1이라 부르지 않습니다.',
        '- 기존116: 2025-11-01~2026-10-24(2025년1경기,2026년115경기). 비교용 시장90%+동결 당시 픽스터 투표10%를 대조로 씁니다. 운영투표모형 재현이 아닙니다.',
        '- 공식 firstPitch+gameDurationMinutes+delayDurationMinutes+60분을 보수적인 완료 상한으로 썼습니다. source_game_ids, 마지막 완료 상한, cutoff를 feature-facts.jsonl.gz에 보존했습니다. 목표와 같은 공식 날짜의 경기는 모두 과거 이력에서 제외합니다.',
        '- 현재 선발·타순을 사후 박스에서 꺼내 쓰지 않았습니다. 과거 보존 입력의 announcement cutoff만 사용하고 개별 선수 입력 자체는 사용하지 않았습니다.','',
        '처음 추가한 목표 첫 투구-10분 조건은 gamePk824194(판단00:00Z, 예정00:10Z, 실제00:09Z)를 불필요하게 제외했습니다. 평가 출력/결과 파일 열람 전에 원시 시각만 확인해 실제 시작 전 조건으로 정정했고116경기를 모두 유지했습니다. 최초 비동기 평가 계산은 이미 끝났을 수 있어 계산 전 수정이라고 주장하지 않습니다. 후보·선택·기간·계수식은 바꾸지 않았습니다. 최초 실행의 NumPy bool 산술 오류도 모델을 바꾸지 않고 float 변환으로 고쳤습니다. PROTOCOL.md와 execution-notes.json에 기록했습니다.','',
        '## 산정식과 고정 범위','',
        '후보24개+market_null1개입니다. 최근 반감기45/180일 × 승률식Poisson/음이항/Pythagorean × pure/시장75%·득점25%/시장50%·득점50%/시장 잔차 보정의24개를 모두 실행했습니다. 반복적인 소수 선형계수 흔들기 대신, 실제 득점 분포와 승률 변환식을 비교했습니다.','',
        '시점 이전 시즌별 리그 경기당 득점으로 팀 득점/허용득점을 정규화합니다. 리그 환경에는30경기·4.5득점 사전값, 팀 비율에는 중립20경기, 홈원정 비율에는 중립30경기 사전값을 둡니다. 이전 시즌은0.5배와 시간 감쇠를 함께 적용하고 그 이전 시즌은 버립니다. 현재 시즌 환경 μ에는 이전 시즌 환경30경기 사전값을 사용합니다.','',
        '`log λ = log μ + Xβ`, 입력 순서는 아래 계수표와 같습니다. 공격팀/수비팀을 바꿔 홈·원정 득점 관측을 각각 학습합니다. 득점 Poisson 회귀의 평균 음의 로그우도(상수항 제외)에0.02 L2를 더하고 상수항은 규제하지 않습니다.','',
        'Poisson은 독립 득점 차의 Skellam 승리확률에 동률질량×λh/(λh+λa)를 더합니다. 음이항은 동일 λ와 학습 잔차 기반 α를 사용해0~100득점 질량을 합칩니다. 누락 꼬리질량도 같은 비율로 나누는 고정 근사입니다. Pythagorean은 `λh^1.83/(λh^1.83+λa^1.83)`입니다. 시즌 승률 공식을 단일 경기 matchup에 확장한 것은 이 실험의 가정입니다.','',
        '일정 변수는 상대 최근3일 경기수/3, 상대 최근3일 경기시간180분 초과분/180 합계, 그리고 경기수/3×상대 최근14일 허용득점 로그비율입니다. 이는 불펜을 포함한 투수진 부담의 대용치입니다. 2023 개별 투수 로그가 없어 실제 구원투수 투구수·연투 효과는 학습하지 않았습니다. 계수의 부호를 인과효과로 해석하지 않습니다.','',
        'residual은 `logit(p)=logit(base)+b0+b1·(logit(score)−logit(base))`이며0.02 L2/상수항 비규제입니다. 2023 가격표본으로 선택용 계수를 학습하고2023+24로 재학습했습니다. score 모형의 학습 내 예측을 잔차 학습에 썼으므로 학습 내부 성과는 낙관적일 수 있습니다. 선택에는2024만 썼습니다. 보조116에서는 동일 계수의 base에 비교용9:1을 넣습니다.','',
        'primary는2024 적중률과ROI가 시장 이상인 후보 중 logloss/Brier/이름 순 최소입니다. hit_leader는 적중률 최대(Brier/이름 tie), profit_leader는ROI 최대(logloss/이름 tie)이며 서로 바꾸지 않습니다. 시장 자체를 포함했고 frozen-selection.json을 평가 정산 전에 저장했습니다.','',
        '| 검증 역할 | 후보 | 적중/경기 | 적중률 | ROI | logloss | Brier | 시장과 픽 차이 |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for role,name in dict(baseline='market_null',**r['selected']).items():
        m=r['validation'][name]
        lines.append(f"| {role} | {name} | {m['wins']}/{m['n']} | {m['accuracy']*100:.6f}% | {m['roi']*100:.6f}% | {m['logloss']:.9f} | {m['brier']:.9f} | {r['validation_pick_changes'][name]} |")
    lines+=['','profit_leader는 검증ROI가 가장 높아도 적중률이 시장보다 낮아 primary 자격을 얻지 못했습니다. 검증의 작은 개선을 미래에서 보장하지 못했습니다.','',
        '## 동결 후 평가','',
        '기간은 새230의2026-08-10, 기존116의2026-07-01을 기준으로 미만/이상으로 나눴습니다. 모든 표본의 무승부는0건이고 coverage는100%입니다. 표의 픽 변경은 같은 경기 대조군 대비 방향이 바뀐 수입니다.','',
        '| 평가집합/기간 | 역할 | 적중/경기 | 적중률 | ROI | 손익(원) | 픽 변경 | logloss | Brier |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for cohort,table in r['periods'].items():
        for period,out in table['periods'].items():
            policies={'baseline':dict(metrics=out['baseline'],pick_changes=0),**out['policies']}
            for role,p in policies.items():
                m=p['metrics']
                lines.append(f"| {cohort}/{period} | {role} | {m['wins']}/{m['n']} | {m['accuracy']*100:.6f}% | {m['roi']*100:.6f}% | {m['net_krw']:,} | {p['pick_changes']} | {m['logloss']:.9f} | {m['brier']:.9f} |")
    lines+=['','## 불확실성과 해석','',
        '주 단위 동일 경기 paired bootstrap5,000회, seed20261029로 대조군과의 차이를 계산했습니다. 아래 구간의 단위는%p입니다. 전체/기간별 logloss·Brier 구간까지 results.json에 있습니다.','',
        '| 집합 | 역할 | 주수 | Δ적중률95% | ΔROI95% |','|---|---|---:|---:|---:|']
    for cohort,table in r['periods'].items():
        for role,p in table['periods']['all']['policies'].items():
            ci=p['paired_bootstrap'];a=ci['accuracy_delta95'];b=ci['roi_delta95']
            lines.append(f"| {cohort} | {role} | {ci['weeks']} | [{a[0]*100:.6f}, {a[1]*100:.6f}] | [{b[0]*100:.6f}, {b[1]*100:.6f}] |")
    lines+=['','새230은 단4주라 구간 추정 자체가 불안정합니다. hit_leader의 합계 적중률은54.782609%로 시장53.913043%보다 높지만8월10일 이후에는70/130으로 시장72/130보다 낮고, 차이 구간은0을 포함합니다. primary는 새230에서 적중률·ROI·logloss·Brier 모두 악화했습니다. 기존116에서는 primary가65/116·ROI -10.172414%로 비교용9:1의63/116·-13.344828%보다 좋아졌지만 적중률과ROI 구간은0을 포함합니다.','',
        '기존116의 primary logloss 차이95% 구간은[-0.011480614,-0.000662447]이나 반복해 본 회고 집합,25후보 선택,여러 head/기간 비교를 보정하지 않았습니다. 이를 확증적 통계 유의성·최적해·미래 흑자로 주장하지 않습니다. 모든 동결 head의 전체 평가ROI는 여전히 음수입니다.','',
        '홈팀은 끝내기나9회말 생략 때문에 득점 노출이 다르고 연장 득점도 포함돼 독립 득점 가정이 정확하지 않습니다. 구장·날씨·선발·라인업의 개별 실시간 정보를 쓰지 않았고, 이적/부상/전력변화는 과거 팀 이력으로만 간접 반영합니다. 과거 결과는 사후 수집본이므로 원래 발표 시각이나 이후 정정 여부까지 인증하지 못합니다. archived 가격은 실행 가능한 매수가격을 증명하지 않으며 수수료/세금은 추가하지 않았습니다. 9:1 대조의 개선을 새230 시장 단독 개선으로 바꾸어 말하지 않습니다.','',
        '## 실제 계수와 재현','',
        '최종 득점률 회귀 계수입니다. 선택용2023 계수, 모든 잔차계수·α·최적화 진단은 results.json의 configs에 있습니다.','',
        '| 변수 | h45 최종β | h180 최종β |','|---|---:|---:|']
    for i,name in enumerate(r['configs']['45']['final_score']['names']):
        lines.append(f"| {name} | {r['configs']['45']['final_score']['beta'][i]:.12f} | {r['configs']['180']['final_score']['beta'][i]:.12f} |")
    c=r['configs']['45']['pyth']
    lines+=['',f"primary 잔차계수(b0,b1)는 선택용{c['selection_residual']['beta']}, 최종{c['final_residual']['beta']}입니다. 최종 과산포α는 h45={r['configs']['45']['final_score']['alpha']:.12f}, h180={r['configs']['180']['final_score']['alpha']:.12f}입니다.",'',
        '런타임은 Python3.14.3 / NumPy2.5.1 / SciPy1.18.0입니다. 저장소 루트에서 실행합니다. 아래 run/검사에는 선행 worktree나 네트워크가 필요 없습니다. prepare는 최초 출처 추출용으로만 남겼으며 재현 때 실행하지 않습니다.','',
        '```powershell','python scripts/mlb_retune_research.py run','python -m unittest discover -s tests -p test_mlb_retune_research.py -v','python scripts/mlb_retune_research_verify.py','python scripts/mlb_retune_research_report.py','```','',
        f"커밋 `{v['source_commit']}`에서 코드·PROTOCOL·sources만 git archive로 꺼내 별도 디렉터리에서 실행했고 socket을 막아 네트워크 접근을 금지했습니다. {v['regression_tests']}개 회귀 검사가 통과했습니다. predictions/validation_predictions/frozen-selection/feature-facts의 정규화SHA256이 원실행과 모두 일치했고, 전체 validation/evaluation/기간별 수치와 계수도 정확히 같았습니다. 상세는 verification.json, tests.log, clean-replay.log에 있습니다.",'',
        '검사는 모든 후보/기간의 독립 정산, 원장 확률과 동결 계수 일치, 선택 규칙과 coverage, 모든 과거 source ID의 종료/날짜 경계, 미래·현재 결과 변조 불변성, 원자료에서 일정/득점/환경 변수 독립 재계산, 득점분포 대칭·단조성, 무승부 환불·가격 순서를 포함합니다. 원자료 해시는 sources/manifest.json과 results.json에 있습니다. 텍스트 해시는 Git CRLF 차이를 정규화합니다.','',
        '검증 원장 validation_predictions.jsonl에는2024 전2,251경기의 모든25후보 확률과 시장확률이 있어 부모가 검증 주를 재표본해 선택 안정성을 확인할 수 있습니다. 평가 predictions.jsonl에는346경기의 모든 후보 확률과 primary/hit_leader/profit_leader 각각의 후보명·확률, baseline_kind, 가격순서, fixture_id/gamePk를 저장했습니다.','',
        '## 확인한 1차 근거','',
        '- [MLB 공식 Pythagorean 설명](https://www.mlb.com/glossary/advanced-stats/pythagorean-winning-percentage): 득실점 전력 추정과1.83 지수를 소개합니다(2026-10-29 확인).',
        '- [Miller, A Derivation of the Pythagorean Won-Loss Formula in Baseball](https://arxiv.org/abs/math/0509698): 독립 Weibull 득점 가정에서 Pythagorean 관계를 유도합니다. 이 논문은 본 실험의 Poisson/NB나 시장 초과수익을 입증하지 않습니다(2026-10-29 확인).',
        '- 결과 자료 자체의 공식 원천은 [MLB StatsAPI2023 schedule](https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=2023-01-01&endDate=2023-12-31&gameTypes=R%2CF%2CD%2CL%2CW&hydrate=gameInfo)입니다. 2024~26 schedule과 보존 결과는 선행 수집본을 복사했고 출처 URL·원본 해시를 manifest에 남겼습니다.','',
        '운영 src·DB·추천정책·서버·배포를 변경하지 않았습니다. 이 작업의 push/PR/merge는 수행하지 않으며 부모가 cherry-pick 후 통합합니다.','']
    report=ROOT/'docs/research/2026-10-29-mlb-retune.md'
    report.write_text('\n'.join(lines),encoding='utf8',newline='\n')
    print(report)


if __name__=='__main__': main()
