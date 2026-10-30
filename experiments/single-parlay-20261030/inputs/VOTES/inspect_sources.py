import ast, collections, json, pathlib, re, sys
if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf8')
BASE = pathlib.Path('C:/Users/user/Documents/ChatGPT/sportstoto-worktrees')
YEAR = BASE/'year-model-hit-comparison-20261029/experiments/year-model-hit-20261029'
UP = BASE/'tipster-year-validation-20261028'
def selected(path, names, env):
    tree = ast.parse(path.read_text(encoding='utf8'))
    nodes = [n for n in tree.body if (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)) or (isinstance(n, ast.FunctionDef) and n.name in names)]
    exec(compile(ast.Module(body=nodes,type_ignores=[]), str(path), 'exec'), env)
env = {'re':re}
selected(UP/'scripts/evaluate_tipster_expansion.py', {'team_key'},env)
selected(UP/'scripts/join_tipster_expansion.py', {'ALIASES','canonical'},env)
selected(UP/'scripts/collect_tipster_year_nba.py', {'NAMES'},env)
selected(UP/'scripts/tipster_year_aliases.py', {'NBA','MLB','configure'},env)
env['configure']()
canonical=env['canonical']
def read(p):
    t=p.read_text(encoding='utf8');return json.loads(t) if p.suffix=='.json' else [json.loads(l) for l in t.splitlines() if l]
def key(r):return r['league'], r.get('match_date') or r.get('date') or r.get('kickoff','')[:10], canonical(r['home'],r['league']), canonical(r['away'],r['league'])
strict=read(YEAR/'strict-results.json')['event_ledger'];ids={(e['league'],e['fixture_id']) for e in strict}
extra=[e for e in read(YEAR/'standard-ot-results.json')['event_ledger'] if e['league']=='NBA' and (e['league'],e['fixture_id']) not in ids]
events=strict+extra
offers=[]
for name in ['offers.jsonl','old-preserved-offers.jsonl']:
    for i,r in enumerate(read(UP/'experiments/tipster-year-20261028/prices'/name)):
        offers.append(dict(r,_source=name,_row=i+1))
oi=collections.defaultdict(list)
for o in offers:oi[key(o)].append(o)
counts=collections.Counter();missing=[];multi=[]
for e in events:
    matches=[o for o in oi[key(e)] if o['odds']==e['prices'] and o['odds_order']==e['order']]
    counts[(e['league'],bool(matches))]+=1
    if not matches:missing.append({k:e[k] for k in ['league','fixture_id','match_date','home','away','prices']})
    elif len({(o.get('raw_year'),o.get('raw_round'),o.get('raw_game_no')) for o in matches})>1:multi.append((e['fixture_id'],[(o['raw_year'],o['raw_round'],o['raw_game_no']) for o in matches]))
print('YEAR',len(events),'OFFER_MATCH',dict((str(k),v) for k,v in counts.items()),'MULTI',multi[:5],'MISSING',len(missing),missing[:12])
ekeys={key(e) for e in events}
for group,name in [('KBO','cohorts.json'),('NPB','events.json')]:
    d=read(BASE/'league-loss-cap-ablation-20261030/.local-test'/group/name);rows=d['records'] if isinstance(d,dict) else d
    rows=[r for r in rows if r.get('voters')]
    new=[r for r in rows if key(r) not in ekeys]
    print(group,'voteevents',len(rows),'new',len(new),'sample',new[:2])
    for r in new:
        m=[o for o in oi[key(r)] if o['odds']==r['prices']]
        print('EXTRA',r['event_id'],r['kickoff'],'offer_matches',len(m))
