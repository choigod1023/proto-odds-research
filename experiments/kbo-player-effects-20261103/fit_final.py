"""Refit all preselected families and freeze 2026 predictions without test labels."""
import copy,hashlib,json,subprocess
import research as r
SELECTION='5dbddb0ea645ebf025dc5b7ce4c94ddca96ce35c';SHA='27c995c0d66bbc9a1e9c1a741735540adb21355ba76fbfac4eb4eff8a650eebb'
def load_selection():
 raw=(r.A/'selection.json').read_bytes()
 if hashlib.sha256(raw).hexdigest()!=SHA or raw!=subprocess.check_output(['git','show',SELECTION+':experiments/kbo-player-effects-20261103/artifacts/selection.json']):raise ValueError('selection freeze mismatch')
 return json.loads(raw)
def substitutions(models,rows):
 row=next(x for x in rows if x['roster']['home']['hitters'] and x['roster']['away']['starter']);spec=models['M1']['spec'];basehit=row['roster']['home']['hitters'][0];basepit=row['roster']['away']['starter'];newhit=next(x for x in spec['hitters'] if x not in row['roster']['home']['hitters']);newpit=next(x for x in spec['starters'] if x!=basepit);variants={'original':row,'replace_home_first_hitter':copy.deepcopy(row),'replace_away_starter':copy.deepcopy(row)};variants['replace_home_first_hitter']['roster']['home']['hitters'][0]=newhit;variants['replace_away_starter']['roster']['away']['starter']=newpit
 return {'fixture_id':row['fixture_id'],'home_first_hitter':[basehit,newhit],'away_starter':[basepit,newpit],'probabilities':{name:{variant:r.predict(model,[v])[0]['probabilities_3way'] for variant,v in variants.items()} for name,model in models.items()},'note':'known training IDs; descriptive identity substitution, not causal player effect'}
def main():
 selection=load_selection();rows,labels,scenarios=r.load_data();first=scenarios['projected'][0]['event_date'];train=r.cut_training(rows,(2023,2024,2025),first);models={}
 for name in ('M0','M1','M2'):
  models[name]=r.fit(train,labels,name,selection['selected_player_penalties'].get(name,10.));print('fit',name,'n',len(train),'iterations',models[name]['iterations'],flush=True)
 predictions={}
 for scenario,rs in scenarios.items():
  predictions[scenario]={name:r.predict(model,rs) for name,model in models.items()}
  for name in models:
   flags=[x['roster_coverage'][side] for x in predictions[scenario][name] for side in ('home','away')];print('coverage',scenario,name,'missing_hitter_lineups',sum(x[0] for x in flags),'unknown_hitter_fraction_sum',round(sum(x[1] for x in flags),6),'missing_starters',sum(x[2] for x in flags),'unknown_starters',sum(x[3] for x in flags),flush=True)
 counterfactual={scenario:substitutions(models,rows) for scenario,rows in scenarios.items()}
 r.dump(r.A/'final-models.json',{'selection_commit':SELECTION,'selection_sha256':SHA,'selected_family':selection['selected_family'],'training_n':len(train),'models':models,'counterfactual':counterfactual,'historical_receipt_proven':False})
 r.dump(r.A/'predictions.json',{'selection_commit':SELECTION,'selection_sha256':SHA,'selected_family':selection['selected_family'],'scenarios':predictions,'counts':{k:len(v['M0']) for k,v in predictions.items()},'historical_receipt_proven':False,'2026_labels_read':False,'source_scenario':'retrospective previously examined'});print('selected',selection['selected_family'],flush=True)
if __name__=='__main__':main()
