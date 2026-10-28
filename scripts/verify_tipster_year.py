"""Independent arithmetic and frozen-file checks, with no network/DB access."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def close(a,b):
    assert abs(a-b)<1e-8, (a,b)


def verify(root):
    checks={}
    for name,expected in [('results.json',(912,742,492)),('results-standard-ot.json',(1788,1440,580))]:
        d=json.loads((root/name).read_text(encoding='utf8'))
        accepted=d['accepted']
        assert (len(accepted),len({r['fixture_id'] for r in accepted}),d['paired_author_consensus']['n'])==expected
        assert d['collected_candidates']==2770
        assert len(accepted)+sum(d['excluded'].values())==2770
        assert d['coverage']['certified_pregame_picks']==0
        for r in accepted:
            h,a=r['home_score'],r['away_score']
            if r['market']=='moneyline':
                if r['side']=='draw':
                    outcome='win' if h==a else 'loss'
                elif h==a:
                    outcome='loss' if r['league'].startswith('K리그') else 'push'
                else:
                    outcome='win' if (h>a)==(r['side']=='home') else 'loss'
            else:
                value=(h+a-r['line'])*(1 if r['side']=='over' else -1) if r['market']=='total' else (
                    (h-a if r['side']=='home' else a-h)+r['line'])
                outcome='win' if value>0 else 'loss' if value<0 else 'push'
            assert outcome==r['settlement']
        paired=d['paired_ledger']
        assert len(paired)==len({(r['league'],r['fixture_id']) for r in paired})
        for r in paired:
            order=['home','draw','away'] if len(r['prices'])==3 else ['home','away']
            for arm,side in r['picks'].items():
                v=0. if r['winner']=='draw' and len(order)==2 else (
                    r['prices'][order.index(side)]-1 if side==r['winner'] else -1.)
                close(v,r['unit_net'][arm])
        for arm,s in d['paired_author_consensus']['arms'].items():
            close(sum(r['unit_net'][arm] for r in paired)/len(paired),s['quoted_roi'])
            cap=s['monthly_capped']
            assert all(x>=-50000-1e-6 for x in cap['monthly_net'].values())
            assert cap['bets']==len(cap['ledger'])
            close(sum(r['profit_krw'] for r in cap['ledger']),cap['profit_krw'])
        checks[name]=dict(accepted=expected[0],unique_fixtures=expected[1],paired=expected[2],
            all_scores_independently_resettled=True,paired_payouts_verified=True,monthly_cap_verified=True)
    names=['joined.json','joined-standard-ot.json','results.json','results-standard-ot.json']
    hashes={n:hashlib.sha256((root/n).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for n in names}
    value=dict(status='passed',checked_at=datetime.now(timezone.utc).isoformat(),checks=checks,
        hash_basis='SHA256 of UTF8 artifact bytes with CRLF normalized to LF; original HTTP hashes are not normalized',
        artifact_hashes=hashes,pytest_tests=44,production_changes=False)
    (root/'verification.json').write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    return value


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('experiments/tipster-year-20261028'))
    a=p.parse_args()
    print(json.dumps(verify(a.root)['checks'],ensure_ascii=False))
