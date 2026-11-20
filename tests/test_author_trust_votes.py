import copy, importlib.util, pathlib, datetime

P=pathlib.Path(__file__).resolve().parents[1]/'experiments'/'author-trust-votes-20261030'
spec=importlib.util.spec_from_file_location('trust_study',P/'run.py'); study=importlib.util.module_from_spec(spec); spec.loader.exec_module(study)

def dataset():
    frames=study.read('frames.jsonl.gz'); raw=study.read('old-votes-raw.jsonl.gz')+study.read('new-votes-raw.jsonl.gz')
    return study.prepare(frames,raw)[0]

def test_heldout_flips_do_not_affect_fit_or_predictions():
    train,val,test=study.split(dataset()); boundary=min(study.ts(x['kickoff']) for x in val)
    original=study.fit(train,boundary); flipped=copy.deepcopy(test)
    for x in flipped: x['winner']=next(s for s in x['sides'] if s!=x['winner'])
    assert study.fit(train,boundary)==original
    for prior in study.PRIORS:
        assert [study.predict(x,original,prior) for x in test]==[study.predict(x,original,prior) for x in flipped]
    frames=study.read('frames.jsonl.gz'); raw=study.read('old-votes-raw.jsonl.gz')+study.read('new-votes-raw.jsonl.gz'); heldout={x['id'] for x in test}
    for f in frames:
        if f['event_id'] in heldout:
            sides=[x['side'] for x in f['choices']]; f['winner']=next(s for s in sides if s!=f['winner'])
    t2,v2,e2=study.split(study.prepare(frames,raw)[0]); h2=study.fit(t2,min(study.ts(x['kickoff']) for x in v2))
    assert original==h2
    for prior in study.PRIORS: assert [study.predict(x,original,prior) for x in test]==[study.predict(x,h2,prior) for x in e2]

def test_fit_rejects_recent_results_and_prediction_ignores_postgame_fields():
    rows=dataset(); train,val,_=study.split(rows); boundary=min(study.ts(x['kickoff']) for x in val)
    history=study.fit(train,boundary); recent=copy.deepcopy(train[0]); recent['kickoff']=(boundary-datetime.timedelta(hours=47)).isoformat(); recent['votes']=[('test-only','home')]
    assert study.fit(train+[recent],boundary)==history
    x=copy.deepcopy(val[0]); p=study.predict(x,history,1); x['winner']='bogus'; x['odds']=[999]*len(x['odds']); x['final_odds']=[.0001]*len(x['odds']); assert study.predict(x,history,1)==p

def test_identity_bridge_is_built_before_postgame_exclusion():
    def v(author,u,t): return {'author':author,'source_site':'site','source_url':u,'event_id':'e','kickoff':'2026-01-03T12:00:00+00:00','published_at_claim':t,'selection':'home'}
    raw=[v('a','https://site/a','2026-01-01T00:00:00Z'),v('b','https://site/b','2026-01-01T00:00:00Z'),v('a','https://site/b','2026-01-04T00:00:00Z')]
    f={'event_id':'e','kickoff':'2026-01-03T12:00:00+00:00','raw_forecasts':[],'choices':[{'side':'home','odds':2,'scores':{'market':.5}},{'side':'away','odds':2,'scores':{'market':.5}}], 'winner':'home','original_model_picks':{'market90_vote10':'home'}}
    rows,reasons,_,_=study.prepare([f],raw)
    assert len(rows[0]['votes'])==1 and reasons['postgame_claim']==1

def test_article_identity_query_is_not_dropped():
    a='https://forum.example/bbs/board.php?bo_table=analysis&wr_id=123'
    b='https://forum.example/bbs/board.php?bo_table=analysis&wr_id=456'
    assert study.url(a)!=study.url(b)
    assert study.url(a)!=study.url(a.replace('analysis','other'))
    assert study.url('https://forum.example/post?id=123')!=study.url('https://forum.example/post?id=456')
    assert study.url('https://forum.example/Post?id=123')!=study.url('https://forum.example/post?id=123')

def test_tracking_alias_and_transitive_citation():
    a='https://www.pickswise.com/article/?id=123&page=2&utm_source=feed'
    alias='http://pickswise.com/article?id=123&fbclid=opaque'
    assert study.url(a)==study.url(alias)
    def vote(author, address, **extra):
        return dict(author=author,source_site='site',source_url=address,event_id='e',kickoff='2026-01-03T12:00:00+00:00',published_at_claim='2026-01-01T00:00:00Z',selection='home',**extra)
    raw=[vote('a',a),vote('b','https://example.com/b',citation_urls=[alias]),vote('c','https://example.com/c',citation_urls=['https://example.com/b?utm_medium=social'])]
    frame={'event_id':'e','kickoff':'2026-01-03T12:00:00+00:00','raw_forecasts':[],'choices':[{'side':'home','odds':2,'scores':{'market':.5}},{'side':'away','odds':2,'scores':{'market':.5}}], 'winner':'home','original_model_picks':{'market90_vote10':'home'}}
    rows,_,_,_=study.prepare([frame],raw); assert len(rows[0]['votes'])==1
    raw=[vote('a','https://forum.example/bbs/board.php?bo_table=analysis&wr_id=123'),vote('b','https://forum.example/bbs/board.php?bo_table=analysis&wr_id=456')]
    rows,_,_,_=study.prepare([frame],raw); assert len(rows[0]['votes'])==2

def test_later_conflicting_author_vote_excluded_under_frozen_protocol():
    common={'author':'a','source_site':'site','event_id':'e','kickoff':'2026-01-03T12:00:00+00:00'}
    raw=[dict(common,source_url='https://site/a',published_at_claim='2026-01-01T00:00:00Z',selection='home'),dict(common,source_url='https://site/b',published_at_claim='2026-01-02T00:00:00Z',selection='away')]
    frame={'event_id':'e','kickoff':'2026-01-03T12:00:00+00:00','raw_forecasts':[],'choices':[{'side':'home','odds':2,'scores':{'market':.5}},{'side':'away','odds':2,'scores':{'market':.5}}], 'winner':'home','original_model_picks':{'market90_vote10':'home'}}
    rows,reasons,_,_=study.prepare([frame],raw); assert rows==[] and reasons['conflicting_component']==1

def test_independent_settlement():
    spec=importlib.util.spec_from_file_location('verification',P/'verify.py'); verify=importlib.util.module_from_spec(spec); spec.loader.exec_module(verify); verify.verify()
