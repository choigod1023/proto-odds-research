"""Render the frozen KBO experiment as Korean Markdown, without model selection."""
from collections import Counter
import json
from pathlib import Path

from kbo_retune_research import DEFAULT, ROOT, read, write_json


def pct(v):
    return f'{100*v:.6f}%'


def metric_line(label, m):
    return (f'{label}: {m["wins"]}/{m["settled"]}승, 적중률 {pct(m["accuracy"])}, '
            f'ROI {pct(m["roi"])}, 손익 {m["net_krw"]:,}원, '
            f'logloss {m["logloss"]:.9f}, Brier {m["brier"]:.9f}.')


def main():
    result = json.loads((DEFAULT/'results.json').read_text(encoding='utf8'))
    frozen = json.loads((DEFAULT/'frozen-selection.json').read_text(encoding='utf8'))
    manifest = json.loads((DEFAULT/'input-manifest.json').read_text(encoding='utf8'))
    features = read(DEFAULT/'features.jsonl.gz')
    candidates, heads = result['candidates'], result['heads']
    primary = candidates[heads['primary']]
    diagnostics = {}
    for role in ['train', 'validation', 'evaluation']:
        rr = [r for r in features if r['role'] == role]
        gaps = [r['proof'][side]['score_games']-r['proof'][side]['batting_games']
                for r in rr for side in ['home', 'away']]
        diagnostics[role] = {'first': min(r['date'] for r in rr), 'last': max(r['date'] for r in rr),
                             'league_runs_min': min(r['league_runs'] for r in rr),
                             'league_runs_max': max(r['league_runs'] for r in rr),
                             'team_season_score_minus_batting_game_counts': dict(sorted(Counter(gaps).items())),
                             'team_game_count_min': min(s['n'] for r in rr for s in r['sides']),
                             'team_game_count_max': max(s['n'] for r in rr for s in r['sides'])}
    write_json(DEFAULT/'feature-diagnostics.json', diagnostics)
    lines = [
        '# KBO 리그별 모형 재연구 — 2026-10-29', '',
        'KBO의 과거 득실점·타격·휴식·일정·선발 평균 이닝과 불펜 의존도를 추가해 실제 Python으로 30개 후보를 비교했다. '
        '검증에서 동결한 primary는 평가198경기에서 적중 수가 같고 손익만 5,300원 개선됐다. '
        '확률 오차와 최대낙폭은 악화됐다. 실질적인 우월성이나 미래 수익을 입증한 결과가 아니다.', '',
        '## 고정한 비교와 수행 범위', '',
        '- train: 2023-04-01~2023-11-13, 710경기. validation: 2024-03-23~2024-10-28, 712경기.',
        '- 2023 학습 → 2024 선택 → 2023+2024의 1,422경기 재학습 → 2026-03-29~2026-07-23의198경기 평가.',
        '- 기존203경기 중2026-07-26 이후 과거28일 투수자료가 부족한5경기를 뺀 선행 연구198경기를 유지했다. '
        '이번 연구 안에서는 모든 후보가 같은 경기의100%를 매수한다. 원래203경기 대비97.536946%다.',
        '- 평가 집합은 여러 기존 연구에서 본 회고 집합이다. 일자 기준 인과성만 보장하며 정확한 자료 공개 시각·매수 시각·당시 거래 가능성을 인증하지 않았다.',
        '- 비교용9:1은 동결 연구의 Shin 시장확률90%+픽스터 투표비율10%다. 운영투표모형 재현이라고 부르지 않는다. '
        '과거2023·2024에 픽스터 자료가 없어 선택 기준은 시장 단독이다. 평가에서 기준선 가용성이 바뀌는 한계가 있다.',
        '- `market_null`은 평가에서도 순수 시장이다. 새로운 MLB230 시장 단독 검사와 이 KBO 비교를 섞지 않는다.',
        '- 단폴1만원, 월 손실 제한 없음, 확률0.5 이상이면 홈. 무승부는 환불하고 적중률·확률오차에서만 제외한다. '
        '이번 고정 cohort에는 무승부가0경기다. 환불 분기는 별도 Python 테스트로 검증했다.', '',
        '## 평가 전 동결 증거', '',
        '`PROTOCOL.md`를 최초88100396에서 커밋했고, 기존9:1과 맞추기 위한 Shin 사전 정정을ddf60a4a에서 기록했다. '
        '첫 freeze는 원천 ID 검사에서 중단됐다. Naver 합성연도 ID와 올스타 처리만 명시적으로 보완한 뒤 '
        '원자료·검증확률·모든 학습계수·선택을46e0c622에서 커밋하고 그 뒤에2026평가를 실행했다. '
        '평가 후 후보·변수·기간·계수·선택을 변경하지 않았다. 이후 추가한 것은 모든 후보의 기간별 출력과 회귀검사다.', '',
        f'- 선택 동결 파일 SHA256(LF): `{result["frozen_selection_sha256"]}`.',
        f'- primary: `{heads["primary"]}`.',
        f'- hit_leader / profit_leader: 둘 다 `{heads["hit_leader"]}`.',
        f'- validation eligible 후보 수: {len(heads["eligible"])}개. 기준선의 적중률과 ROI가 모두 비악화인 후보에서 '
        'logloss→Brier→이름 순으로 primary를 선택했다. 별도 head의 tie 규칙도 프로토콜대로 적용했다.',
        '- `validation_predictions.jsonl` 712행에 날짜·점수·가격·가격순서·시장확률·30개 후보 전체 확률을 남겼다. '
        '검증 주를 재표본하는 선택 안정성 검사용이며 평가 결과로 재선택하지 않는다.', '',
        '## 결과', '',
        metric_line('검증 기준 시장', candidates['baseline']['validation']),
        metric_line('검증 primary', primary['validation']),
        metric_line('검증 hit_leader / profit_leader', candidates[heads['hit_leader']]['validation']), '',
        '| 기간 | 기준/동결 역할 | 적중 | 적중률 | ROI | 손익(원) | logloss | Brier | 기준 대비 픽 변경 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|',
    ]
    for period, label in [('all', '전체198'), ('before_july', '7월1일 전152'), ('july_onward', '7월1일 이후46')]:
        p = result['periods'][period]
        for role, m, changes in [('9:1 기준', p['baseline'], 0)] + [
                (role, p['heads'][role]['metrics'], p['heads'][role]['changes_vs_baseline'])
                for role in ['primary', 'hit_leader', 'profit_leader']]:
            lines.append(f'| {label} | {role} | {m["wins"]}/{m["settled"]} | {pct(m["accuracy"])} | '
                         f'{pct(m["roi"])} | {m["net_krw"]:,} | {m["logloss"]:.9f} | {m["brier"]:.9f} | {changes} |')
    allp = result['periods']['all']
    lines += ['',
        'primary는 전체에서14경기의 방향을 바꿨다. 기준선 대비 적중 수 차이는0승, ROI 차이는+0.267677%p다. '
        '7월 전에는1승·8,800원 나빠지고7월 이후에는1승·14,100원 좋아졌다. '
        '전체 logloss는0.661282209→0.663261630, Brier는0.234604708→0.235501970으로 악화됐다. '
        '일마감 최대낙폭도157,700원→210,200원으로 커졌다. 이 정도의 차이로 개선을 확정하지 않는다.', '',
        '검증 hit_leader와 profit_leader는 같은 후보지만 역할을 각각 보존했다. 평가에서19경기를 바꿨으며 '
        '기준보다1승 적고8,900원 더 손실이 났다. 더 좋아 보이는 다른 평가 후보로 바꾸지 않았다.', '',
        metric_line('평가 순수시장 market_null (9:1과 구분)', candidates['market_null']['evaluation']), '',
        '### 같은 경기의 주 단위 paired bootstrap', '',
        '월요일 시작 주를 블록으로 묶어5,000회 복원추출했다(seed20261029). 아래는 후보-9:1 차이의95% percentile 구간이다. '
        '전체16주·7월 전12주·이후4주뿐이며, 다중후보 선택·이미 본 회고 자료의 편향을 제거하는 구간이 아니다.', '',
        '| 기간 | 역할 | 적중률 차이 구간(%p) | ROI 차이 구간(%p) |', '|---|---|---:|---:|',
    ]
    for period in ['all', 'before_july', 'july_onward']:
        for role in ['primary', 'hit_leader', 'profit_leader']:
            ci = result['periods'][period]['heads'][role]['paired_week_bootstrap']
            lines.append(f'| {period} | {role} | [{100*ci["accuracy_delta95"][0]:.6f}, {100*ci["accuracy_delta95"][1]:.6f}] '
                         f'| [{100*ci["roi_delta95"][0]:.6f}, {100*ci["roi_delta95"][1]:.6f}] |')
    lines += ['', '전체 primary의 적중률·ROI 구간이 모두0을 포함한다. 이 실험으로 통계적으로 입증된 최적해나 미래 수익을 주장할 수 없다.', '',
        '### 전체 후보 성적 — 선택은 검증만 사용', '',
        '아래 전체30개는 기준선·market_null과28개 연구후보다. 각 후보의 검증·최종계수, 기간별 평가, 월별 손익, '
        '픽 변경 수는 `results.json`에 보존했다. 표의 평가 성적은 사후 재선택 근거가 아니다.', '',
        '| 후보 | 검증 적중률 | 검증 ROI | 검증 logloss | 평가 적중률 | 평가 ROI | 평가 픽 변경 |',
        '|---|---:|---:|---:|---:|---:|---:|',
    ]
    for name, c in candidates.items():
        v, e = c['validation'], c['evaluation']
        lines.append(f'| `{name}` | {pct(v["accuracy"])} | {pct(v["roi"])} | {v["logloss"]:.9f} | '
                     f'{pct(e["accuracy"])} | {pct(e["roi"])} | {c["evaluation_changes_vs_baseline"]} |')
    lines += ['', '## KBO 설계 이유와 계산식', '',
        '10개 구단의 같은 리그 안에서 시즌 득점환경과 팀 공격·실점의 상대적 수준을 추정한다. '
        '2023학습 cohort의 팀당 점수 평균은4.624648, 분산은10.799252였고, 재학습1,422경기의 평균은4.990506, 분산은12.079727이었다. '
        '이 원자료의 과산포 때문에 포아송만 가정하지 않고 음이항 후보를 비교했다. MLB에서 얻은 계수·분산을 가져오지 않았다.', '',
        '- 리그 평균 L=`(과거 시즌 양팀 득점합+20×4.5)/(과거 팀경기수+20)`. '
        '팀 O/D=`(득점 또는 실점합+s×L)/(경기수+s)`. 시즌과 최근28일을 별도로 계산한다.',
        '- 기본 전력 특징은 시즌/최근28일 `log(O_h/D_h)-log(O_a/D_a)`, 홈/원정 득실차의 수축값 차이, '
        '쉬는 날수 차이, 최근7일 경기수 차이다. 동일 날짜 경기는 모두 제외하며, 시즌 전적0개이면 명시한 사전값을 쓴다.',
        '- 과거 팀 전체 타격의 `OPS*=(H+BB+32)/(AB+BB+100)+(H+2B+2×3B+3×HR+40)/(AB+100)`를 시즌/28일로 만든다. '
        'HBP·SF가 없으므로 정식 OPS가 아니다. 당일 타순이나 발표되지 않은 당일 선발을 추측하지 않는다.',
        '- 투수 원천 선수pcode로 과거 첫 투수를 선발, 그 뒤를 구원으로 나눈다. '
        '28일 선발outs를5경기×15outs로 수축하고, `의존도=clip(1-수축선발outs/27,0,1)`와 '
        '`3일구원outs/27`을 곱한다. `(K-BB+9)/(구원outs+45)`와 과거 선발 평균 이닝도 별도 특징으로 쓴다. BF는 쓰지 않는다.',
        '- 로지스틱12개는4묶음×ridge{.01,.1,1}. `logit(p)=logit(reference)+clip(x/train_std,-6,6)β`. '
        '절편 없이 평균logloss+ridge×||β||²/2를 최소화한다.',
        '- 음이항8개는수축20/60×시즌/28일×blend.1/.3. '
        '`mu_h=O_h D_a/L exp(H)`, `mu_a=O_a D_h/L exp(-H)`. H와과산포r는2023 또는2023+24 학습점수에서만 추정한다. '
        '`Var=mu+mu²/r`, `P(home|non-draw)=P(R_h>R_a)/(P(R_h>R_a)+P(R_a>R_h))`. '
        '0..200 PMF를 합산하고tail>1e-8이면 중단한다. 최종 점수의 독립근사라 이닝·연장·홈말 종료 구조를 재현하지 않는다.',
        '- Pythagorean8개는수축20/60×지수1.5/2×blend.1/.3. 위 시즌 기대득점의 비에 '
        '`logistic(exponent×log(mu_h/mu_a))`를 적용한다. 분포형 후보는 `logistic((1-w)logit(reference)+w logit(model))`로 혼합한다.', '',
        '검증 전 음이항r=3.463763660, H=0.014081263이었고, 재학습r=3.513101718, H=0.005218165였다. '
        'NB/Pythagorean 계열은 primary로 선택되지 않았다. 결과를 보고 이 계열의 수축·지수·비중을 다시 탐색하지 않았다.', '',
        '### primary 최종계수', '',
        '`β`는 표준편차로 나눈 값에 곱한다. 절편·평균중심화는 없다. 음수 계수도 그대로 보존하며 인과효과로 해석하지 않는다.', '',
        '| 특징 | 학습 표준편차 | β |', '|---|---:|---:|',
    ]
    f = primary['final_fit']
    for name, scale, beta in zip(f['names'], f['scale'], f['beta']):
        lines.append(f'| `{name}` | {scale:.12f} | {beta:.12f} |')
    lines += ['', '## 원자료와 source guards', '',
        '원자료는 선행 미병합 worktree에서 읽기만 한 뒤 이 연구 디렉터리로 복사했다. '
        'KBO 행의 lossless JSONL 추출본을 gzip(mtime0)으로 보존했다. 타자는 원천 JSON의 각 key와 raw 객체 전체를 유지했다. '
        '원본 경로와 바이트SHA256은 `input-manifest.json`, 실행 입력해시는 `results.json`에 있다. '
        '최종 실행은 공유 폴더·선행 worktree·네트워크·DB를 읽을 필요가 없다.', '',
        '- 투수2,656경기(2023:724/2024:728/2025:733/2026:471), 원장 점수2,879행, 원천 타자2,896경기를 보존했다. '
        '투수id와 날짜·팀·점수가 맞는 타자2,656경기를 특징 원천으로 썼다.',
        '- 날짜/팀 기준 score anchor는 투수gameId와 대조한다. 더블헤더14개 추가 경기는 고유id로 보존한다. '
        'KBO10개 구단이 아닌 올스타4경기는 리그 환경에서 빼고 원본에는 보존했다. '
        '합성연도 접두어를 쓰는 포스트시즌/올스타 ID 50건은 월일과 말미의 실제 연도를 확인했다.',
        '- 통합 점수사실은2,913경기(2023:723/2024:728/2025:736/2026:726)다. '
        '투수 기록에 없는 고유 점수경기는 원장에서 추가했다. 2025사실도 보존하지만 현재 특징은 같은 시즌만 쓰므로2026예측에2025를 이월하지 않는다.',
        '- 투수 허용실점 합계=상대 점수, 선수id/비음수 정수outs, 타자H≤AB 및2B+3B+HR≤H, '
        '날짜/팀/점수 연결, 고유id, 배당과 순서를 검사한다. 원천이 손상되면 실험을 중단한다.',
        '- 모든 고정 cohort에서 알려진 과거28일 점수원장의 관련 경기에 투수원본이 있는지 검사했다. '
        '누락을 불펜0이나 휴식으로 만들지 않았다. 기대 원장 자체가 알려주지 않는 누락까지 찾아냈다고 주장하지 않는다.',
        '- 실제 고정 cohort의 모든 팀 관측에서 시즌 누적 타격과 점수 이력의 경기수가 일치했다. '
        '이 사실만으로 원천 전체의 완전성을 인증하지는 않는다. 표본 개수와 원천 ID를 '
        '`features.jsonl.gz`의 proof와 아래 진단에 보존했다. 후보별로 다른 subset을 고르지 않았다.', '',
        '| 역할 | 경기전 L 최소~최대 | 팀 과거경기 최소~최대 | 시즌 score경기수-타격경기수 차이: 팀관측수 |',
        '|---|---|---|---|',
    ]
    for role, d in diagnostics.items():
        lines.append(f'| {role} | {d["league_runs_min"]:.6f}~{d["league_runs_max"]:.6f} | '
                     f'{d["team_game_count_min"]}~{d["team_game_count_max"]} | '
                     f'{json.dumps(d["team_season_score_minus_batting_game_counts"], ensure_ascii=False)} |')
    lines += ['', '실행 입력 SHA256(gzip 바이트):', '']
    for name, sha in manifest['committed_inputs'].items():
        lines.append(f'- `{name}`: `{sha}`')
    lines += ['', '## 재실행과 검사', '',
        f'실행 환경: Python{result["versions"]["python"]}, NumPy{result["versions"]["numpy"]}, SciPy{result["versions"]["scipy"]}. '
        '명령은 저장소 루트에서 실행한다. `prepare`는 원본 이입용이며 재현에는 필요 없다.', '',
        '```powershell',
        'python scripts/kbo_retune_research.py evaluate',
        'python -m unittest discover -s tests -p test_kbo_retune_research.py -v',
        'python scripts/kbo_retune_research_report.py',
        '```', '',
        '재선택 없이 `frozen-selection.json`과 커밋한 입력으로 결과를 재생한다. 처음부터 2023학습/2024선택도 재현하려면 '
        '별도 빈 연구 디렉터리에 커밋한 `sources/`, `input-manifest.json`, `PROTOCOL.md`만 복사하고 '
        '`python scripts/kbo_retune_research.py freeze --folder <복사경로>`와 `evaluate --folder <복사경로>`를 순서대로 실행한다. '
        '기존 선택파일을 덮어쓰는 freeze는 거부한다. 텍스트 해시는 Git의 Windows 줄바꿈 차이만 LF로 정규화한다.', '',
        '회귀 검사는 고정표본·모든 검증확률 재생·원 단위 독립정산·가격순서·환불·Shin 기준 일치·선택규칙·'
        '학습에 2026자료 거부·평가점수 변형 시 학습계수 불변·음이항 조건부확률·과거자료 민감도·'
        '당일/미래 원천 타자와 투수 변형 시 특징 불변·과거 원본 누락/실점 훼손 거부를 실제 실행한다. '
        '최종 재현 로그는 `verification.json`에 기록한다.', '',
        '## 검토한1차 근거와 한계', '',
        '- [KBO 공식 팀 투수 기록](https://www.koreabaseball.com/Record/Team/Pitcher/Basic1.aspx): '
        'IP·SO·BB·R·ER의 구분을 확인했다. 공식 페이지의 새 수치가 이번 입력이라고 주장하지 않는다.',
        '- [Sean Dolinar(2014), Run Distribution Using the Negative Binomial Distribution](https://community.fangraphs.com/run-distribution-using-the-negative-binomial-distribution/): '
        '저자 자신의 MLB 점수 분석에서 과산포를 다루는 음이항 접근과 무득점 과소추정의 한계를 검토했다. '
        '이번 분산은 KBO 학습자료에서 다시 구했다.',
        '- [Steven J. Miller, A Derivation of the Pythagorean Won-Loss Formula in Baseball](https://arxiv.org/abs/math/0509698): '
        '득실점 비율의 이론과 독립성·분포 가정을 참고했다. KBO에 그대로 맞는다는 증거는 아니다.', '',
        '정확한 매수 시점·자료 공개 시점·원천 전체의 완전성은 인증하지 못했다. 과거 타격 합계가 당일 선발 타순을 대신하지 못하고, '
        '과거 선발 평균 이닝도 당일 선발의 예고 정보가 아니다. 작은 평가 표본, 특히 7월 이후 4주와 후보 선택의 불안정성을 고려해야 한다. '
        '검증 logloss 선두 두 후보의 차이도 매우 작다. 운영 채택·정책 변경·배포·merge는 하지 않았다.', '',
    ]
    path = ROOT/'docs/research/2026-10-29-kbo-retune.md'
    path.write_text('\n'.join(lines), encoding='utf8', newline='\n')
    print(str(path))


if __name__ == '__main__':
    main()
