import csv
import gzip
import importlib.util
import json
from pathlib import Path

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
    summary = archive.run(source, output, audit)
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
    archive.run(source, output, audit)
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
