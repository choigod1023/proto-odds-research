from copy import deepcopy
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from research_chronology import DateWitnesses, audit_archive


ROW = dict(Division='NOR', MatchDate='2023-03-12', HomeTeam='Molde',
           AwayTeam='HamKam', FTHome='1.0', FTAway='1.0')
PAYLOAD = b'test archive'


def registry(payload=PAYLOAD):
    return dict(source_sha256=hashlib.sha256(payload).hexdigest(), witnesses=[dict(
        id='test', source_fixture=deepcopy(ROW), confirmed_day='2023-12-03',
        checked_at_utc='2026-10-31T14:15:35Z', kind='official_fixture_result',
        official_home='Molde', official_away='HamKam', identity_check='fixture+score',
        observation_method='web_tool_rendered_text',
        urls=['https://www.fotball.no/fotballdata/turnering/hjem/?fiksId=186850'])])


def test_iso_date_can_be_contradicted_and_original_remains_immutable():
    witness = DateWitnesses(registry(), PAYLOAD)
    assert witness.assess(ROW)['status'] == 'contradicted'
    rebuilt = witness.require_verified([ROW])[0]
    assert rebuilt['MatchDate'] == '2023-12-03'
    assert rebuilt['original_MatchDate'] == ROW['MatchDate'] == '2023-03-12'


@pytest.mark.parametrize('field,value', [('Division', 'D1'), ('HomeTeam', 'molde'),
    ('AwayTeam', 'Ham-Kam'), ('FTHome', '2'), ('MatchDate', '2024-03-12')])
def test_other_leagues_aliases_scores_and_seasons_do_not_inherit_witness(field, value):
    row = {**ROW, field: value}
    witness = DateWitnesses(registry(), PAYLOAD)
    assert witness.assess(row)['status'] == 'unverified'
    with pytest.raises(ValueError, match='unverified fixture chronology'):
        witness.require_verified([ROW, row])


def test_changed_archive_and_duplicate_witnesses_fail_closed():
    with pytest.raises(ValueError, match='archive differs'):
        DateWitnesses(registry(), PAYLOAD + b'changed')
    r = registry()
    r['witnesses'].append(deepcopy(r['witnesses'][0]))
    with pytest.raises(ValueError, match='duplicate'):
        DateWitnesses(r, PAYLOAD)


@pytest.mark.parametrize('field,value', [('urls', ['https://example.org/result']),
    ('urls', ['http://www.fotball.no/result']), ('urls', []),
    ('kind', 'publication_date'), ('official_home', ''),
    ('observation_method', 'guessed'), ('confirmed_day', '2023-13-03'),
    ('checked_at_utc', '2026-10-31T14:15:35'),
    ('checked_at_utc', '2020-01-01T00:00:00Z')])
def test_incomplete_or_invalid_witness_rejected(field, value):
    r = registry()
    r['witnesses'][0][field] = value
    with pytest.raises(ValueError):
        DateWitnesses(r, PAYLOAD)


def test_audit_does_not_call_ambiguous_dates_errors_or_swap_unknown_rows(tmp_path):
    source = tmp_path / 'test.csv'
    source.write_text('Division,MatchDate,HomeTeam,AwayTeam,FTHome,FTAway\n'
                      'NOR,2023-03-12,Molde,HamKam,1,1\n'
                      'NOR,2024-02-03,A,B,0,0\n'
                      'NOR,2024-14-03,C,D,0,0\n', encoding='utf-8')
    report = audit_archive(source, registry(source.read_bytes()))
    assert report['counts'] == dict(contradicted=1, month_day_ambiguous=2,
                                     unverified=1, invalid_fixture=1)
    assert not report['strict_chronology_ready']
    assert not report['witnessed_rebuild_ready']
    assert report['findings'][0]['shift_days'] == 266
    assert report['pregame_receipts_verified'] is False


def test_fully_witnessed_correction_still_requires_rebuild(tmp_path):
    source = tmp_path / 'test.csv'
    source.write_text('Division,MatchDate,HomeTeam,AwayTeam,FTHome,FTAway\n'
                      'NOR,2023-03-12,Molde,HamKam,1,1\n', encoding='utf-8')
    report = audit_archive(source, registry(source.read_bytes()))
    assert not report['strict_chronology_ready']
    assert report['witnessed_rebuild_ready']
