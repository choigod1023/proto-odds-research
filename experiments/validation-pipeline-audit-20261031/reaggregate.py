"""Reaggregate unchanged saved forecasts with corrected policy comparisons."""
from decimal import Decimal
import hashlib,importlib.util,json
from pathlib import Path

HERE=Path(__file__).resolve().parent
STUDY=HERE.parent/'all-sports-underdog-20261031'


def load(path):
    spec=importlib.util.spec_from_file_location('reaudit_common',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def run():
    c=load(STUDY/'common.py');audit={}
    for p in sorted((STUDY/'results').glob('*/*/results.json')):
        old=json.loads(p.read_text(encoding='utf8'));rows=c.read(p.parent/'ledger.jsonl.gz')
        before=hashlib.sha256((p.parent/'ledger.jsonl.gz').read_bytes()).hexdigest()
        info={'source_commit':'f403ef75da47b02374172581763b44f646094739','old_results_lf_sha256':hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest(),'ledger_sha256':before}
        cut=old['chronological_split_day']
        old['full']=c.summarize(rows,True)
        old['by_year']={y:c.summarize([r for r in rows if r['day'][:4]==y],True) for y in sorted({r['day'][:4] for r in rows})}
        old['by_league']={l:c.summarize([r for r in rows if r['league']==l]) for l in sorted({r['league'] for r in rows})}
        old['first_half']=c.summarize([r for r in rows if r['day']<cut],True)
        old['second_half']=c.summarize([r for r in rows if r['day']>=cut],True)
        old['trained_only']=c.summarize([r for r in rows if not r['fallback']],True)
        old['fallback_only']=c.summarize([r for r in rows if r['fallback']],True)
        old['aggregation_revision']='pipeline-audit-v2: ties retained, fallback split, per-opportunity uncertainty'
        old['forecast_generator_common_lf_sha256']=old['common_lf_sha256']
        old['aggregation_common_lf_sha256']=hashlib.sha256((STUDY/'common.py').read_bytes().replace(b'\r\n',b'\n')).hexdigest()
        info['models']={}
        for m in c.MODELS:
            a=old['full'][m]['policies'];non=a['non_underdogs'];u=a['underdogs'];fav=a['favorites']
            assert Decimal(a['all']['net_units'])-Decimal(non['net_units'])==Decimal(u['net_units'])
            info['models'][m]={'all_net':a['all']['net_units'],'underdog_net':u['net_units'],'old_favorites_net':fav['net_units'],
                'correct_non_underdog_net':non['net_units'],'ties_count':non['bets']-fav['bets'],
                'ties_net':str(Decimal(non['net_units'])-Decimal(fav['net_units'])),
                'trained_bets':old['trained_only'][m]['policies']['all']['bets'],'trained_roi':old['trained_only'][m]['policies']['all']['roi'],
                'fallback_bets':old['fallback_only'][m]['policies']['all']['bets']}
        c.save(p,old)
        assert hashlib.sha256((p.parent/'ledger.jsonl.gz').read_bytes()).hexdigest()==before
        audit[old['cohort']+'/'+old['sport']]=info
        print(json.dumps({'completed':old['cohort']+'/'+old['sport'],'ties':{m:v['ties_count'] for m,v in info['models'].items()}},ensure_ascii=False),flush=True)
    c.save(HERE/'reaggregation.json',audit)


if __name__=='__main__':run()
