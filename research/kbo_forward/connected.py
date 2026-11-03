"""Connect direct KBO identity/history verification to the frozen model input."""
import copy,gzip,hashlib,json
from pathlib import Path
from datetime import datetime,timedelta
from official import OfficialClient,SourceError,KST,now_utc
from features import build_row,seed_from_frozen

class ConnectedOfficial(OfficialClient):
    def prepare_history(self,game):
        target=datetime.strptime(game['source']['G_DT'],'%Y%m%d').date().isoformat()
        g=game['source'];identity={'target':target,'home_id':g['HOME_ID'],'away_id':g['AWAY_ID']}
        key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
        folder=self.root/'prepared-history';folder.mkdir(exist_ok=True);index=folder/(key+'.json')
        event={'official_game_id':g['G_ID'],'season':int(g['SEASON_ID']),'home_id':g['HOME_ID'],'away_id':g['AWAY_ID'],'kickoff_at':datetime.strptime(g['G_DT']+' '+g['G_TM'],'%Y%m%d %H:%M').replace(tzinfo=KST).isoformat()}
        if index.exists():
            ref=json.loads(index.read_bytes());path=folder/(ref['sha256']+'.json.gz');raw=gzip.decompress(path.read_bytes())
            if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise SourceError('prepared_history_changed')
            history=json.loads(raw)
            if self.verify_history_stats(event,{},history):return history
            raise SourceError('prepared_history_no_longer_verifies')
        seed=seed_from_frozen(now_utc().isoformat(),target)
        history=self.make_history(game,seed)
        if not self.verify_history_stats(event,{},history):raise SourceError('new_history_did_not_verify')
        raw=json.dumps(history,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8');sha=hashlib.sha256(raw).hexdigest();path=folder/(sha+'.json.gz')
        if not path.exists():
            with path.open('xb') as out:out.write(gzip.compress(raw,mtime=0))
        index.write_text(json.dumps({'identity':identity,'sha256':sha,'prepared_at':now_utc().isoformat()},sort_keys=True),encoding='utf8')
        return history

    def snapshot(self,game):
        first=super().snapshot(game)
        if not first.get('announcement_verified'):
            g=game['source'];start=datetime.strptime(g['G_DT']+' '+g['G_TM'],'%Y%m%d %H:%M').replace(tzinfo=KST)
            if timedelta(minutes=30)<=start-now_utc()<=timedelta(hours=3):
                try:self.prepare_history(game);first['history_prepared']=True
                except SourceError as exc:first['history_preparation_missing']=str(exc)
            return first
        history=self.prepare_history(game)
        result=super().snapshot(game,history=history,build_row=build_row)
        if not result.get('source_verified'):
            return result
        if any(v.get('status')!='complete' for v in result['row'].get('history_coverage',{}).values()):
            result.update(source_verified=False,missing=['verified_history_coverage_incomplete'])
            return result
        # Keep full received source bundles once in compressed, content-addressed
        # evidence. Ledger records refer to their bytes, never duplicate seeds.
        payload=copy.deepcopy(result['payload'])
        folder=self.root/'verified-bundles';folder.mkdir(exist_ok=True)
        for kind,ev in payload['evidence'].items():
            raw=json.dumps(ev['body'],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')
            digest=hashlib.sha256(raw).hexdigest();path=folder/(digest+'.json.gz')
            if path.exists():
                if gzip.decompress(path.read_bytes())!=raw:raise SourceError('archived_verified_bundle_changed')
            else:
                with path.open('xb') as out:out.write(gzip.compress(raw,mtime=0))
            body={'archived_canonical_bundle_sha256':digest,'relative_file':str(path.relative_to(self.root)).replace('\\','/'),'kind':kind,'contains_direct_response_bytes_and_receipts':True}
            ev['body']=body;ev['sha256']=hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()
        payload['row']['input_quality']={'missing_features':sorted(k for k,v in payload['row']['features'].items() if v['value'] is None),'historical_seed_receipt_is_current_not_historical_proof':True,'baseball_walk_plus_HBP_not_inferred_as_walks':True}
        result['payload']=payload
        return result

def verify_archived_bundles(payload,root):
    root=Path(root).resolve()
    for ev in payload['evidence'].values():
        body=ev['body']
        if 'archived_canonical_bundle_sha256' not in body:raise ValueError('direct-source archived bundle missing')
        expected=body['archived_canonical_bundle_sha256'];path=(root/body['relative_file']).resolve()
        if not path.is_relative_to(root/'verified-bundles') or path.name!=expected+'.json.gz':raise ValueError('source bundle path outside archive')
        raw=gzip.decompress(path.read_bytes())
        if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('source bundle hash mismatch')
        json.loads(raw)
