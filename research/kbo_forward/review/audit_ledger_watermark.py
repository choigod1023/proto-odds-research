"""Review fixed ledger in a temp directory with synthetic inputs; no live fetches."""
import copy,importlib.util,json,subprocess,sys,tempfile,types
from datetime import timedelta
from pathlib import Path
root=Path(r'C:\Users\user\Documents\Codex\kbmforward')
commit='a755f36c2d639bb1b499ef39854a2961b7ee765e'
sys.path.insert(0,str(root/'research/kbo_forward'))
source=subprocess.check_output(['git','-C',str(root),'show',commit+':research/kbo_forward/ledger.py'])
mod=types.ModuleType('reviewed_ledger');mod.__file__=str(root/'research/kbo_forward/ledger.py');exec(compile(source,mod.__file__,'exec'),mod.__dict__)
spec=importlib.util.spec_from_file_location('fixtures',root/'experiments/kbo-m0-followup-20261103/test_forward.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
def result(x,c,when,h,a):
 body={'test_only':True,'home':h,'away':a}
 return {'event_key':c['event_key'],**{k:x['event'][k] for k in ('official_game_id','season','home_id','away_id')},'game_date':x['row']['event_date'],'status':'final','home_score':h,'away_score':a,'evidence':{'uri':'https://www.koreabaseball.com/test','received_at':when.isoformat(),'body':body,'sha256':mod.digest(mod.canonical(body))}}
with tempfile.TemporaryDirectory(prefix='kbo-ledger-review-') as tmp:
 p=Path(tmp)/'ledger.jsonl';x=old.snapshot();x['market'].update(kind='full_game_3way',settlement_rule='draw_is_outcome');x['market']['odds']['draw']='3.10';x['market']['close_at']=x['event']['kickoff_at'];x['market']['round_close_at']=x['event']['kickoff_at']
 c=mod.capture(x,p,clock=lambda:old.NOW,infer=old.fake_infer)
 forged_promoted=c['source_verification']=='direct_fetch_parsed_identity_checked_local_clock' and c['eligible_for_paired_paper_comparison']
 view_leaked='MODEL_INPUT_VALIDATION_ONLY' in json.dumps(c) or c['input']['market']!=x['market']
 when=mod.moment(x['event']['kickoff_at'])+timedelta(hours=4);a=result(x,c,when,4,2);mod.settle(a,p,clock=lambda:when)
 later=when+timedelta(minutes=20);same=result(x,c,later,4,2);ignored=mod.settle(same,p,clock=lambda:later) is None
 middle=when+timedelta(minutes=10);stale=result(x,c,middle,1,2)
 try:mod.settle(stale,p,clock=lambda:later);stale_accepted=True
 except ValueError:stale_accepted=False
 u=old.snapshot();u.pop('market');u['evidence'].pop('odds');bad=lambda row:({'baseline':{'bad':1}}, {'bogus':'hash'});q=Path(tmp)/'unpriced.jsonl'
 try:r=mod.capture_unpriced(u,q,clock=lambda:old.NOW,infer=bad);invalid_unpriced_accepted=r is not None
 except (ValueError,KeyError):invalid_unpriced_accepted=False
assert forged_promoted and not view_leaked and not ignored and not stale_accepted and invalid_unpriced_accepted
print(json.dumps({'ledger_commit':commit,'model_validation_only_view_leaked_to_capture':view_leaked,'fabricated_source_promoted_to_verified':forged_promoted,'identical_later_result_receipt_ignored':ignored,'conflicting_older_than_latest_receipt_accepted':stale_accepted,'invalid_unpaired_unpriced_forecast_accepted':invalid_unpriced_accepted},indent=2))
