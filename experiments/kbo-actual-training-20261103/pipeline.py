"""Matched actual/projected roster modelling; accepts verified immutable pairs only."""
import hashlib,json,math,sys
from pathlib import Path
P=Path(__file__).resolve().parent;V=P/'vendor';sys.path.insert(0,str(V));sys.path.insert(0,str(V/'v2'))
import research as original
import research_v2 as corrected
CONTRACT_SHA='512c6172eb792ed6e5f7afe7b90d4cfaa0e4087b05e0a497ec7cf9a997cb2480'
FEATURES=('home_team_strength','away_team_strength','home_rest_days','away_rest_days','park_factor','home_lineup_strength','away_lineup_strength','home_starter_strength','away_starter_strength','home_bullpen_load','away_bullpen_load')
PLAYER=tuple(k for k in FEATURES if any(s in k for s in ('lineup_strength','starter_strength','bullpen_load')))
ROSTER_FEATURES=tuple(k for k in PLAYER if 'lineup_strength' in k or 'starter_strength' in k)
FAMILIES=('M0','M2');SELECTORS=('projected','actual');MODEL_NAMES=('M0_projected','M0_actual','M2_projected','M2_actual','oldfull_M0','oldfull_M2')

def verify_pinned_code():
 if hashlib.sha256((P/'contract.json').read_bytes()).hexdigest()!=CONTRACT_SHA:raise ValueError('contract hash mismatch')
 man=json.loads((V/'manifest.json').read_bytes())
 if man['source_commit']!='d08ccb708033fb6b067e84e80eae81ea3c9b4cfa':raise ValueError('wrong source model commit')
 for name,sha in man['files'].items():
  if hashlib.sha256((V/name).read_bytes()).hexdigest()!=sha:raise ValueError('vendor hash mismatch: '+name)

def index(rows):
 out={x['fixture_id']:x for x in rows}
 if len(out)!=len(rows):raise ValueError('duplicate fixtures')
 return out

def validate_roster(roster,actual):
 if set(roster)!={'home','away'}:raise ValueError('roster sides')
 for side in ('home','away'):
  z=roster[side]
  if set(z)!={'hitters','starter'}:raise ValueError('unexpected player fields')
  hits=z['hitters'];starter=z['starter']
  if actual and (hits is None or starter is None):raise ValueError('actual roster identity missing')
  if hits is not None and (len(hits)!=9 or len(set(hits))!=9 or any(not isinstance(i,str) or not i.strip() for i in hits)):raise ValueError('invalid ordered hitters')
  if starter is not None and (not isinstance(starter,str) or not starter.strip()):raise ValueError('invalid starter')
 if actual and set(roster['home']['hitters'])&set(roster['away']['hitters']):raise ValueError('opposing duplicate hitter')
 if actual and roster['home']['starter']==roster['away']['starter']:raise ValueError('opposing duplicate starter')

def validate_row(row,actual):
 required={'fixture_id','season','event_date','home_id','away_id','features','roster'}
 if set(row)!=required or not isinstance(row['fixture_id'],str) or not row['fixture_id'] or not all(isinstance(row[k],str) and row[k] for k in ('home_id','away_id')) or row['home_id']==row['away_id']:raise ValueError('invalid normalized row or extra target fields')
 from datetime import date
 if date.fromisoformat(row['event_date']).year!=row['season']:raise ValueError('season or date mismatch')
 if set(row['features'])!=set(FEATURES):raise ValueError('unexpected feature fields')
 for value in row['features'].values():
  if set(value)!={'value'} or value['value'] is not None and (type(value['value']) not in (int,float) or not math.isfinite(value['value'])):raise ValueError('invalid feature value')
 validate_roster(row['roster'],actual)

def validate_lineage(fid,event_date,lineage,known_dates):
 """Raw model inputs must be reconstructed from strictly earlier fixture data."""
 from datetime import date
 if set(lineage)!=set(PLAYER):raise ValueError('missing player feature lineage')
 day=date.fromisoformat(event_date)
 for name,entry in lineage.items():
  if not isinstance(entry,dict) or set(entry)!={'availability_basis','completed_at','received_at','latest_source_date','source_fixture_ids','value'} or not isinstance(entry['source_fixture_ids'],list) or entry.get('availability_basis')!='prior_calendar_day_assumed_completed_not_received' or entry.get('completed_at') is not None or entry.get('received_at') is not None:raise ValueError('history schema or availability invalid')
  original.adapter.check_nested(entry,fid,day,known_dates)
  value=entry.get('value')
  if 'lineup' in name and value is not None:
   for person in value:
    if set(person)!={'player_id','history'}:raise ValueError('unexpected target batter data')
    for hist in person['history'] or []:
     if not {'fixture_id','date','ab','hit','bb','hr'}<=set(hist) or set(hist)-{'fixture_id','date','ab','hit','bb','hr','provenance'} or any(type(hist[k]) is not int or hist[k]<0 for k in ('ab','hit','bb')) or hist['hit']>hist['ab']:raise ValueError('incomplete batting components')
  if 'starter' in name and value is not None:
   if set(value)!={'projected_player_id','appearances'}:raise ValueError('starter profile schema')
   for hist in value['appearances']:
    if not {'fixture_id','date','er','outs','r'}<=set(hist) or set(hist)-{'fixture_id','date','er','outs','r','provenance'} or any(type(hist[k]) is not int or hist[k]<0 for k in ('er','outs')):raise ValueError('invalid pitcher components')
  if 'bullpen' in name and value is not None:
   for game in value['games']:
    if type((game.get('relief') or {}).get('outs')) is not int or game['relief']['outs']<0:raise ValueError('invalid relief outs')

def validate_pairs(projected,actual,expected_seasons=None):
 a=index(projected);b=index(actual)
 if set(a)!=set(b):raise ValueError('matched fixture sets differ')
 for fid,x in a.items():
  y=b[fid];validate_row(x,False);validate_row(y,True)
  if any(x[k]!=y[k] for k in ('fixture_id','season','event_date','home_id','away_id')):raise ValueError('paired identity differs')
  if expected_seasons is not None and x['season'] not in expected_seasons:raise ValueError('unexpected season')
  if any(x['features'][k]!=y['features'][k] for k in FEATURES if k not in ROSTER_FEATURES):raise ValueError('nonplayer or bullpen feature differs')
 return {k:sorted(v,key=lambda x:(x['event_date'],x['fixture_id'])) for k,v in [('projected',projected),('actual',actual)]}

def fit_gate(rows,labels):
 if len(rows)<100 or len({r[k] for r in rows for k in ('home_id','away_id')})<2:raise ValueError('blocked_data: insufficient training fixtures or teams')
 outcomes=set()
 for x in rows:
  y=labels.get(x['fixture_id'])
  if y is None or y.get('status')!='final' or any(type(y.get(k)) is not int or y[k]<0 for k in ('home_score','away_score')):raise ValueError('missing/invalid development outcome')
  outcomes.add('home' if y['home_score']>y['away_score'] else 'away' if y['home_score']<y['away_score'] else 'draw')
 if outcomes!={'home','draw','away'}:raise ValueError('blocked_data: missing outcome categories')

def fit_four(pairs,labels,years,first_target):
 if any(x['season']>=2026 for x in pairs['projected']) or set(labels)-{x['fixture_id'] for x in pairs['projected']}:raise ValueError('labels outside matched development cohort forbidden before freeze')
 cells={}
 for selector in SELECTORS:
  train=original.cut_training(pairs[selector],years,first_target);fit_gate(train,labels)
  for family in FAMILIES:
   name=family+'_'+selector;cells[name]=original.fit(train,labels,'M0',10.) if family=='M0' else corrected.fit(train,labels,100.)
 if len({cells[k]['n_train'] for k in cells})!=1:raise ValueError('paired fitting counts differ')
 return cells

def frozen_context_models():
 verify_pinned_code();base=json.loads((V/'old-final-models.json').read_bytes())['models']['M0'];m2=json.loads((V/'old-v2-model.json').read_bytes())['model']
 if base['family']!='M0' or m2['family']!='M2_v2' or m2['player_l2']!=100.:raise ValueError('old full fit invalid')
 return {'oldfull_M0':base,'oldfull_M2':m2}

def predict(model,rows):
 if model['family']=='M0':return original.predict(model,rows)
 if model['family']=='M2_v2':return corrected.predict(model,rows)
 raise ValueError('unregistered model family')

def predict_cells(cells,rows,add_old_context):
 models=dict(cells)
 if add_old_context:models.update(frozen_context_models())
 if set(models)!=(set(MODEL_NAMES) if add_old_context else set(MODEL_NAMES[:4])):raise ValueError('model cell count mismatch')
 return {name:predict(model,rows) for name,model in models.items()}

def diagnostics(predictions,labels):
 out={}
 for name,items in predictions.items():
  draws=hits=0;ll=brier=0.;mean=0.
  for x in items:
   y=labels[x['fixture_id']];a=y['home_score'];b=y['away_score'];mean+=x['probabilities_3way']['draw'];draws+=a==b
   if a==b:continue
   truth='home' if a>b else 'away';p=x['probabilities_2way'];hits+=max(('home','away'),key=p.get)==truth;ll-=math.log(max(p[truth],1e-15));brier+=sum((p[k]-(k==truth))**2 for k in ('home','away'))
  n=len(items);dec=n-draws
  out[name]={'threeway':original.metrics(items,labels),'n':n,'draws':draws,'observed_draw_rate':draws/n,'mean_predicted_draw':mean/n,'decisive_hits':hits,'decisive':dec,'decisive_accuracy':hits/dec if dec else None,'decisive_logloss':ll/dec if dec else None,'decisive_brier':brier/dec if dec else None}
 return out
