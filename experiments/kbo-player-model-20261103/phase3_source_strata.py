"""Stratify already frozen overlay by preregistered price provenance."""
import argparse,importlib.util,json,sys
from pathlib import Path
P=Path(__file__).resolve().parent;spec=importlib.util.spec_from_file_location('kbo_price_strata',P/'phase3_overlay.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
parser=argparse.ArgumentParser();parser.add_argument('--output');args=parser.parse_args()
base=json.loads((P/'phase3/overlay-results.json').read_text(encoding='utf8'));result={}
for cohort,rows in base['ledgers'].items():
    groups={}
    for origin in ('original_archived','official_restored'):
        entries=[r for r in rows if r['price_source']['origin']==origin]
        groups[origin]={'n':len(entries),'refunds':sum(r['winner']=='draw' for r in entries),'date_range':[min((r['date'] for r in entries),default=None),max((r['date'] for r in entries),default=None)],'models':{name:m.metrics(entries,name) for name in ('market',)+m.MODES},'player_vs_market':m.paired(entries,'player','market'),'player_vs_team':m.paired(entries,'player','team')}
    result[cohort]=groups
if args.output:
    with Path(args.output).open('x',encoding='utf8',newline='\n') as f:json.dump(result,f,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False);f.write('\n')
print(json.dumps({k:{origin:{'n':v['n'],'refunds':v['refunds'],'player_hits':v['models']['player']['hits'],'player_roi':v['models']['player']['roi_including_refunds'],'market_hits':v['models']['market']['hits'],'market_roi':v['models']['market']['roi_including_refunds']} for origin,v in groups.items()} for k,groups in result.items()},ensure_ascii=False))
