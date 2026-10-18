from prediction_performance import performance_index


def test_performance_index_preserves_all_events_and_excludes_private_inputs():
    records={'a':{'prediction_snapshot_id':'one','result':'hit','score_forecast':{'private':'inputs'}},
             'b':{'prediction_snapshot_id':'two','result':'pending'}}
    result=performance_index(records)
    assert result['scope']=='all_ledger_predictions'
    assert len(result['records'])==2
    assert 'score_forecast' not in result['records']['a']
    assert result['records']['b']['result']=='pending'
    assert 'score_forecast' in records['a']


def test_public_metadata_keeps_kickoff_but_not_private_fixture_inputs():
    record={'prediction_snapshot_id':'s','kickoff':'2026-10-18T08:00:00Z',
            'fixture':{'sport':'bs','league':'KBO','home':'KIA','away':'LG','secret':'never'}}
    result=performance_index({'e':record})['records']['e']
    assert result['kickoff']==record['kickoff']
    assert result['fixture']['home']=='KIA'
    assert 'secret' not in result['fixture']
