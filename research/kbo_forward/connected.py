"""Connect direct KBO identity/history verification to the frozen model input."""
import copy,gzip,hashlib,json
from pathlib import Path
from datetime import datetime,timedelta
from official import OfficialClient,SourceError,KST,now_utc
from features import build_row,seed_from_frozen

class ConnectedOfficial(OfficialClient):
    def _history_index(self, source):
        target = datetime.strptime(source['G_DT'], '%Y%m%d').date().isoformat()
        identity = {'target': target, 'home_id': source['HOME_ID'], 'away_id': source['AWAY_ID']}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        return identity, self.root / 'prepared-history' / (key + '.json')

    def prepare_history(self, game):
        g = game['source']
        identity, index = self._history_index(g)
        target = identity['target']
        folder = index.parent
        folder.mkdir(exist_ok=True)
        self.stage('prepared_history_check' if index.exists() else 'history_collection', g)
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
            temp = path.with_suffix('.tmp')
            temp.write_bytes(gzip.compress(raw, compresslevel=3, mtime=0))
            temp.replace(path)
        temp = index.with_suffix('.tmp')
        temp.write_text(json.dumps({'identity': identity, 'sha256': sha, 'prepared_at': now_utc().isoformat()},
                                   sort_keys=True), encoding='utf8')
        temp.replace(index)
        return history

    def prewarm_history(self, games, now):
        # Limit optional work to one uncached game per idle pregame cycle.
        local_day = now.astimezone(KST).date()
        upcoming = []
        for game in games:
            g = game['source']
            if (g['G_DT'] != local_day.strftime('%Y%m%d') or str(g['GAME_STATE_SC']) != '1'
                    or str(g['CANCEL_SC_ID']) != '0'):
                continue
            start = datetime.strptime(g['G_DT'] + ' ' + g['G_TM'], '%Y%m%d %H:%M').replace(tzinfo=KST)
            if timedelta(hours=3) < start - now <= timedelta(hours=6) and not self._history_index(g)[1].exists():
                upcoming.append(game)
        if upcoming:
            upcoming.sort(key=lambda game: (game['source']['G_TM'], game['source']['G_ID']))
            game = upcoming[int(now.timestamp()) // 300 % len(upcoming)]
            self.stage('history_prewarm', game['source'])
            self.prepare_history(game)
            self.stage('history_prewarm_complete', game['source'])

    def snapshot(self, game):
        result = super().snapshot(game, history=lambda: self.prepare_history(game), build_row=build_row)
        if not result.get('announcement_verified'):
            g = game['source']
            start = datetime.strptime(g['G_DT'] + ' ' + g['G_TM'], '%Y%m%d %H:%M').replace(tzinfo=KST)
            if (timedelta(hours=1) <= start - now_utc() <= timedelta(hours=3)
                    and not self._history_index(g)[1].exists()):
                try:
                    self.prepare_history(game)
                    result['history_prepared'] = True
                except SourceError as exc:
                    result['history_preparation_missing'] = str(exc)
        if not result.get('source_verified'):
            return result
        self.stage('archive_sources', game['source'])
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
