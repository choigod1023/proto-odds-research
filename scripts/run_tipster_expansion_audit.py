"""Reproduce local source normalization, exact joins, and descriptive audit."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from scripts.evaluate_tipster_expansion import evaluate
from scripts.join_tipster_expansion import read_rows, join


def build(root, references=()):
    candidates=[]; fixtures=[]; offers=[]; inputs={}
    def load(path):
        inputs[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
        return read_rows(path)
    for sub,file in [('korean','candidates.jsonl'),('overseas','candidates.jsonl'),
                     ('kbl','kbl-candidates.jsonl'),('kbl','nba-candidates.jsonl')]:
        candidates.extend(load(root/sub/file))
    for sub,file in [('korean','outcome_facts.json'),('overseas','official_matched_results.json'),
                     ('kbl','official-kbl-results.jsonl'),('prices','official_results.jsonl'),
                     ('parent','npb-results.json'),('parent','nba-results.json'),('parent','extra-results.json')]:
        fixtures.extend(load(root/sub/file))
    for path in references:
        rows=load(path);fixtures.extend(rows)
        offers.extend(r for r in rows if r.get('odds'))
    offers.extend(load(root/'parent/proto-offers.json'))
    extra=root/'parent/proto-extra-offers.json'
    if extra.exists():offers.extend(load(extra))
    normalized_fixtures=[]
    for f in fixtures:
        r=dict(f)
        r['source_url']=r.get('source_url') or r.get('official_result_url') or r.get('result_source_url') or r.get('outcome_source_url')
        r['home']=r.get('home_name_official') or r.get('home')
        r['away']=r.get('away_name_official') or r.get('away')
        r['kickoff']=r.get('kickoff') or r.get('official_kickoff') or r.get('kickoffKST')
        if not r.get('match_date') and not r.get('date') and r.get('kickoff'):
            r['match_date']=r['kickoff'][:10]
        normalized_fixtures.append(r)
    normalized=[]
    for c in candidates:
        r=dict(c)
        r['source_url']=r.get('source_url') or r.get('url')
        r['period']=r.get('period') or r.get('market_scope')
        r['odds_time_quality']=r.get('odds_time_quality') or r.get('price_evidence_tier')
        if r.get('market')=='total' and not r.get('side'):
            r['side']=r.get('selection')
        if r.get('source') in {'Pickswise','Covers','TonysPicks'} and r.get('market') in {'moneyline','total','spread'}:
            # These collected full-game labels exclude first-five/quarters/player props.
            r['period']='full_game_including_overtime'
        normalized.append(r)
    joined=join(normalized,normalized_fixtures,offers)
    result=evaluate(joined)
    result['inputs']=inputs
    result['source_collection_counts']=dict(Counter(r['source'] for r in candidates))
    result['candidate_count_by_league']=dict(Counter(r['league'] for r in candidates))
    (root/'joined.json').write_text(json.dumps(joined,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    (root/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('experiments/tipster-roi-expansion-20261028'))
    p.add_argument('--reference',type=Path,nargs='*',default=[])
    a=p.parse_args();r=build(a.root,a.reference)
    print(json.dumps(dict(counts=r['source_collection_counts'],settled=r['settled'],
                         sources={k:{f:v for f,v in d.items() if f not in {'monthly_capped','dates'}}
                                  for k,d in r['groups']['source'].items()},
                         rejected=r['excluded_counts'],paired=r['paired_market_proxy']),ensure_ascii=False))


if __name__=='__main__':main()
