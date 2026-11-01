import csv
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

PATH = Path(__file__).resolve().parents[1] / 'experiments/all-sports-underdog-20261031/football_archive.py'
spec = importlib.util.spec_from_file_location('football_archive', PATH)
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


def row(**changes):
    data = dict(Division='E0', MatchDate='2020-01-02', HomeTeam='Home', AwayTeam='Away',
                FTHome='2.0', FTAway='1.0', FTResult='H', OddHome='2', OddDraw='3.5', OddAway='4')
    data.update(changes)
    return data


def run(tmp_path, rows):
    source, output, audit = (tmp_path / x for x in ('source.csv', 'out.gz', 'audit.json'))
    with source.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=archive.FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    summary = archive.run(source, output, audit, allow_unverified_chronology=True)
    with gzip.open(output, 'rt', encoding='utf-8') as f:
        records = [json.loads(line) for line in f]
    return source, output, audit, summary, records


def test_fixture_conflicts_and_identical_duplicates(tmp_path):
    rows = [row(), row(FTHome='2', OddHome='2.00'),
            row(HomeTeam='Conflict'), row(HomeTeam='Conflict', OddHome='2.1'),
            row(HomeTeam='Bad'), row(HomeTeam='Bad', FTResult='A'),
            row(HomeTeam='Missing'), row(HomeTeam='Missing', OddHome=''),
            row(HomeTeam='Draw', FTHome='0', FTAway='0', FTResult='D'),
            row(HomeTeam='Away winner', FTHome='0', FTAway='2', FTResult='A')]
    source, output, audit, summary, records = run(tmp_path, rows)
    assert summary['all_raw_rows'] == 10
    assert summary['accepted_rows'] == 3
    assert summary['exclusions'] == {'conflicting_fixture_rows': 6, 'identical_duplicate_rows': 1}
    assert {r['y'] for r in records} == {0, 1, 2}
    assert records[-1]['kickoff'] == '2020-01-02T00:00:00'
    assert all(r['kickoff_precision'] == 'date' for r in records)
    before = output.read_bytes()
    archive.run(source, output, audit, allow_unverified_chronology=True)
    assert output.read_bytes() == before


def test_invalid_rows_and_reasons(tmp_path):
    rows = [row(HomeTeam=''), row(HomeTeam='Same', AwayTeam='same'),
            row(HomeTeam='Date', MatchDate='2020-02-30'),
            row(HomeTeam='Score', FTHome='-1'), row(HomeTeam='Result', FTResult='X'),
            row(HomeTeam='Mismatch', FTResult='A'), row(HomeTeam='Odds', OddHome='NaN'),
            row(HomeTeam='Huge', OddHome='1e1000'), row(HomeTeam='Margin', OddHome='1.01', OddDraw='1.01', OddAway='1.01'),
            row(HomeTeam='Low margin', OddHome='100', OddDraw='100', OddAway='100')]
    _, _, _, summary, records = run(tmp_path, rows)
    assert records == []
    assert summary['excluded_rows'] == 10
    assert summary['validation_issues_overlapping'] == {'invalid_fixture': 3, 'invalid_margin': 2,
        'invalid_odds': 2, 'invalid_result': 1, 'invalid_score': 1, 'result_score_mismatch': 1}


def test_missing_headers_and_overwrite_protection(tmp_path):
    source = tmp_path / 'in.csv'
    source.write_text('Division\nE0\n', encoding='utf-8')
    with pytest.raises(ValueError, match='columns'):
        archive.run(source, tmp_path / 'out', tmp_path / 'audit')
    with pytest.raises(ValueError, match='distinct'):
        archive.run(source, source, tmp_path / 'audit')


def test_default_quarantines_unverified_dates_and_explicit_witness_rebuilds(tmp_path):
    source, output, audit, _, _ = run(tmp_path, [row(), row(HomeTeam='Unknown')])
    summary = archive.run(source, output, audit)
    assert summary['accepted_rows'] == 0
    assert summary['exclusions']['unverified_fixture_date'] == 2
    assert summary['chronology_policy'] == 'independent_fixture_dates_only'
    assert not summary['pregame_receipts_verified']
    witness = dict(id='fixture', source_fixture=row(), confirmed_day='2020-02-01',
        checked_at_utc='2026-10-31T14:15:35Z', kind='official_fixture_result',
        official_home='Home', official_away='Away', identity_check='explicit test fixture',
        observation_method='web_tool_rendered_text', urls=['https://www.fotball.no/example'])
    registry = dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), witnesses=[witness])
    summary = archive.run(source, output, audit, date_witnesses=registry)
    assert summary['accepted_rows'] == 1
    assert summary['chronology_counts'] == dict(contradicted_corrected=1, unverified=1)
    with gzip.open(output, 'rt', encoding='utf-8') as stream:
        record = json.loads(next(stream))
        assert record['day'] == '2020-02-01'
        assert record['home'] == 'Home' and record['odds'] == [2, 3.5, 4]
    assert '2020-01-02' in source.read_text(encoding='utf-8')


def test_invalid_duplicate_cannot_escape_witnessed_conflict_filter(tmp_path):
    source, output, audit, _, _ = run(tmp_path, [row(), row(OddHome='')])
    witness = dict(id='fixture', source_fixture=row(), confirmed_day='2020-02-01',
        checked_at_utc='2026-10-31T14:15:35Z', kind='official_fixture_result',
        official_home='Home', official_away='Away', identity_check='explicit test fixture',
        observation_method='web_tool_rendered_text', urls=['https://www.fotball.no/example'])
    registry = dict(source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), witnesses=[witness])
    summary = archive.run(source, output, audit, date_witnesses=registry)
    assert summary['accepted_rows'] == 0
    assert summary['exclusions']['conflicting_fixture_rows'] == 2


def test_cli_no_witness_data_returns_blocked_exit(tmp_path):
    source, output, audit, _, _ = run(tmp_path, [row()])
    result = subprocess.run([sys.executable, str(PATH), '--source', str(source),
        '--output', str(output), '--audit', str(audit)], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'chronology validation blocked' in result.stderr
    assert json.loads(audit.read_text(encoding='utf-8'))['accepted_rows'] == 0
