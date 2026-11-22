"""Independent scalar ticket reconstruction for every saved 128-bit survivor."""
import argparse
from functools import lru_cache
import json
import math
from pathlib import Path


@lru_cache(maxsize=None)
def scalar_ticket(half_seed, draw_no):
    mask = (1<<64)-1
    x=(half_seed+draw_no*0x9E3779B97F4A7C15)&mask
    x=((x^(x>>30))*0xBF58476D1CE4E5B9)&mask
    x=((x^(x>>27))*0x94D049BB133111EB)&mask
    value=(x^(x>>31))%math.comb(45,6)
    result=[]
    upper=44
    for k in range(6,0,-1):
        while math.comb(upper,k)>value:
            upper-=1
        result.append(upper+1)
        value-=math.comb(upper,k)
        upper-=1
    return frozenset(result)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report",type=Path,default=Path("data/lotto/reports/seed-128"))
    parser.add_argument("--data",type=Path,default=Path("data/lotto/draws.jsonl"))
    args=parser.parse_args()
    report=json.loads((args.report/"experiment.json").read_text())
    protocol=report["protocol"]
    draws=[json.loads(s) for s in args.data.read_text().splitlines()]
    winning=[(d["draw_no"],set(d["numbers_sorted"])) for d in draws]
    sums={"validation":[0]*7,"final":[0]*7}
    lookup={key:{row["seed_hex"]:row for row in report[key].get("per_pair",[])} for key in sums}
    for pair in report["selection"]["pairs"]:
        value=int(pair["seed_hex"],16)
        odd,even=value>>64,value&((1<<64)-1)
        training_hits=[]
        local={"validation":[0]*7,"final":[0]*7}
        for n,expected in winning:
            actual=scalar_ticket(odd if n%2 else even,n)
            count=len(actual&expected)
            if n<=protocol["train_end"]:
                if count==6:
                    training_hits.append(n)
            else:
                key="validation" if n<=protocol["validation_end"] else "final"
                sums[key][count]+=1
                local[key][count]+=1
        assert training_hits==pair["training_jackpot_draws"]
        assert len(training_hits)>=protocol.get("minimum_pair_wins",4)
        assert sum(n%2==1 for n in training_hits)>=protocol.get("min_odd_wins",protocol.get("min_wins_per_half",2))
        assert sum(n%2==0 for n in training_hits)>=protocol.get("min_even_wins",protocol.get("min_wins_per_half",2))
        for key in sums:
            assert local[key]==lookup[key][pair["seed_hex"]]["histogram"]
    for key in sums:
        if report[key]["status"]=="evaluated":
            assert sums[key]==report[key]["selected_histogram"]
    count=len(report["selection"]["pairs"])
    result={"independent_scalar_check":"passed","pairs_verified":count,
            "rounds_per_pair":len(draws),"pair_round_tickets_verified":count*len(draws),
            "unique_stream_rounds_reconstructed":scalar_ticket.cache_info().currsize,
            "validation_histogram":sums["validation"],"final_histogram":sums["final"]}
    (args.report/"verification.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result))


if __name__=="__main__":
    main()
