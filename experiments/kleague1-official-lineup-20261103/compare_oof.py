"""Report exact-ID linkage to the separately frozen OOF input; no scoring."""
import argparse
import hashlib
import json
import math
from pathlib import Path

BASE=Path(__file__).resolve().parent
EXPECTED='158cd931f3fa576b97678dfcc0b44042b9ef1a5826183853fb6d9b0beb314f05'

def compare(fixtures,rows):
    out=[]
    for f in fixtures:
        candidates=[r for r in rows if r['id']==f['fixture_id']]
        result={'fixture_id':f['fixture_id'],'matching_ids':len(candidates),'identity_date_score_match':None,'complete_threeway_price':None}
        if len(candidates)==1:
            r=candidates[0];result['identity_date_score_match']=(r['home_id'],r['away_id'],r['day'],r['hg'],r['ag'])==(f['lineups'][0]['team_id'],f['lineups'][1]['team_id'],f['day'],f['scores']['home'],f['scores']['away'])
            prices=r.get('prices')
            try: result['complete_threeway_price']=isinstance(prices,list) and len(prices)==3 and all(math.isfinite(float(x)) and float(x)>1 for x in prices)
            except (ValueError,TypeError): result['complete_threeway_price']=False
        out.append(result)
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument('--oof',required=True);a=p.parse_args();b=Path(a.oof).read_bytes()
    if hashlib.sha256(b).hexdigest()!=EXPECTED:raise ValueError('frozen OOF byte SHA mismatch')
    fixtures=[json.loads(x) for x in (BASE/'fixtures.jsonl').read_text(encoding='utf8').splitlines()]
    rows=[json.loads(x) for x in b.decode('utf8').splitlines()];links=compare(fixtures,rows)
    result={'fixtures_sha256':hashlib.sha256((BASE/'fixtures.jsonl').read_bytes()).hexdigest(),'frozen_oof_sha256':EXPECTED,'oof_rows':len(rows),'links':links,'matched_identity_date_score':sum(x['identity_date_score_match'] is True for x in links),'complete_prices_on_matched':sum(x['identity_date_score_match'] is True and x['complete_threeway_price'] is True for x in links),'historical_actual_receipt_verified':0,'note':'Exact identity/date/result with archived 3-way prices is retrospective; it is not evidence of actual pregame receipt or executable prices.'}
    (BASE/'crosswalk.json').write_bytes((json.dumps(result,ensure_ascii=False,indent=2)+'\n').encode('utf8'));print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
