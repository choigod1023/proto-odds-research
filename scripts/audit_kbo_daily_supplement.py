"""Independently verify packaged KBO daily supplement against frozen v1."""
import argparse,base64,gzip,hashlib,json
from pathlib import Path
import research_kbo_daily_supplement as k
import research_kbo_daily_merge_contract as contract


def sha(b):return hashlib.sha256(b).hexdigest()

def audit(v1_path,v1_raw_path,supp_path,daily_raw_path):
    v1b=Path(v1_path).read_bytes()
    if sha(v1b)!=k.V1_SHA:raise ValueError('v1_sha_mismatch')
    v1=[json.loads(x) for x in gzip.decompress(v1b).splitlines()];orig={x['official_game_id']:x for x in v1}
    supplement_bytes=Path(supp_path).read_bytes();supp=[json.loads(x) for x in gzip.decompress(supplement_bytes).splitlines()]
    if len(supp)!=256:raise ValueError('supplement_denominator')
    raw_bundle=Path(daily_raw_path).read_bytes();entries=[json.loads(x) for x in gzip.decompress(raw_bundle).splitlines()]
    bodies={};eligible=set();excluded=set()
    for e in entries:
        m=e['metadata']
        if e['raw_base64'] is None:continue
        raw=base64.b64decode(e['raw_base64'],validate=True)
        if sha(raw)!=m['sha256'] or len(raw)!=m['bytes']:raise ValueError('daily_raw_sha_mismatch')
        bodies[m['sha256']]=raw
        (eligible if e['eligible_for_supplement'] else excluded).add(m['sha256'])
    schedule_raw={}
    for line in gzip.open(v1_raw_path,'rt',encoding='utf8'):
        e=json.loads(line)
        if e['kind']!='official_schedule':continue
        raw=base64.b64decode(e['raw_base64'],validate=True);m=e['metadata']
        if sha(raw)!=m['sha256']:raise ValueError('schedule_sha_mismatch')
        schedule_raw[m['sha256']]=raw
    parsed={};verified_batters=verified_pitchers=0;used=set()
    for g in supp:
        source=orig[g['official_game_id']];e=source['evidence']['schedule'];schedule=json.loads(schedule_raw[e['sha256']]);matches=[x for x in schedule['game'] if x['G_ID']==g['official_game_id']]
        if len(matches)!=1:raise ValueError('schedule_game_mismatch')
        game=matches[0]
        for side,opp in [('home','AWAY_NM'),('away','HOME_NM')]:
            original=source['statistics']['batters'][side]
            for row in g['batters'][side]:
                if row['status']!='matched':continue
                meta=row['daily_evidence'];digest=meta['sha256'];used.add(digest)
                if digest not in eligible or digest in excluded:raise ValueError('ineligible_evidence')
                if (digest,g['series_id']) not in parsed:
                    doc=k.soup(bodies[digest]);parsed[(digest,g['series_id'])]=(k.profile_name(doc),k.daily_rows(doc,g['series_id']))
                name,days=parsed[(digest,g['series_id'])];player=original[row['v1_row_index']]
                if name!=player['name']:raise ValueError('profile_name_mismatch')
                expected=k.merge_batter(source,side,player,days,game[opp].strip(),schedule)
                if expected['status']!='matched' or any(expected[key]!=row[key] for key in ('BB','HR','K','2B','3B','HBP')):raise ValueError('daily_value_mismatch')
                verified_batters+=1
            for row in g['pitcher_id_supplement'][side]:
                if row['status']!='verified_official_profile':continue
                digest=row['profile_evidence']['sha256'];used.add(digest)
                if digest not in eligible or digest in excluded or k.profile_name(k.soup(bodies[digest]))!=row['name']:
                    raise ValueError('pitcher_profile_mismatch')
                verified_pitchers+=1
    for e in entries:
        m=e['metadata'];form=m.get('form') or {};state=form.get('public_form_state_source_sha256')
        if m.get('sha256') in used and state and (state not in eligible or state in excluded):raise ValueError('ineligible_postback_form')
    joined=contract.prior_batter_history(v1,supp,'20261103')
    if len(joined['history'])+joined['coverage']['excluded_zero_opportunities']!=verified_batters or contract.prior_batter_history(v1,supp,'20260728')['history']:
        raise ValueError('prior_guard_failure')
    added=sum(len(contract.verified_pitcher_ids(orig[g['official_game_id']],g)) for g in supp)
    if added!=verified_pitchers:raise ValueError('pitcher_join_mismatch')
    return {'games':len(supp),'verified_daily_rows':verified_batters,'verified_pitcher_rows':verified_pitchers,
            'coverage':joined['coverage'],'raw_response_entries':len(entries),'excluded_response_entries':sum(not x['eligible_for_supplement'] for x in entries),
            'supplement_gzip_sha256':sha(supplement_bytes),'daily_raw_gzip_sha256':sha(raw_bundle),'network_requests':0}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--v1',required=True);p.add_argument('--v1-raw',required=True);p.add_argument('--supplement',required=True);p.add_argument('--daily-raw',required=True);a=p.parse_args()
    print(json.dumps(audit(a.v1,a.v1_raw,a.supplement,a.daily_raw),ensure_ascii=False,indent=2))
