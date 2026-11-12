"""Read-only audit adapter for saved forward captures; no new captures or ledger writes."""
import base64,gzip,hashlib,json
from datetime import datetime,date,timedelta
from pathlib import Path
R=Path(r'C:/Users/user/Documents/ChatGPT/sportstoto-research-state/kbo-m0-forward-v1'); OUT=Path(__file__).resolve().parent

def stamp(s):return datetime.fromisoformat(s.replace('Z','+00:00'))
def inspect_capture(c, root=R):
    event=c['input']['event'];at=stamp(c['frozen_at']); kickoff=stamp(event['kickoff_at']); assert at<=kickoff-timedelta(minutes=30)
    result={'fixture_id':event['official_game_id'],'frozen_at':c['frozen_at'],'reason':[],'starter_history_games':{},'raw_box_pitch_count_fields':0,'complete_feature_pair':False}
    bundles={}
    for name in ('announcement','history'):
        evidence=c['input']['evidence'][name]; raw=gzip.decompress((root/'official'/evidence['body']['relative_file']).read_bytes());assert hashlib.sha256(raw).hexdigest()==evidence['body']['archived_canonical_bundle_sha256']; assert stamp(evidence['received_at'])<=at
        bundle=json.loads(raw);bundles[name]=bundle
        for response in bundle['responses']:
            receipt=response['receipt'];body=base64.b64decode(response['body_base64'],validate=True);assert hashlib.sha256(body).hexdigest()==receipt['sha256']; assert stamp(receipt['finished_at'])<=at
    h=bundles['history']['normalized_history']; target=date.fromisoformat(event['kickoff_at'][:10]); roster=c['input']['announcement']['roster']; coverage=c['input']['row']['history_coverage']
    for side in ('home','away'):
        id=roster[side]['starter']; team=event[side+'_id'];cov=coverage[side]
        if cov['status']!='complete' or date.fromisoformat(cov['schedule_from'])>target-timedelta(days=10) or date.fromisoformat(cov['schedule_through'])<target-timedelta(days=1):result['reason'].append(side+':incomplete_10_day_schedule')
        games=[g for g in h['games'] if g.get('date') and 0<(target-date.fromisoformat(g['date'])).days<=10 and team in g.get('teams',[])]; selected=[g for g in games if id in g.get('pitchers',{})];result['starter_history_games'][side]=len(selected)
        if not selected:result['reason'].append(side+':no_identified_starter_appearance_in_10_days')
        if any(u.get('role')=='pitcher' and u.get('team')==team for g in games for u in g.get('unresolved_identities',[])):result['reason'].append(side+':unresolved_pitcher_identity_in_history')
        if any('pitches' not in g['pitchers'][id] for g in selected):result['reason'].append(side+':normalized_history_missing_pitch_count')
    for response in bundles['history']['responses']:
        if not response['receipt']['url'].endswith('GetBoxScore'):continue
        box=json.loads(base64.b64decode(response['body_base64']))
        for t in box.get('tables',[]):
            headers=t.get('headers') or []
            if headers and any(cell.get('Text')=='투구수' for cell in headers[0].get('row',[])):result['raw_box_pitch_count_fields']+=1
    # The raw name-only pitching rows need a uniquely verified name-to-player-ID join.
    # Without that join, neither raw pitch count nor zero is substituted.
    if result['raw_box_pitch_count_fields']:result['reason'].append('raw_name_to_player_id_pitch_count_join_unverified')
    return result

def main():
    p=R/'paired-ledger.jsonl';raw=p.read_bytes();records=[json.loads(l) for l in raw.splitlines()];captures=[x for x in records if x.get('type')=='capture'];result={'source_sha256':hashlib.sha256(raw).hexdigest(),'scope':'only 3 existing forward captures, receipt and archive hash checks; not full ledger validation','captures':[inspect_capture(c) for c in captures],'limits':['raw box rows use names; normalized player-ID records omit pitch counts','no reconstructed capture, no new official HTTP requests','prior 10-day appearances and receipt cutoffs checked; source team and name mapping require independent validation']};(OUT/'forward-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
