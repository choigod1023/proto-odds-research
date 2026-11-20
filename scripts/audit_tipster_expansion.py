"""Retrospective audit. No production writes, optimization or promotion.

Identity joins use dates and team names BEFORE score consistency checks.
Backfilled article timestamps are not certified pre-event observations.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import numpy as np
from sport_hybrid_validation import load_data, fit, predict, SPORT_FEATURES
from matches import load_matches
from accuracy_formula_lab import apply_design

ARMS = ('market', 'research', 'tipster', 'market90_tipster10', 'research90_tipster10')


def norm(text):
    return re.sub(r'[^a-z0-9가-힣]', '', str(text).lower())


def fixture_key(r):
    return r['sport'], r['date'], r['home'], r['away']


def article_date(title):
    m = re.search(r'(202\d)년\s*(\d{1,2})월\s*(\d{1,2})일', title)
    return f'{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}' if m else None


def article_leagues(title):
    labels = {norm(x) for x in re.findall(r'\[([^]]+)\]',title)}
    mapping = {'mlb':{'MLB'}, 'kbl':{'KBL'}, 'v리그':{'KOVO남','KOVO여'},
               '프리미어리그':{'EPL'}, 'epl':{'EPL'}, 'k리그1':{'K리그1'}, 'k리그2':{'K리그2'}}
    return set().union(*(mapping.get(x,set()) for x in labels))


def team_matches(text, team, aliases):
    n = norm(text)
    return any(norm(a) in n for a in [team, *aliases.get(team, [])] if len(norm(a)) >= 2)


def identity_candidates(article, same_day_official, aliases):
    leagues = article_leagues(article['title'])
    return [r for r in same_day_official if r['league'] in leagues
            and team_matches(article['title'],r['home'],aliases)
            and team_matches(article['title'],r['away'],aliases)]


def parse_tip(article, home, away, aliases, sport):
    text = article.get('pick') or ''
    if 'body' in article:
        match = re.search(r'승무패\s*[:：]\s*(.*?)(?=(?:핸디캡|언더오버)\s*[:：]|$)',article['body'])
        text = match.group(1).strip() if match else ''
    if re.search(r'핸디캡|언더|오버|플핸|마핸',text):
        return None
    if re.search(r'아님|아니|않|무패|더블찬스|이중기회|승무|무패|가능성.*낮',text):
        return None
    if not text:
        return None
    h = team_matches(text, home, aliases)
    a = team_matches(text, away, aliases)
    draw = bool(re.search(r'무승부|^무(?:\s|$)', text))
    if sum((h, a, draw)) != 1:
        return None
    if draw:
        return 1 if sport == 'sc' else None
    win = bool(re.search(r'승리|승(?=\s|$|[).,])',text))
    lose = bool(re.search(r'패배|패(?=\s|$|[).,])',text))
    if win == lose:
        return None
    selected_home = h if win else a
    return 0 if selected_home else (2 if sport == 'sc' else 1)


def probabilities(market, research, tip):
    m, p = np.asarray(market, float), np.asarray(research, float)
    if len(m) != len(p) or tip not in range(len(p)):
        raise ValueError('invalid dimensions or tip')
    for v in (m, p):
        if not np.isfinite(v).all() or (v < 0).any() or not np.isclose(v.sum(), 1):
            raise ValueError('invalid probabilities')
    vote = np.eye(len(p))[tip]
    return {'market': m.tolist(), 'research': p.tolist(),
            'market90_tipster10': (.9*m+.1*vote).tolist(),
            'research90_tipster10': (.9*p+.1*vote).tolist()}


def stats(rows, arm):
    returns = [r['odds'][r['picks'][arm]] * (r['picks'][arm] == r['winner']) - 1 for r in rows]
    result = dict(n=len(rows), hits=sum(r['picks'][arm] == r['winner'] for r in rows),
                  accuracy_pct=100*np.mean([r['picks'][arm] == r['winner'] for r in rows]) if rows else None,
                  roi_pct=100*np.mean(returns) if rows else None,
                  profit_krw=round(10000*sum(returns)))
    if arm != 'tipster' and rows:
        result['brier'] = float(np.mean([sum((np.asarray(r['probabilities'][arm])-np.eye(len(r['odds']))[r['winner']])**2) for r in rows]))
        result['log_loss'] = float(np.mean([-np.log(max(1e-15, r['probabilities'][arm][r['winner']])) for r in rows]))
    return result


def capped(rows, arm):
    days, monthly, selected = defaultdict(list), defaultdict(int), []
    for r in rows:
        days[r['date']].append(r)
    for day, batch in sorted(days.items()):
        chosen = []
        # Freeze selection before outcomes: kickoff, then fixture identity.
        for r in sorted(batch, key=lambda x: (x['kickoff'], fixture_key(x))):
            if monthly[day[:7]] - 10000*(len(chosen)+1) >= -50000:
                chosen.append(r)
        monthly[day[:7]] += stats(chosen, arm)['profit_krw']
        selected.extend(chosen)
    assert all(v >= -50000 for v in monthly.values())
    values = list(monthly.values())
    return dict(**stats(selected, arm), monthly_net=dict(monthly),
                profitable_months=sum(v > 0 for v in values), losing_months=sum(v < 0 for v in values),
                worst_month=min(values) if values else None,
                assumption='10000 KRW singles, same-day worst-case exposure reserved, next-day settlement, no weekly cap')


def paired_interval(rows, challenger, base='research', repeats=2000):
    """Paired day-block bootstrap: descriptive, not independent holdout proof."""
    by_day = defaultdict(list)
    for r in rows:
        def ret(arm):
            pick = r['picks'][arm]
            return r['odds'][pick]*(pick == r['winner'])-1
        by_day[r['date']].append(ret(challenger)-ret(base))
    if len(by_day) < 2:
        return None
    blocks = list(by_day.values())
    sums = np.array([sum(x) for x in blocks]); sizes = np.array([len(x) for x in blocks])
    rng = np.random.default_rng(20261026)
    draws = rng.integers(0, len(blocks), size=(repeats, len(blocks)))
    deltas = 100*sums[draws].sum(1)/sizes[draws].sum(1)
    return dict(roi_difference_pp=float(100*sums.sum()/sizes.sum()),
                descriptive_95_interval_pp=np.quantile(deltas,[.025,.975]).tolist(), day_blocks=len(blocks))


def factor_tags(body):
    tags = {'starter':'선발|투수', 'availability':'결장|부상|복귀', 'rotation':'로테이션|교체|벤치',
            'recent_form':'최근|연승|연패', 'home_away':'홈|원정', 'rest':'휴식|백투백|일정',
            'tactics':'전술|포메이션|블로킹|수비|공격', 'odds':'배당'}
    return [k for k, pattern in tags.items() if re.search(pattern, body)]


def run(args):
    aliases = json.loads(args.aliases.read_text(encoding='utf8'))
    official = []
    for path in args.official:
        obj = json.loads(path.read_text(encoding='utf8'))
        official.extend(obj if isinstance(obj,list) else obj['rows'])
    articles = {}
    for path in args.articles:
        for r in json.loads(path.read_text(encoding='utf8')):
            articles[r['url']] = r
    excluded = set()
    for path in args.previous:
        excluded.update(fixture_key(r) for r in json.loads(path.read_text(encoding='utf8'))['ledger'])
    by_date = defaultdict(list)
    for r in official:
        by_date[r['date']].append(r)
    rejected = []; candidates = defaultdict(list)
    for a in sorted(articles.values(),key=lambda r:r['url']):
        day = article_date(a['title'])
        hits = identity_candidates(a,by_date[day],aliases)
        if len(hits) != 1:
            rejected.append(dict(url=a['url'], reason='article_official_identity', candidates=len(hits)))
            continue
        r = hits[0]
        published = a.get('displayed_published')
        try:
            claimed = datetime.fromisoformat(published).replace(tzinfo=timezone(timedelta(hours=9)))
            kickoff = datetime.fromisoformat(r['kickoff'])
            before = claimed < kickoff
        except (TypeError,ValueError):
            before = False
        if not before:
            rejected.append(dict(url=a['url'],reason='missing_or_not_before_claimed_publication_KST'))
            continue
        if fixture_key(r) in excluded:
            rejected.append(dict(url=a['url'], reason='previous_cohort'))
            continue
        tip = parse_tip(a,r['home'],r['away'],aliases,r['sport'])
        if tip is None:
            rejected.append(dict(url=a['url'], reason='ambiguous_or_missing_moneyline_tip'))
            continue
        candidates[fixture_key(r)].append((r,a,tip))
    data, quality = load_data(args.games)
    match_keys = ['kickoff','sport','league','home_team','away_team']
    scores = load_matches(path=args.games)[match_keys+['home_score','away_score']]
    data = data.merge(scores,on=match_keys,validate='one_to_one')
    print('loaded data',len(data),'candidate fixtures',len(candidates),flush=True)
    data_index = defaultdict(list)
    for idx,r in data.iterrows():
        data_index[(r.sport,str(r.date)[:10],r.home_team,r.away_team,r.league)].append(idx)
    models = {}; training = {}; ledger = []
    for key, group in sorted(candidates.items()):
        # Syndicated duplicates do not count as independent games/voters.
        if len({t for _,_,t in group}) != 1:
            rejected.append(dict(fixture=list(key),reason='conflicting_duplicate_article_votes'))
            continue
        r,a,tip = sorted(group,key=lambda x:x[1]['url'])[0]
        indices = data_index[(*key,r['league'])]
        if len(indices) != 1:
            rejected.append(dict(fixture=list(key),reason='nonunique_or_missing_modeled_offer',count=len(indices)))
            continue
        frame = data.loc[indices]; h = frame.iloc[0]; s = r['sport']
        hs, aws = int(r['home_score']),int(r['away_score'])
        winner = 0 if hs > aws else (1 if hs == aws or s != 'sc' else 2)
        if int(h.y) != winner or int(h.home_score) != hs or int(h.away_score) != aws:
            rejected.append(dict(fixture=list(key),reason='official_score_conflict',official=[hs,aws],local=[float(h.home_score),float(h.away_score)]))
            continue
        if s not in models:
            train = data[(data.sport == s) & (data.year < 2025)]
            cols = list(dict.fromkeys(['elo_diff',*SPORT_FEATURES[s]]))
            models[s] = fit(train,cols,offset=True)
            state, weights, _ = models[s]
            training[s] = dict(n=len(train),last_date=str(train.date.max()),columns=cols,
                               medians=list(state.medians),scales=list(state.scales),weights=weights.tolist())
        p = predict(models[s],frame)[0]
        probs = probabilities(h.market,p,tip)
        picks = {name:int(np.argmax(value)) for name,value in probs.items()}; picks['tipster'] = tip
        picks['market90_tipster10'] = int(np.argmax(np.round(probs['market90_tipster10'],4)))
        state, weights, _ = models[s]
        design = apply_design(frame,state)[0]
        names = ['intercept'] + [name for col in state.columns for name in (col,col+'_missing')]
        contrast = design*(weights[:,picks['research']]-weights[:,tip])
        explanation = dict(market_log_odds_advantage=float(np.log(h.market[picks['research']]/h.market[tip])),
                           feature_logit_advantages={name:float(value) for name,value in zip(names,contrast)},
                           note='Model decomposition only; article factor tags are mentions, not verified causes or an ablation experiment.')
        ledger.append(dict(**r, article_url=a['url'], article_title=a['title'],
                           article_body_sha256=a['body_sha256'], article_displayed_published=a.get('displayed_published'),
                           observed_at=a['observed_at'], article_pick=a['pick'], tip=tip,
                           article_author=a.get('author'), claimed_pregame_assuming_KST=True,
                           source_duplicates=[x[1]['url'] for x in group], factors=factor_tags(a['body']),
                           quality_flags=['non_sport_formation'] if s=='vl' and re.search(r'4-2-3-1|5-1-4',a['body']) else [],
                           odds=list(h.odds), archived_kickoff=str(h.kickoff), winner=winner, probabilities=probs,picks=picks,
                           disagreement_explanation=explanation))
    assert len({fixture_key(r) for r in ledger}) == len(ledger)
    assert not {fixture_key(r) for r in ledger} & excluded
    sports = {}
    for s in ('bs','bk','sc','vl'):
        rows = [r for r in ledger if r['sport']==s]
        different = [r for r in rows if r['picks']['research'] != r['tip']]
        agreed = [r for r in rows if r['picks']['research'] == r['tip']]
        changes = [r for r in rows if r['picks']['research'] != r['picks']['research90_tipster10']]
        sports[s] = dict(new_joined=len(rows), target_met=len(rows)>=100,
                         models={arm:stats(rows,arm) for arm in ARMS},
                         monthly_cap={arm:capped(rows,arm) for arm in ARMS},
                         tipster_disagrees_research=len(different), research_hybrid_changed=len(changes),
                         all_three_same_pick=len(agreed),
                         tipster_agrees_market=sum(r['tip']==r['picks']['market'] for r in rows),
                         same_pick_metrics={arm:stats(agreed,arm) for arm in ('research','research90_tipster10')},
                         average_confidence_boost_pp_on_agreement=float(np.mean([100*(r['probabilities']['research90_tipster10'][r['tip']]-r['probabilities']['research'][r['tip']]) for r in agreed])) if agreed else None,
                         switched_helped=sum(r['picks']['research90_tipster10']==r['winner'] for r in changes),
                         switched_harmed=sum(r['picks']['research']==r['winner'] for r in changes),
                         disagreement_models={arm:stats(different,arm) for arm in ARMS},
                         intervals={arm:paired_interval(rows,arm) for arm in ('tipster','research90_tipster10')},
                         factor_presence=dict(Counter(f for r in different for f in r['factors'])))
    result = dict(status='retrospective source audit; no model adoption; historical quote execution times unverified',
                  full_articles=len(articles), new_joined=len(ledger), previous_overlap=0,
                  strict_pregame_eligible=0, target_met=all(v['target_met'] for v in sports.values()),
                  protocol='Fixed 90:10 weights; argmax single pick; no tuning on 2025 outcomes. Official identity then exact score checks. All eligible new matches, not first 100 winners.',
                  baseline='Pre-2025 trained market-offset research reconstruction, NOT archived production prediction',
                  hybrid='research90 and market90 are distinct; market90 analogue is not eligible production replay',
                  source_caveat='Full original bodies cached locally. Editable displayed dates and backfilled actual odds do not certify pre-event availability. Tipster is a vote, not a calibrated probability.',
                  training=training,quality=quality, sports=sports,
                  overall_monthly_cap={arm:capped(ledger,arm) for arm in ARMS},
                  rejected_counts=dict(Counter(r['reason'] for r in rejected)), rejected=rejected,ledger=ledger,
                  hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [args.games,args.aliases,*args.official,*args.articles,*args.previous]})
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({s:{k:v for k,v in d.items() if k in ('new_joined','models','tipster_disagrees_research','research_hybrid_changed')} for s,d in sports.items()},ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    for name in ('games','aliases','output'): ap.add_argument('--'+name,type=Path,required=True)
    for name in ('official','articles','previous'): ap.add_argument('--'+name,type=Path,nargs='+',required=True)
    run(ap.parse_args())
