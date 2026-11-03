"""Annotate already excluded fixtures; does not alter the frozen price cohorts."""
import argparse,collections,importlib.util,json,sys
from datetime import date,datetime
from pathlib import Path
P=Path(__file__).resolve().parent;spec=importlib.util.spec_from_file_location('kbo_overlay_audit',P/'phase3_overlay.py');m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
manifest,restored=m.verify_sources();listing,official=m.load_evidence()
features=m.load_gzip(P/'phase2/features.jsonl.gz',m.EXPECTED['features.jsonl.gz']);labels=m.load_gzip(P/'phase2/test-labels.jsonl.gz',m.EXPECTED['test-labels.jsonl.gz'])
fixtures={x['fixture_id']:x for x in features if x['season']==2026};ys={x['fixture_id']:x for x in labels};rest={x['fixture_id'] for x in restored};physical=collections.Counter((r['event_date'],r['home_id'],r['away_id']) for r in fixtures.values());out=[]
for fid,row in sorted(fixtures.items()):
    if row['market'] is not None or fid in rest:continue
    day=date.fromisoformat(row['event_date']);flags=set()
    if not row['kickoff_at'] and physical[(row['event_date'],row['home_id'],row['away_id'])]>1:flags.add('date_only_doubleheader_ambiguous')
    schedules=[s for s in listing if s.get('gmOsidTsYear')==row['season'] and s.get('saleStartDate') and s.get('saleEndDate') and m.epoch_date(s['saleStartDate']).date()<=day<=m.epoch_date(s['saleEndDate']).date()]
    if not schedules:flags.add('no_round_in_sale_window')
    same=[]
    for schedule in schedules:
        info=official.get(str(schedule['gmTs']))
        if info is None:flags.add('official_round_not_fetched');continue
        for c in info['rows']:
            try:cd=m.epoch_date(c['gameDate']).date()
            except (KeyError,TypeError,ValueError,OverflowError):continue
            if c.get('leagueCode')=='BS001' and c.get('homeId')==m.TEAMS.get(row['home_id']) and c.get('awayId')==m.TEAMS.get(row['away_id']) and cd==day:same.append(c)
    if schedules and not same:flags.add('no_same_day_oriented_team_candidate')
    full=[]
    for c in same:
        modern=c.get('betId')=='2' and c.get('betTypId')=='2';legacy=c.get('betId') is None and c.get('betTypId') is None and c.get('handi')==0
        if (modern or legacy) and c.get('drawAllot') in (0,0.0) and c.get('gameSubject') in (None,''):full.append(c)
    if same and not full:flags.add('no_matching_full_game_2way_product')
    good=[]
    for c in full:
        local=[];y=ys[fid]
        if c.get('gameReject')!='0' or c.get('buyReject')!='0':local.append('cancelled_or_sales_rejected')
        if not all(m.valid_price(c.get(k)) for k in ('winAllot','loseAllot')):local.append('invalid_or_unit_price')
        if c.get('mchScore')!=f"{y['home_score']}:{y['away_score']}":local.append('score_mismatch')
        if row['kickoff_at'] and datetime.fromisoformat(row['kickoff_at'])!=m.epoch_date(c['gameDate']):local.append('kickoff_mismatch')
        flags.update(local)
        if not local:good.append(c)
    if len(good)>1:flags.add('multiple_valid_official_candidates')
    if len(good)==1 and not flags:flags.add('unexplained_single_candidate')
    if not flags:flags.add('no_individually_identified_reason')
    out.append({'fixture_id':fid,'date':row['event_date'],'source_kickoff_known':bool(row['kickoff_at']),'flags':sorted(flags)})
counts=collections.Counter(flag for x in out for flag in x['flags'])
result={'scope':'2026 fixtures already excluded from both fixed cohorts','excluded_fixtures':len(out),'flag_counts_nonexclusive':dict(counts),'fixtures':out,'note':'Flags can overlap and describe observed candidate problems; they do not change the preregistered cohort.'}
parser=argparse.ArgumentParser();parser.add_argument('--output');args=parser.parse_args()
if args.output:
    with Path(args.output).open('x',encoding='utf8',newline='\n') as f:json.dump(result,f,ensure_ascii=False,indent=2,sort_keys=True);f.write('\n')
print(json.dumps({'excluded':len(out),'flags':dict(counts)},ensure_ascii=False))
