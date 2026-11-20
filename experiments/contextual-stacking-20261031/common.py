from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
LEAGUES=['K리그1','K리그2','KBO','NPB','MLB']
COMPONENTS=['market','vote_score','score_static','outcome_static']

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def at(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.utcoffset() is None: raise ValueError('timezone required')
    return result.astimezone(timezone.utc)

def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf8',newline='\n')

def read_gz(path):
    with gzip.open(path,'rt',encoding='utf8') as handle:
        return [json.loads(line) for line in handle]

def write_gz(path,rows):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('wb') as target:
        with gzip.GzipFile(fileobj=target,mode='wb',mtime=0,filename='') as handle:
            for row in rows: handle.write((canonical(row)+'\n').encode('utf8'))
