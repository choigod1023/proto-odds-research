"""Lossless date-only retrospective projection adapter. Never uses raw target features."""
import argparse,gzip,hashlib,json,re
from datetime import date
from pathlib import Path

HASHES={'rows.jsonl':'66433dd4706e602befd75f8084ee43eb2e84a54b2657438c37733e3d80531c8c','labels.jsonl':'ef32ffe806b41521f5bd53c12d0eea1362bd44293181fc3ebb7c24c6fcb249ca','prior-projected-features.jsonl.gz':'eca78a16ca59ccc1de6eca47713acded513541c59cddbbb4d8dd193e7c5b27ae'}
COLUMNS=('fixture_id','league','season','kickoff_at','decision_at','home_id','away_id','market','availability_tier','sources','decision_time_basis','identity_basis')
SOURCE={name:('batters' if 'lineup' in name else 'pitchers' if ('starter' in name or 'bullpen' in name) else 'games') for name in ('home_team_strength','away_team_strength','home_rest_days','away_rest_days','park_factor','home_lineup_strength','away_lineup_strength','home_starter_strength','away_starter_strength','home_bullpen_load','away_bullpen_load')}


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def check_sources(path):
    for name,expected in HASHES.items():
        if digest(path/name)!=expected:raise ValueError('source hash mismatch: '+name)

def event_date(row):
    ident=row['fixture_id'];d=None; kickoff=row['kickoff_at']
    if kickoff is not None:
        d=date.fromisoformat(kickoff[:10])
        if re.match(r'^\d{8}',ident) and ident[4:8]!=d.strftime('%m%d'):raise ValueError('source fixture date mismatch')
    elif re.match(r'^\d{8}',ident) and int(ident[:4])==int(row['season']):
        d=date.fromisoformat(f'{ident[:4]}-{ident[4:6]}-{ident[6:8]}')
    elif re.match(r'^score:\d{4}-\d{2}-\d{2}:',ident):d=date.fromisoformat(ident[6:16])
    if d is None or int(row['season'])!=d.year:raise ValueError('ambiguous source date')
    return d

def nonnegative(*xs):return all(type(v) is int and v>=0 for v in xs)

def scalar(name,value):
    if value is None or name=='park_factor':return None
    if name.endswith('_rest_days'):
        return float(value) if nonnegative(value) else None
    if name.endswith('_team_strength'):
        usable=[z['runs_for']-z['runs_against'] for z in value if nonnegative(z.get('runs_for'),z.get('runs_against'))]
        return sum(usable)/len(usable) if usable else None
    if name.endswith('_lineup_strength'):
        ab=hit=bb=0
        for person in value:
            for x in person.get('history') or []:
                if nonnegative(x.get('ab'),x.get('hit'),x.get('bb')) and x['hit']<=x['ab']:
                    ab+=x['ab'];hit+=x['hit'];bb+=x['bb']
        return (hit+bb)/(ab+bb) if ab+bb else None
    if name.endswith('_starter_strength'):
        usable=[x for x in value['appearances'] if nonnegative(x.get('er'),x.get('outs'))]
        outs=sum(x['outs'] for x in usable)
        return 27*sum(x['er'] for x in usable)/outs if outs else None
    if name.endswith('_bullpen_load'):
        games=value['games']
        if not games or any(not nonnegative((x.get('relief') or {}).get('outs')) for x in games):return None
        return float(sum(x['relief']['outs'] for x in games))
    raise ValueError('unregistered feature')

def check_nested(obj,target_id,target_day,known_dates):
    if isinstance(obj,dict):
        for key,val in obj.items():
            if key in ('fixture_id','id') and val is not None:
                if val==target_id or val not in known_dates or known_dates[val]>=target_day:raise ValueError('nested fixture temporal violation')
            if key in ('date','latest_source_date') and val is not None and date.fromisoformat(val)>=target_day:raise ValueError('nested date temporal violation')
            if key=='source_fixture_ids':
                if any(x==target_id or x not in known_dates or known_dates[x]>=target_day for x in val):raise ValueError('source ID temporal violation')
            check_nested(val,target_id,target_day,known_dates)
    elif isinstance(obj,list):
        for val in obj:check_nested(val,target_id,target_day,known_dates)

def sanitize_raw_metadata(obj):
    # Intentionally exclude the raw target player's IDs and statistics.
    row={k:obj.get(k) for k in COLUMNS}
    row['event_date']=str(event_date(row))
    return row

def build_features(source):
    raw={}
    with (source/'rows.jsonl').open(encoding='utf8') as f:
        for line in f:
            obj=json.loads(line); row=sanitize_raw_metadata(obj);fid=row['fixture_id']
            if fid in raw:raise ValueError('duplicate source fixture')
            raw[fid]=row
    dates={fid:date.fromisoformat(row['event_date']) for fid,row in raw.items()};result=[];seen=set()
    with gzip.open(source/'prior-projected-features.jsonl.gz','rt',encoding='utf8') as f:
        for line in f:
            proj=json.loads(line);fid=proj['fixture_id']
            if fid not in raw or fid in seen or proj['scenario']!='projected_from_prior_games' or proj['availability_tier']!='retrospective_unproven':raise ValueError('projection identity invalid')
            seen.add(fid);row=raw[fid];day=dates[fid];check_nested(proj['features'],fid,day,dates);check_nested(proj['projection'],fid,day,dates)
            features={}
            for name in SOURCE:
                entry=proj['features'].get(name)
                if entry is None:continue
                value=scalar(name,entry['value'])
                features[name]={'value':value,'available_at':None,'availability_basis':'prior_calendar_day_assumed_completed_not_received','source_id':SOURCE[name], 'history_fixture_ids':entry['source_fixture_ids'],'history_latest_date':entry['latest_source_date'],'history_max_completed_at':None}
            row['features']=features;result.append(row)
    if len(raw)!=2913 or seen!=set(raw):raise ValueError('source coverage mismatch')
    return sorted(result,key=lambda r:(r['event_date'],r['fixture_id']))

FIXTURE=re.compile(r'"fixture_id"\s*:\s*("(?:\\.|[^"\\])*")')
def build_labels(source,rows,stage):
    accepted={r['fixture_id']:r for r in rows if (r['season']<=2025 if stage=='development' else r['season']==2026)};seen=set();result=[]
    with (source/'labels.jsonl').open(encoding='utf8') as f:
        for line in f:
            match=FIXTURE.search(line)
            if not match:raise ValueError('missing label ID')
            fid=json.loads(match.group(1))
            if fid not in accepted:continue
            if fid in seen:raise ValueError('duplicate label')
            seen.add(fid);r=accepted[fid];raw=json.loads(line)
            if raw['received_at'] is not None or raw['revision'] is not None:raise ValueError('unexpected archive provenance')
            label={'fixture_id':fid,'status':'final','status_basis':'archived_score_not_independently_official','home_score':raw['home_score'],'away_score':raw['away_score'],'completed_at':None,'received_at':None,'assumed_completed_date':r['event_date'],'availability_basis':'retrospective_prior_calendar_day_assumption','revision':None,'revision_basis':'archive_content_hash_not_official_revision','market_id':raw['market_id'],'settlement_rule':raw['settlement_rule']}
            result.append(label)
    if seen!=set(accepted):raise ValueError('incomplete label coverage')
    return sorted(result,key=lambda r:(r['assumed_completed_date'],r['fixture_id']))

def write_jsonl(path,rows):
    path=Path(path)
    with path.open('xb') as raw:
        stream=gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) if path.suffix=='.gz' else raw
        try:
            for r in rows:stream.write((json.dumps(r,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n').encode('utf8'))
        finally:
            if stream is not raw:stream.close()

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=Path);p.add_argument('--stage',required=True,choices=('features','development','test'));p.add_argument('--output',required=True);p.add_argument('--assume-prior-calendar-day',action='store_true');a=p.parse_args()
    if not a.assume_prior_calendar_day:raise ValueError('explicit retrospective assumption required')
    check_sources(a.source);rows=build_features(a.source)
    result=rows if a.stage=='features' else build_labels(a.source,rows,a.stage)
    write_jsonl(a.output,result);print(json.dumps({'stage':a.stage,'count':len(result),'sha256':digest(a.output),'assumption':'prior calendar day completed; actual receipt/completion unknown'}))
if __name__=='__main__':main()
