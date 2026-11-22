import importlib.util
from pathlib import Path
spec=importlib.util.spec_from_file_location('fixed_share',Path(__file__).with_name('run.py'))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def row(day,y=0,ident='a'):
    return dict(id=ident,day=day,sport='bs',league='x',kickoff=day,odds=[2,2],y=y,fallback=False,month_state_sha256='x',predictions={n:{'p':p} for n,p in zip(m.NAMES,([.5,.5],[.9,.1],[.1,.9]))})
def test_delayed_and_same_day():
    rows=[row('2025-01-01',0,'a'),row('2025-01-04',1,'b'),row('2025-01-05',0,'c'),row('2025-01-05',1,'d')]
    out=m.calculate(rows); assert out[0]['weights']==out[1]['weights']; assert out[2]['weights']==out[3]['weights']; assert out[2]['weights'][1]>out[2]['weights'][2]
    changed=m.calculate([row('2025-01-01',1,'a')]+rows[1:]); assert changed[1]['weights']==out[1]['weights']; assert changed[2]['weights']!=out[2]['weights']
def test_share_and_settlement():
    w=m.update([1/3]*3,[[.5,.5],[.9,.1],[.1,.9]],0); assert abs(sum(w)-1)<1e-12 and min(w)>m.ALPHA/3
    r=m.calculate([row('2025-01-01')])[0]; assert r['methods']['market']['net']==1
