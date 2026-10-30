"""Independent settlement and metric verification from the frozen ledger."""
import collections, json, pathlib
P=pathlib.Path(__file__).resolve().parent
def verify():
    ledger=json.loads((P/'ledger.json').read_text(encoding='utf8'))
    report=json.loads((P/'results.json').read_text(encoding='utf8'))
    totals=collections.defaultdict(lambda:[0,0]); errors=[]
    for row in ledger:
        payout=dict(zip(row['sides'],row['odds']))
        for name,pick in row['picks'].items():
            won=int(pick==row['winner']); profit=-100
            if won: profit+=round(payout[pick]*100)
            expected=row['settlement'][name]
            if won!=expected['hit'] or profit!=expected['profit_cents']: errors.append((row['id'],name))
            totals[name][0]+=won; totals[name][1]+=profit
    for name,(hits,profit) in totals.items():
        metric=report['selected_test'][name]
        if hits!=metric['hit'] or len(ledger)!=metric['n'] or profit/(100*len(ledger))!=metric['roi'] or hits/len(ledger)!=metric['accuracy']: errors.append(('metric',name))
    if errors: raise AssertionError(errors)
    print('independent settlement agreement:',len(ledger),'events,',sum(len(x['picks']) for x in ledger),'picks')
if __name__=='__main__': verify()
