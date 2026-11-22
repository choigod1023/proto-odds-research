import json, math
from pathlib import Path
p=Path('data/lotto/reports/seed-expanded')
r=json.loads((p/'experiment.json').read_text())
draws=[json.loads(line) for line in Path('data/lotto/draws.jsonl').read_text().splitlines()]
mask=(1<<64)-1

def ticket(seed, number):
    x=(seed+number*0x9E3779B97F4A7C15)&mask
    x=((x^(x>>30))*0xBF58476D1CE4E5B9)&mask
    x=((x^(x>>27))*0x94D049BB133111EB)&mask
    rank=(x^(x>>31))%math.comb(45,6)
    combo=[]
    upper=44
    for k in range(6,0,-1):
        while math.comb(upper,k)>rank:
            upper-=1
        combo.append(upper+1)
        rank-=math.comb(upper,k)
        upper-=1
    return set(combo)

aggregate={'validation':[0]*7,'final':[0]*7}
for entry in r['training_hits']:
    seed=int(entry['seed'])
    hits=[]
    local={'validation':[0]*7,'final':[0]*7}
    for d in draws:
        count=len(ticket(seed,d['draw_no']) & set(d['numbers_sorted']))
        if d['draw_no']<=r['protocol']['train_end']:
            if count==6: hits.append(d['draw_no'])
        else:
            key='validation' if d['draw_no']<=r['protocol']['validation_end'] else 'final'
            aggregate[key][count]+=1
            local[key][count]+=1
    assert hits==entry['draws']
    for key in local:
        row=next(v for v in r[key]['per_seed'] if v['seed']==entry['seed'])
        assert row['histogram']==local[key]
for key in aggregate:
    assert aggregate[key]==r[key]['selected_histogram']
summary={'independent_scalar_check':'passed','seeds_verified':len(r['training_hits']),
         'rounds_per_seed':len(draws),'ticket_sets_checked':len(draws)*len(r['training_hits']),
         'validation_histogram':aggregate['validation'],'final_histogram':aggregate['final']}
(p/'verification.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary))
