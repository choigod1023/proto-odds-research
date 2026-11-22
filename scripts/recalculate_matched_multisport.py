"""Independent flat-single arithmetic on linked diagnostic rows.

Does not repair historical source evidence, infer missing player forecasts,
retrain models, select a winner, or alter any bankroll/production state.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.devig import multiplicative, shin


def choice(p):
    if not p or any(not math.isfinite(x) or x < 0 for x in p) or not math.isclose(sum(p), 1, abs_tol=1e-7):
        raise ValueError('invalid probability vector')
    top = max(p)
    winners = [i for i, x in enumerate(p) if math.isclose(x, top, abs_tol=1e-12, rel_tol=0)]
    return winners[0] if len(winners) == 1 else None


def payout(pick, outcome, odds):
    if pick is None:
        return None
    if outcome is None:
        return 0.0
    return odds[pick] - 1 if pick == outcome else -1.0


def close(a, b):
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, abs_tol=1e-8, rel_tol=0)


def score_row(row):
    odds = row['odds']
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) or x <= 1 for x in odds):
        raise ValueError('invalid odds')
    n = len(odds)
    if n not in (2, 3) or len(row['vote_share']) != n:
        raise ValueError('different market dimensions')
    if (row['outcome'] is not None and (type(row['outcome']) is not int or not 0 <= row['outcome'] < n)
            or row['outcome'] is None and row['league'] != 'KBO'):
        raise ValueError('invalid outcome or unsupported refund')
    vote = row['vote_share']
    market = multiplicative(odds)
    market_shin = shin(odds)
    arms = {'vote': choice(vote), 'market_multiplicative': choice(market),
            'market_shin': choice(market_shin),
            'market90_vote10_multiplicative': choice([.9*m + .1*v for m, v in zip(market, vote)]),
            'market90_vote10_shin': choice([.9*m + .1*v for m, v in zip(market_shin, vote)])}
    for name, p in row['models'].items():
        arms[name] = choice(p)
    profits = {name: payout(p, row['outcome'], odds) for name, p in arms.items()}
    for name, claimed in row['claimed_profit'].items():
        if name not in profits or not close(claimed, profits[name]):
            raise ValueError('worker payout differs: ' + row['fixture_id'] + ':' + name)
    for name, claimed in row.get('claimed_selections', {}).items():
        if name not in arms or arms[name] != claimed:
            raise ValueError('worker selection differs: ' + row['fixture_id'] + ':' + name)
    return dict(row, selections=arms, profits=profits)


def normalize_baseball(rows):
    out = []
    side = {'home': 0, 'away': 1, None: None}
    for r in rows:
        models = {m: [p, 1-p] for m, p in r['model_home_probabilities'].items()}
        out.append(dict(fixture_id=r['official_id'], league='KBO', kickoff=r['kickoff'],
                        odds=[r['prices']['home'], r['prices']['away']],
                        outcome=None if r['winner'] == 'draw' else side[r['winner']],
                        vote_share=[r['vote_home_fraction'], 1-r['vote_home_fraction']],
                        models=models, evidence=r['evidence_class'],
                        claimed_selections={'vote': side[r['selections']['vote']],
                                            'market_multiplicative': side[r['selections']['market']],
                                            'market90_vote10_multiplicative': side[r['selections']['market90_vote10_multiplicative']],
                                            **{m: side[r['selections'][m]] for m in models}},
                        claimed_profit={'vote': r['profit']['vote'], 'market_multiplicative': r['profit']['market'],
                                        'market90_vote10_multiplicative': r['profit']['market90_vote10_multiplicative'],
                                        **{m: r['profit'][m] for m in models}}))
    return out


def normalize_soccer(rows):
    out = []
    for r in rows:
        total = sum(r['vote_counts'])
        revisions = r.get('model_revisions', [])
        if total <= 0 or len(revisions) > 1:
            raise ValueError('invalid soccer vote/model revision')
        models = {p['name'] + '_postgame_roster_oracle': p['p'] for p in revisions}
        claimed = {'vote': r['vote_profit'], 'market_multiplicative': r['market_profit'],
                   'market90_vote10_multiplicative': r['market90_vote10_profit']}
        claimed.update({p['name'] + '_postgame_roster_oracle': p['model_profit'] for p in revisions})
        out.append(dict(fixture_id=r['fixture_id'], league=r['league'], kickoff=r['kickoff'],
                        odds=r['odds'], outcome=r['outcome'], vote_share=[v/total for v in r['vote_counts']],
                        models=models, evidence=r['evidence_tier'], claimed_profit=claimed))
        out[-1]['claimed_selections'] = {'vote': r['vote_pick'], 'market_multiplicative': r['market_pick'],
                                        'market90_vote10_multiplicative': r['market90_vote10_pick'],
                                        **{p['name'] + '_postgame_roster_oracle': p['model_pick'] for p in revisions}}
    return out


def normalize_basketball(rows):
    out = []
    for r in rows:
        out.append(dict(fixture_id=r['fixture_id'], league=r['league'], kickoff=r['fixture']['kickoff'],
                        odds=r['price']['odds'], outcome={'home': 0, 'away': 1}[r['outcome']],
                        vote_share=[r['vote_home_share'], 1-r['vote_home_share']], models={}, evidence=r['category'],
                        claimed_selections={a: {'home': 0, 'away': 1, None: None}[r['selections'][b]]
                                            for a, b in [('vote', 'vote'), ('market_multiplicative', 'market'),
                                                         ('market90_vote10_multiplicative', 'multiplicative_9_1')]},
                        claimed_profit={'vote': r['unit_profit']['vote'], 'market_multiplicative': r['unit_profit']['market'],
                                        'market90_vote10_multiplicative': r['unit_profit']['multiplicative_9_1']}))
    return out


def normalize_baseball_2025(rows):
    out = []
    side = {'home': 0, 'away': 1, None: None}
    for r in rows:
        if not r['kickoff_kst'].startswith('2025-'):
            raise ValueError('supplement is not 2025')
        if r['selections'].get('named_author_source') not in ('home', 'away'):
            raise ValueError('supplement needs an explicit source selection')
        models = {r['model_identity'][m]['model_id']: [p['home'], p['away']]
                  for m, p in r['model_probabilities'].items()}
        aliases = {'vote': 'named_author_source', 'market_multiplicative': 'market',
                   'market90_vote10_multiplicative': 'market90_source10_multiplicative',
                   **{r['model_identity'][m]['model_id']: m for m in r['model_probabilities']}}
        vh = float(r['selections']['named_author_source'] == 'home')
        out.append(dict(fixture_id=r['official_game_id'], league='KBO', kickoff=r['kickoff_kst'],
                        odds=[r['prices']['home'], r['prices']['away']], outcome=side[r['official_winner']],
                        vote_share=[vh, 1-vh], models=models,
                        evidence='supplementary_publication_claim_postgame_roster_oracle',
                        claimed_selections={a: side[r['selections'][b]] for a, b in aliases.items()},
                        claimed_profit={a: r['profit_units'][b] for a, b in aliases.items()}))
    return out


def summarize(rows, arms):
    result = {}
    for arm in arms:
        active = [r for r in rows if r['selections'].get(arm) is not None]
        decided = [r for r in active if r['outcome'] is not None]
        wins = sum(r['selections'][arm] == r['outcome'] for r in decided)
        net = sum(r['profits'][arm] for r in active)
        result[arm] = dict(bets=len(active), decided=len(decided), hits=wins,
                           refunds=len(active)-len(decided), abstained=len(rows)-len(active),
                           hit_rate=wins/len(decided) if decided else None,
                           net_units=round(net, 8), roi=net/len(active) if active else None)
    return result


CONTROLS = ['vote', 'market_multiplicative', 'market_shin',
            'market90_vote10_multiplicative', 'market90_vote10_shin']


def markdown(result):
    labels = {'vote': '원문 투표', 'market_shin': '시장(Shin)',
              'market90_vote10_shin': '시장(Shin) 90%·투표 10%',
              'kbo-actual-training-2026-M0_actual': '실제 타순 학습 M0_actual',
              'kbo-actual-training-2026-M2_actual': '실제 타순 학습 M2_actual',
              'kbo-player-effects-v2-2026-M0': '별도 projected-training M0',
              'kbo-player-effects-v2-2026-M1': '별도 projected-training M1',
              'kbo-player-effects-v2-2026-M2': '별도 projected-training M2',
              'kbo-actual-training-2025-M0_actual': '2025 실제 타순 학습 M0_actual',
              'kbo-actual-training-2025-M2_actual': '2025 실제 타순 학습 M2_actual',
              'league_specific_postgame_roster_oracle': '리그별 사후 명단 모델'}
    lines = ['# 동일 경기·배당 비교 재계산 — 2026-11-04', '',
             '요청 기간은 2025-01-01~2026-11-04다. **전 기간·전 리그의 경기 전 자료 연결은 미완료**다. '
             '이 보고서는 실제로 연결한 사후 진단만 제시한다. 원문 게시 주장, 사후 명단, 보관 배당을 '
             '독립적으로 입증된 사전 입력으로 바꾸어 부르지 않는다.', '',
             'PR #567은 Python 3.11·3.12 CI와 리뷰 통과 후 2026-11-04 06:18:48 UTC에 병합됐다. '
             'main 커밋은 `fe885b5d06a4be40143f151f461a98ff4529f4a7`다. 이 작업에서 배포하거나 모델을 채택하지 않았다.', '',
             '## 같은 경기의 실제 계산값', '',
             '아래 9:1은 운영과 같은 `src/devig.py` Shin 시장 확률 90%에 원문 작성자 투표 비율 10%를 더한 '
             '고정식이다. 투표 비율은 보정된 적중 확률이 아니다. 운영 전체의 투표자 자격·필터·의사결정 원장 재생도 아니다. '
             '기존 워커의 단순 역배당(multiplicative) 방식은 JSON에 별도 보존했다. 같은 9:1 명칭으로 두 방식을 섞지 않는다.', '',
             '모든 수치는 경기당 1단위 단폴 손익이다. ROI=순손익/실제 베팅 원금, 적중률=적중/승패 확정 베팅 수다. '
             '동률 예측은 기권, 야구 2방향 무승부는 환급이다. 같은 비교표에서는 모든 전략이 베팅 가능한 공통 경기만 사용했다. '
             '월 -5만원 모의원장은 수정하지 않았고, 아래 계산은 그 원장의 수익이 아니다.', '']
    specs = [('baseball/player_intersection', 'KBO: 선수모델 공통 교집합'),
             ('soccer/player_intersection', 'K리그: 사후 명단 모델 공통 교집합'),
             ('soccer/2026/controls', 'K리그 2026: 선수모델을 요구하지 않은 대조군'),
             ('KBL/2026/controls', 'KBL 2026: 대조군만'), ('NBA/2026/controls', 'NBA 2026: 대조군만'),
             ('soccer/2025/controls', 'K리그 2025: 새 원문 복구의 탐색 표본')]
    view = dict(result['cohorts'])
    supplement = result.get('supplemental_baseball_2025')
    if supplement:
        view['baseball_2025_supplement'] = supplement['cohorts']['baseball/player_intersection']
        specs.append(('baseball_2025_supplement', 'KBO 2025: 별도 원문 복구 사례(주 비교와 합산하지 않음)'))
    for key, title in specs:
        if key not in view:
            continue
        cohort = view[key]
        lines += ['### ' + title, '',
                  f"{cohort['common_bet_fixtures']}경기, {cohort['period'][0]}~{cohort['period'][1]}. 기간의 모든 경기를 뜻하지 않는다.", '',
                  '| 방식 | 적중/확정 | 적중률 | 순손익(단위) | ROI |', '|---|---:|---:|---:|---:|']
        for name, m in cohort['methods'].items():
            if name in ('market_multiplicative', 'market90_vote10_multiplicative'):
                continue
            label = labels.get(name, name)
            hit = f"{100*m['hit_rate']:.2f}%" if m['hit_rate'] is not None else '산출 불가'
            roi = f"{100*m['roi']:+.2f}%" if m['roi'] is not None else '산출 불가'
            lines.append(f"| {label} | {m['hits']}/{m['decided']} | {hit} | {m['net_units']:+.2f} | {roi} |")
        lines.append('')
    lines += ['## 증거와 미완료 범위', '', '원문 수집·상품 오류 수정·명단 시점 감사·2025 누락은 [고정 연구 기록](RESEARCH_CONTEXT.md)에 보존했다. 이 계산은 해당 근거의 품질을 새로 인증하지 않는다.', '']
    return '\n'.join(lines)


def cohorts(rows):
    seen = set()
    for r in rows:
        key = (r['league'], r['fixture_id'])
        if key in seen:
            raise ValueError('duplicate physical fixture: ' + repr(key))
        seen.add(key)
    groups = defaultdict(list)
    for r in rows:
        sport = 'baseball' if r['league'] == 'KBO' else 'soccer' if r['league'] in ('K1', 'K2') else 'basketball'
        groups[sport + '/controls'].append(r)
        groups[sport + '/' + r['kickoff'][:4] + '/controls'].append(r)
        groups[sport + '/' + r['kickoff'][:7] + '/controls'].append(r)
        groups[r['league'] + '/' + r['kickoff'][:4] + '/controls'].append(r)
        if r['models']:
            groups[sport + '/player_intersection'].append(r)
            groups[sport + '/player_intersection/' + r['kickoff'][:7]].append(r)
    out = {}
    for name, group in groups.items():
        model_sets = [set(r['models']) for r in group]
        if name.startswith('soccer/player_intersection'):
            # K1 and K2 have different frozen coefficients; one league-specific
            # postgame oracle arm per fixture, never rename these as KBO M0.
            alias = 'league_specific_postgame_roster_oracle'
            group = [dict(r, selections={**r['selections'], alias: r['selections'][next(iter(r['models']))]},
                          profits={**r['profits'], alias: r['profits'][next(iter(r['models']))]}) for r in group]
            extras = [alias]
        else:
            extras = sorted(set.intersection(*model_sets)) if model_sets else []
        arms = CONTROLS + extras
        common = [r for r in group if all(r['selections'].get(a) is not None for a in arms)]
        out[name] = dict(candidate_fixtures=len(group), common_bet_fixtures=len(common),
                         period=[min(r['kickoff'] for r in group), max(r['kickoff'] for r in group)],
                         evidence_counts=dict(__import__('collections').Counter(r['evidence'] for r in group)),
                         methods=summarize(common, arms))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for sport in ('baseball', 'soccer', 'basketball'):
        p.add_argument('--' + sport, type=Path, required=True)
    p.add_argument('--soccer-2025', type=Path)
    p.add_argument('--baseball-2025', type=Path, help='separate source-curated case; never pool with 2026 player arms')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    rows, inputs = [], {}
    for sport in ('baseball', 'soccer', 'basketball'):
        path = getattr(args, sport); raw = path.read_bytes()
        data = [json.loads(line) for line in raw.splitlines() if line.strip()] if path.suffix == '.jsonl' else json.loads(raw)
        inputs[sport] = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'rows': len(data)}
        rows.extend(globals()['normalize_' + sport](data))
    if args.soccer_2025:
        raw = args.soccer_2025.read_bytes()
        extra = json.loads(raw)['rows']
        if any(not r['kickoff'].startswith('2025-') for r in extra):
            raise ValueError('2025 supplemental cohort has other years')
        inputs['soccer_2025'] = {'path': str(args.soccer_2025), 'sha256': hashlib.sha256(raw).hexdigest(), 'rows': len(extra)}
        rows.extend(normalize_soccer(extra))
    scored = [score_row(r) for r in rows]
    result = {'inputs': inputs, 'cohorts': cohorts(scored),
              'method': 'Flat single stake 1; no monthly cap simulation; no model fitting or promotion. Tie abstains. Two-way baseball draw refunds. Soccer regulation 3way; basketball underlying historical fullgame/OT assumptions remain conditional.',
              'probability_bases': {'multiplicative': 'normalized inverse decimal odds',
                                    'shin': 'pinned repository src/devig.py shin; same market basis as production, not a replay of entire production electorate/filter policy'},
              'code_sha256': hashlib.sha256((ROOT/'src/devig.py').read_bytes()).hexdigest(),
              'strict_pregame_common_evidence_proven': False,
              'production_policy_or_monthly_ledger_modified': False}
    supplemental_count = 0
    if args.baseball_2025:
        raw = args.baseball_2025.read_bytes()
        supplemental_rows = [score_row(r) for r in normalize_baseball_2025(json.loads(raw)['rows'])]
        # Check identities jointly, but keep the different source-selection and
        # annual coefficient sets in a separate comparison population.
        cohorts(scored + supplemental_rows)
        result['supplemental_baseball_2025'] = {
            'input_path': str(args.baseball_2025), 'input_sha256': hashlib.sha256(raw).hexdigest(),
            'cohorts': cohorts(supplemental_rows), 'rows': supplemental_rows,
            'note': 'Individually curated explicit win component; not pooled with conservative 2026 excerpt filter. Author identity unverified; retrospective case only.'}
        supplemental_count = len(supplemental_rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8')
    (args.output.parent/'recalculated-rows.json').write_text(json.dumps(scored, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8')
    (args.output.parent/'REPORT.md').write_text(markdown(result), encoding='utf8')
    print(json.dumps({'verified_main_rows': len(scored), 'supplemental_2025_rows': supplemental_count,
                      'cohorts': len(result['cohorts'])}))


if __name__ == '__main__':
    main()
