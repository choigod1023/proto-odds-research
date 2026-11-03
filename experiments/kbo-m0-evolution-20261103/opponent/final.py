"""Refit the committed 2025-selected configuration; emit 2026 forecasts only."""
import json,subprocess,hashlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent));import candidate as c
P=Path(__file__).resolve().parent;ROOT=P.parents[2];DEV=P/'development';OUT=P/'final'

def committed(path):
 raw=path.read_bytes();rel=path.relative_to(ROOT).as_posix();blob=subprocess.check_output(['git','cat-file','blob','HEAD:'+rel],cwd=ROOT)
 if raw.replace(b'\r\n',b'\n')!=blob:raise ValueError('development output is not committed or differs: '+rel)
 return raw

def main():
 selection=json.loads(committed(DEV/'selection.json'));committed(DEV/'models.json');committed(DEV/'predictions.json')
 if selection['configurations']!=c.CONFIGS or selection['used2026_outcomes'] is not False:raise ValueError('development freeze mismatch')
 selected=selection['selected'];data=c.core.load();train=c.core.final_training(data)
 if selected=='baseline':model=c.core.fit_baseline(train,data['labels']);predict=c.core.predict_baseline
 elif selected in c.CONFIGS:model=c.fit(train,data['labels'],selected);predict=c.predict
 else:raise ValueError('invalid selected configuration')
 actual=predict(model,data['test_actual']);projected=predict(model,data['test_projected'])
 if len(actual)!=709 or [x['fixture_id'] for x in actual]!=[x['fixture_id'] for x in projected]:raise ValueError('test forecast cohort mismatch')
 c.core.dump(OUT/'model.json',{'selected':selected,'model':model,'training_n':len(train),'selection_sha256':hashlib.sha256((DEV/'selection.json').read_bytes()).hexdigest(),'used2026_outcomes':False})
 c.core.dump(OUT/'predictions.json',{'selected':selected,'actual':actual,'projected':projected,'used2026_outcomes':False})
 print(json.dumps({'selected':selected,'training_n':len(train),'actual_predictions':len(actual),'projected_predictions':len(projected)},ensure_ascii=False))
if __name__=='__main__':main()
