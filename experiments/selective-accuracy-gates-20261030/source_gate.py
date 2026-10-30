"""Diagnostic source publication gate; timestamps are claims, not receipt proof."""
from collections import defaultdict, Counter
from datetime import datetime
import gzip, json
from pathlib import Path

HERE = Path(__file__).resolve().parent

def identity(fixture, url, author, side):
    return ((fixture or '').lower(), (url or '').rstrip('/').lower(),
            (author or '').strip().lower(), side)

def aware(value):
    try:
        parsed = datetime.fromisoformat((value or '').replace('Z', '+00:00'))
        return parsed if parsed.tzinfo and parsed.utcoffset() is not None else None
    except (ValueError, TypeError):
        return None

def metadata_index(frames):
    index = defaultdict(list)
    for frame in frames:
        for vote in frame.get('raw_forecasts', []):
            key = identity(frame.get('event_id'), vote.get('source_url'), vote.get('author'), vote.get('side'))
            index[key].append((frame.get('kickoff'), vote.get('source_reported_published_at')))
    return index

def gate(rows, frames):
    index = metadata_index(frames); retained = []; audit = []; counts = Counter()
    for row in rows:
        key = identity(row.get('fixture_id'), row.get('source_url'), row.get('author'), row.get('side'))
        matches = index.get(key, []); kickoff = aware(row.get('kickoff'))
        compatible = [(aware(k), aware(p)) for k,p in matches if aware(k) == kickoff and kickoff is not None]
        times = [published for _,published in compatible if published is not None]
        status = ('unmatched' if not matches else 'unknown' if not times else
                  'postkickoff' if any(t >= kickoff for t in times) else 'pre_kickoff_claim')
        counts[status] += 1
        audit.append({'fixture_id':row.get('fixture_id'),'source_url':row.get('source_url'),
                      'author':row.get('author'),'side':row.get('side'),'kickoff':row.get('kickoff'),
                      'matched_publication_claims':[p for k,p in matches], 'status':status})
        if status != 'postkickoff': retained.append(row)
    return retained, audit, dict(counts)

def load_frames():
    with gzip.open(HERE/'votes-metadata.jsonl.gz','rt',encoding='utf8') as f:
        return [json.loads(line) for line in f]
