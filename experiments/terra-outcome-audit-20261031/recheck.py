from pathlib import Path
from decimal import Decimal
from collections import defaultdict
import argparse,gzip,hashlib,json

def main():
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    inp=a.source/'inputs/targets.jsonl.gz';pred=a.source/'results/predictions.jsonl.gz';res=a.source/'results/results.json'
    def read(path):
        with gzip.open(path,'rt',encoding='utf8') as f:return [json.loads(line) for line in f]
    targets=read(inp); preds=read(pred); stored=json.loads(res.read_text(encoding='utf8'))
    key=lambda r:(r['league'],r['cohort'],r['event_id'])
    index={key(r):r for r in targets};assert len(index)==len(targets)
    groups=defaultdict(lambda:defaultdict(lambda:[0,0,0,Decimal(0)])); checked=0
    for r in preds:
        if not r['actual_votes'] or not r['kickoff'].startswith('2026'):continue
        t=index[key(r)]
        for name in ('outcome','odds','sides','league','kickoff','decision_at','actual_votes'):
            assert r[name]==t[name],(key(r),name)
        group=(r['league'],r['model'],r['mode'])
        for arm in ('candidate','market','fixed9','vote','home_constant'):
            acc=groups[group][arm];acc[0]+=1
            p=r.get(arm)
            if p is None:continue
            assert not p['void']
            pick=p['pick'];win=pick==t['outcome'];net=Decimal(str(t['odds'][pick]))-1 if win else Decimal(-1)
            assert p['hit']==int(win) and Decimal(p['profit_cents'])==net*100
            acc[1]+=1;acc[2]+=win;acc[3]+=net;checked+=1
    selected={}; disagreements=0
    for s in stored['actual_vote_2026']:
        g=(s['league'],s['model'],s['mode'])
        for arm in ('candidate','market','fixed9','vote','home_constant'):
            n,b,w,net=groups[g][arm];expect=s['metrics'][arm];obs={'opportunities':n,'bets':b,'wins':w,'nonvoid':b,'coverage':b/n if n else None,'accuracy':w/b if b else None,'roi':float(net/b) if b else None}
            for k,v in obs.items():
                e=expect[k]
                if v is None or e is None:assert v is e,(g,arm,k,v,e)
                else:assert abs(v-e)<1e-12,(g,arm,k,v,e)
        if s['selected']:
            selected.setdefault(s['league'],{})[s['mode']]={arm:{'n':z[0],'bets':z[1],'wins':z[2],'coverage':z[1]/z[0] if z[0] else None,'accuracy':z[2]/z[1] if z[1] else None,'roi':float(z[3]/z[1]) if z[1] else None} for arm,z in groups[g].items()}
    output={'input_sha256':{x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in (inp,pred,res)},'target_rows':len(targets),'prediction_rows':len(preds),'settlement_count_actual_vote_all_candidates_modes':checked,'actual_vote_2026_targets':sum(t['actual_votes'] and t['kickoff'].startswith('2026') for t in targets),'selected':selected,'summary_discrepancies':disagreements}
    a.output.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print('targets',output['actual_vote_2026_targets'],'settlements',checked,'summary_discrepancies',disagreements)
if __name__=='__main__':main()
