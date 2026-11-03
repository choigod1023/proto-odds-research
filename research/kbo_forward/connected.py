"""Connect direct KBO identity/history verification to the frozen model input."""
import copy,gzip,hashlib,json
from pathlib import Path
from datetime import datetime
from official import OfficialClient,SourceError,KST,now_utc
from features import build_row,seed_from_frozen

class ConnectedOfficial(OfficialClient):
    def snapshot(self,game):
        first=super().snapshot(game)
        if not first.get('announcement_verified'):
            return first
        target=datetime.strptime(game['source']['G_DT'],'%Y%m%d').date().isoformat()
        seed=seed_from_frozen(now_utc().isoformat(),target)
        history=self.make_history(game,seed)
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
