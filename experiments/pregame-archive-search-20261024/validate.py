"""Freeze handoff cases from saved probes; no network, no ROI calculation."""
from datetime import datetime, timezone
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def dt(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def main():
    probes = {p['name']: p for p in json.loads((ROOT / 'network_probes.json').read_text())}
    origin, archived = probes['early_origin'], probes['early_archive']
    cdx = probes['early_cdx_before_start']['archive_response']
    capture = datetime.strptime(cdx[1][0], '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    assert cdx[1][1] == origin['url']
    assert cdx[1][3] == '200'
    assert cdx[1][2] == archived['sha1_base32']
    assert origin['sha256'] == archived['sha256']
    assert archived['status'] == 200 and archived['content_type'] == 'application/pdf'
    assert '/20250418223959id_/' in archived['final_url']  # reject fallback redirects
    assert archived['pdf_header'] == 'Injury Report: 04/18/25 05:30 PM'
    records = []
    for event, start, person, assertion in [
        ('2025-04-19:MIL@IND', '2025-04-19T13:00:00-04:00', 'Damian Lillard', 'contains_lillard_out'),
        ('2025-04-19:DET@NYK', '2025-04-19T18:00:00-04:00', 'Jaden Ivey', 'contains_ivey_out'),
    ]:
        assert archived[assertion]
        kickoff = dt(start)
        assert capture < kickoff < dt(archived['observed_at'])
        published = '2025-04-18T17:30:00-04:00'
        assert dt(published) < capture
        records.append({
            'id': event + ':absence', 'event_id': None, 'event_key_candidate': event,
            'sport': 'bk', 'kind': 'absence', 'subject': person, 'claim_status': 'Out',
            'summary': person + ' Out in archived report.',
            'source_url': origin['url'], 'source_report_at': published,
            'published_at': None,
            'publication_note': 'report header time; not independently measured first publication',
            'source_creation_metadata': archived['pdf_metadata']['creationDate'],
            'source_http_last_modified': origin['last_modified'],
            'third_party_archive_at': capture.isoformat(),
            'archive_url': archived['final_url'], 'archive_sha256': archived['sha256'],
            'archive_cdx_digest': cdx[1][2],
            'observed_at': archived['observed_at'],
            'kickoff_at': start, 'kickoff_type': 'scheduled_not_actual_tipoff',
            'kickoff_source_url': 'https://www.nba.com/news/starting-5-april-19-playoffs-time-go-inside-todays-4-games',
            'archive_lead_seconds': int((kickoff-capture).total_seconds()),
            'evidence_level': 'third_party_pre_scheduled_start_capture_payload_verified',
            'verification': {'source_url': archived['final_url'],
                             'observed_at': archived['observed_at'], 'confirmed': True},
            'eligible_at_or_after': capture.isoformat(),
            'model_efficacy_roi': None,
            'limits': ['not final gameday status', 'local event ID and odds cutoff not matched',
                       'availability as of archive time only', 'one shared snapshot, not independent cases'],
        })
    late = probes['nba_available']['archive_response']['archived_snapshots']['closest']['timestamp']
    late = datetime.strptime(late, '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    assert late > dt(records[0]['kickoff_at'])
    mlb = probes['mlb_origin']
    meta = dict(mlb['date_metadata'])
    mlb_capture = probes['mlb_available']['archive_response']['archived_snapshots']['closest']['timestamp']
    mlb_capture = datetime.strptime(mlb_capture, '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    mlb_start = '2025-03-19T06:10:00-04:00'
    assert dt(meta['datePublished']) < dt(meta['dateModified']) < dt(mlb_start) < mlb_capture
    negative = [
        {'id': 'nba_same_day_report', 'source_url': probes['nba_origin']['url'],
         'report_header': probes['nba_origin']['pdf_header'],
         'archive_at': late.isoformat(), 'kickoff_at': records[0]['kickoff_at'],
         'eligible': False, 'reason': 'nearest archive is after scheduled start; exact-URL earlier CDX empty'},
        {'id': 'mlb_faq', 'source_url': mlb['url'], 'source_datePublished': meta['datePublished'],
         'source_dateModified': meta['dateModified'], 'observed_at': mlb['observed_at'],
         'archive_at': mlb_capture.isoformat(), 'kickoff_at': mlb_start,
         'eligible': False, 'reason': 'nearest capture after start; publisher timestamps alone insufficient'},
    ]
    report = {'records': records, 'negative_controls': negative, 'roi': None,
              'verified_snapshot_count': 1, 'candidate_event_count': 2}
    (ROOT / 'cases.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'verified_snapshot_count': 1, 'candidate_event_count': 2,
                      'lead_seconds': [r['archive_lead_seconds'] for r in records],
                      'sha1_matches_cdx': True, 'sha256_matches_current_origin': True}))


if __name__ == '__main__':
    main()
