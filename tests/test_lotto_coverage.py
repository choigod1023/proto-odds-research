import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import itertools
import math
import lotto_coverage as c

def brute(tickets,n,k):
    h=[0]*(k+1)
    for draw in itertools.combinations(range(1,n+1),k):
        h[max(len(set(draw)&set(t)) for t in tickets)]+=1
    return h

def test_chunk_enumerator_matches_independent_sets():
    tickets=[[1,2,3],[2,3,4],[4,5,6]]
    out=c.count({'p':tickets},n=8,k=3,batch=7)['p']
    assert out['maximum_match_histogram']==brute(tickets,8,3)

def test_split_shared_label_never_hurts_all_thresholds():
    # Exhaust all ordered triples of size-two tickets on five labels;
    # split shared label 1 into unused label 6 on one affected ticket.
    tickets=list(itertools.combinations(range(1,6),2))
    checked=0
    for portfolio in itertools.product(tickets,repeat=3):
        affected=[i for i,t in enumerate(portfolio) if 1 in t]
        if len(affected)<2:continue
        changed=[list(t) for t in portfolio]
        j=affected[0];changed[j]=[6 if x==1 else x for x in changed[j]]
        before=brute(portfolio,6,2);after=brute(changed,6,2)
        for threshold in (1,2):assert sum(after[threshold:])>=sum(before[threshold:])
        checked+=1
    assert checked>0

def test_disjoint_analytic_counts():
    assert c.disjoint_formula()=={'3':966650,'4':56750}
    assert len(set(sum(c.NEW,[])))==30

def test_four_match_union_bound_is_attained():
    assert c.disjoint_formula()['4']==5*sum(math.comb(6,j)*math.comb(39,6-j) for j in range(4,7))

def test_duplicate_ticket_is_not_extra_coverage():
    ticket=[1,2,3]
    a=c.count({'one':[ticket],'twice':[ticket,ticket]},n=7,k=3,batch=5)
    assert a['one']['maximum_match_histogram']==a['twice']['maximum_match_histogram']
