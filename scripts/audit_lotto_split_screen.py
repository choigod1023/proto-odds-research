"""Read summaries and replay fixed representatives without rerunning the study."""
import json
import time
import lotto_split_screen as s

def main():
    s.prior.old.resources.constrain();start=time.monotonic()
    with s.Exclusive():
        spec=s.read('protocol.json');assert spec==s.protocol()
        records=s.read('records.json');result=s.read('results.json');progress=s.read('progress.json')
        assert len(records)==len(s.jobs())==280 and result['status']=='completed'
        assert progress['state']=='completed' and progress['completed']==280
        for index,(r,job) in enumerate(zip(records,s.jobs())):
            assert (r['kind'],r['strength'],r['replicate'])==job and r['index']==index
            assert r['seed']==s.SEED+10000*index
            assert len(r['candidates'])==2 and r['candidates'][0]['index']<990<=r['candidates'][1]['index']
            for c in r['candidates']:
                assert c['direction'] in (-1,1)
                assert c['adjusted_p']==min(1,2*c['raw_p']) and c['confirmed']==(c['adjusted_p']<=.05)
            assert r['split_any']==any(c['confirmed'] for c in r['candidates'])
            assert r['split_target']==any(c['confirmed'] and c['index']==r['target_index'] for c in r['candidates'])
            assert r['full_any']==(r['full_any_p']<=.05)
            assert r['full_target']==(r['full_target_p'] is not None and r['full_target_p']<=.05)
        assert result['cells']==s.summarize(records) and result['gate']==s.gate(records)
        native=s.Native()
        def pause(tick):
            assert time.monotonic()-start<120 and s.prior.old.resources.peak_mib()<384
            time.sleep(max(.001,.5*(time.monotonic()-tick)))
        indices=[0,100,130,160,190,220,250]
        for i in indices:assert s.evaluate(i,native,pause)==records[i],i
        actual=s.read('actual-results.json')
        if not result['gate']['passed']:
            assert actual['status']=='not_triggered'
            assert not (s.OUT/'actual-draws.json').exists() and not (s.OUT/'actual-frozen-candidates.json').exists()
        else:
            assert actual['status']=='completed'
            data=s.read('actual-draws.json');rows=[{v-1 for v in r['numbers']} for r in data['draws']]
            frozen=s.read('actual-frozen-candidates.json')
            train=native.sample(rows[:800],[200,400,600,800],199,frozen['training_seed'],pause)
            assert s.digest(train.tobytes())==frozen['train_matrix_sha256'] and s.screen(train)==frozen['candidates'];del train
            test=native.sample(rows[800:],[445],999,frozen['validation_seed'],pause)
            assert s.digest(test.tobytes())==actual['validation_matrix_sha256']
            assert s.confirm(test,frozen['candidates'])==actual['candidates']
        paths=[p for p in s.OUT.glob('*.json') if p.name!='verification.json']
        evidence={'status':'passed','records':280,'replayed_indices':indices,'all_protocol_source_hashes_passed':True,
                  'gate_recomputed':True,'actual_status':actual['status'],'native_sha256':s.digest(native.path.read_bytes()),
                  'wall_seconds':time.monotonic()-start,'peak_mib':s.prior.old.resources.peak_mib(),
                  'sha256':{str(p.relative_to(s.ROOT)).replace('\\','/'):s.digest(p.read_bytes()) for p in paths}}
        s.save('verification.json',evidence)
        print(json.dumps({k:v for k,v in evidence.items() if k!='sha256'}))

if __name__=='__main__':main()
