"""Only open historical draws if the prospectively frozen simulation gate passes."""
import argparse
import json
from pathlib import Path
import time
import lotto_split_screen as s

EXPECTED={'base.jsonl':'30321eef31fe7494a7ada3e2224ca548967fefcb85e2558c771b1e88971224b2',
          'extra.jsonl':'0d7e5c56ca52b8b362896d0c00eaa450fad607b04cfe5ae9fa644900b891455e'}

def run(source):
    s.prior.old.resources.constrain()
    with s.Exclusive():
        results=s.read('results.json');assert results['status']=='completed'
        assert s.read('protocol.json')==s.protocol()
        assert results['gate']==s.gate(s.read('records.json'))
        if (s.OUT/'actual-results.json').exists():return s.read('actual-results.json')
        if not results['gate']['passed']:
            result={'status':'not_triggered','reason':'fixed synthetic feasibility gate failed; historical data not read or screened',
                    'gate':results['gate']}
            s.save('actual-results.json',result);return result
        assert source is not None,'approved original source directory required'
        raw=[]
        for name,expected in EXPECTED.items():
            blob=(Path(source)/name).read_bytes();assert s.digest(blob)==expected,name
            raw.extend(json.loads(line) for line in blob.decode('utf-8').splitlines())
        assert [r['draw_no'] for r in raw]==list(range(1,1246))
        draws=[{'draw_no':r['draw_no'],'numbers':r['numbers_sorted']} for r in raw]
        s.save('actual-draws.json',{'original_sha256':EXPECTED,'draws':draws})
        rows=[{v-1 for v in r['numbers']} for r in draws]
        assert all(len(r)==6 and min(r)>=0 and max(r)<45 for r in rows)
        start=time.monotonic();cpu=time.process_time();native=s.Native()
        def pause(tick):
            assert time.monotonic()-start<120 and s.prior.old.resources.peak_mib()<384
            time.sleep(max(.001,.5*(time.monotonic()-tick)))
        train=native.sample(rows[:800],[200,400,600,800],199,s.SEED+9000001,pause)
        candidates=s.screen(train);train_hash=s.digest(train.tobytes());del train
        frozen={'candidates':candidates,'train_rounds':[1,800],'validation_rounds':[801,1245],
                'train_matrix_sha256':train_hash,'training_seed':s.SEED+9000001,'validation_seed':s.SEED+9000002}
        if (s.OUT/'actual-frozen-candidates.json').exists():assert s.read('actual-frozen-candidates.json')==frozen
        else:s.save('actual-frozen-candidates.json',frozen)
        # Freeze precedes evaluating validation counts or any validation p-value.
        test=native.sample(rows[800:],[445],999,s.SEED+9000002,pause)
        confirmed=s.confirm(test,candidates)
        result={'status':'completed','candidates':confirmed,'validation_matrix_sha256':s.digest(test.tobytes()),
                'any_confirmed':any(c['confirmed'] for c in confirmed),'original_sha256':EXPECTED,
                'interpretation':'retrospective historical test: these draws were examined in earlier research; not a fresh prospective test or proof of lottery predictive power',
                'wall_seconds':time.monotonic()-start,'cpu_seconds':time.process_time()-cpu,'peak_mib':s.prior.old.resources.peak_mib()}
        s.save('actual-results.json',result);return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source-dir');args=parser.parse_args()
    print(json.dumps(run(args.source_dir),ensure_ascii=False))
