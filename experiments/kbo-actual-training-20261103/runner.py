"""Matched four-cell development and six-cell final prediction operations."""
import collections
import pipeline as p

def coverage(items):
 flags=[x['roster_coverage'][side] for x in items for side in ('home','away')]
 return {'team_games':len(flags),'missing_lineups':int(sum(z[0] for z in flags)),'unknown_hitter_slots':int(round(9*sum(z[1] for z in flags))),'missing_starters':int(sum(z[2] for z in flags)),'unknown_starters':int(sum(z[3] for z in flags))}

def require_equal_fixtures(pairs):
 ids=[[x['fixture_id'] for x in pairs[s]] for s in p.SELECTORS]
 if ids[0]!=ids[1]:raise ValueError('inconsistent paired sort or cohort')

def develop(paired,labels):
 """Train 2023-24 on matched rosters; evaluate both selectors on paired 2025."""
 pairs=p.validate_pairs(paired['projected'],paired['actual'],{2023,2024,2025});require_equal_fixtures(pairs)
 if set(labels)!={x['fixture_id'] for x in pairs['projected']}:raise ValueError('development label set mismatch')
 val={s:[x for x in pairs[s] if x['season']==2025] for s in p.SELECTORS};require_equal_fixtures(val)
 if not val['projected']:raise ValueError('blocked_data: no matched 2025 validation')
 first=val['projected'][0]['event_date'];cells=p.fit_four(pairs,labels,(2023,2024),first);results={};predictions={}
 for selector in p.SELECTORS:
  preds=p.predict_cells(cells,val[selector],False);predictions[selector]=preds;results[selector]={'metrics':p.diagnostics(preds,labels),'coverage':{k:coverage(v) for k,v in preds.items()}}
 counts=dict(collections.Counter(x['season'] for x in pairs['projected']))
 return {'counts_by_season':counts,'training_n':cells['M0_actual']['n_train'],'validation_n':len(val['projected']),'models':cells,'results':results,'predictions':predictions,'family_or_hyperparameter_selection':False,'2026_labels_read':False}

def final_predictions(paired,labels,target):
 """Refit on matched 2023-25; predict all six cells on both same test selectors."""
 dev=p.validate_pairs(paired['projected'],paired['actual'],{2023,2024,2025});test=p.validate_pairs(target['projected'],target['actual'],{2026});require_equal_fixtures(dev);require_equal_fixtures(test)
 if set(labels)!={x['fixture_id'] for x in dev['projected']}:raise ValueError('development label set mismatch')
 if not test['actual']:raise ValueError('blocked_data: no eligible 2026 fixture')
 cells=p.fit_four(dev,labels,(2023,2024,2025),test['actual'][0]['event_date']);models={**cells,**p.frozen_context_models()};predictions={s:p.predict_cells(cells,test[s],True) for s in p.SELECTORS}
 return {'counts_by_training_season':dict(collections.Counter(x['season'] for x in dev['projected'])),'training_n':cells['M0_actual']['n_train'],'target_n':len(test['actual']),'models':models,'predictions':predictions,'coverage':{s:{k:coverage(v) for k,v in models_.items()} for s,models_ in predictions.items()},'historical_receipt_verified':False,'actual_roster_is_retrospective':True,'2026_labels_read':False,'no_model_or_hyperparameter_selection':True,'context_full_training_differs_from_matched':True}
