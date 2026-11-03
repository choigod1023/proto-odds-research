"""Freeze development selection before generating label-free final predictions."""
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'common'))
import core
import candidate

DEV=HERE/'artifacts'/'development.json.gz'
FINAL=HERE/'artifacts'/'final-predictions.json.gz'
REPO=HERE.parents[2]


def digest(value):
    return hashlib.sha256(value).hexdigest()


def write_new(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)+'\n').encode('utf8')
    with path.open('xb') as f:f.write(gzip.compress(raw,mtime=0))
    print(path.name,'sha256',digest(path.read_bytes()))


def ids(rows):
    return digest(('\n'.join(r['fixture_id'] for r in rows)+'\n').encode('utf8'))


def development():
    data=core.load();train,val=core.development(data)
    models={'baseline':core.fit_baseline(train,data['labels'])}
    predictions={'baseline':core.predict_baseline(models['baseline'],val)}
    reference=core.baseline_reference()['development_predictions']
    if [p['fixture_id'] for p in reference] != [p['fixture_id'] for p in predictions['baseline']]:
        raise ValueError('baseline_reference_cohort_mismatch')
    error=max(abs(a['probabilities_3way'][k]-b['probabilities_3way'][k]) for a,b in zip(reference,predictions['baseline']) for k in ('home','draw','away'))
    if error > 1e-9:
        raise ValueError('baseline_reference_replay_mismatch:' + str(error))
    for name in candidate.CONFIGS:
        print('fit_development',name,flush=True)
        models[name]=candidate.fit(train,data['labels'],name)
        predictions[name]=candidate.predict(models[name],val)
    selection=core.choose(predictions,data['labels'])
    result={'configs':candidate.CONFIGS,'selection':selection,'models':models,'predictions':predictions,
            'train_count':len(train),'validation_count':len(val),'train_fixture_sha256':ids(train),
            'validation_fixture_sha256':ids(val),'baseline_reference_max_abs_error':error,
            'test_labels_or_prices_accessed':False,'selection_rule':'core.choose fixed before final predictions'}
    write_new(DEV,result)


def committed_file(path):
    rel=path.relative_to(REPO).as_posix()
    blob=subprocess.check_output(['git','show','HEAD:'+rel],cwd=REPO)
    if blob!=path.read_bytes():
        raise ValueError('file_not_committed_or_differs:'+rel)
    return digest(blob)


def final():
    # Committed source/settings and development selection must precede final fit.
    dev_sha=committed_file(DEV)
    code_sha=committed_file(HERE/'candidate.py')
    dev=json.loads(gzip.decompress(DEV.read_bytes()))
    if dev['configs'] != candidate.CONFIGS:
        raise ValueError('configuration_changed_after_selection')
    data=core.load();train=core.final_training(data);selected=dev['selection']['selected']
    print('fit_final',selected,flush=True)
    model=core.fit_baseline(train,data['labels']) if selected=='baseline' else candidate.fit(train,data['labels'],selected)
    preds={}
    for scenario in ('test_actual','test_projected'):
        preds[scenario]=core.predict_baseline(model,data[scenario]) if selected=='baseline' else candidate.predict(model,data[scenario])
    print('fit_final_draw_hurdle_diagnostic',flush=True)
    diagnostic=model if selected=='draw_hurdle' else candidate.fit(train,data['labels'],'draw_hurdle')
    dpreds={s:candidate.predict(diagnostic,data[s]) for s in ('test_actual','test_projected')}
    for group in (preds,dpreds):
        for scenario in ('test_actual','test_projected'):
            if len(group[scenario])!=709 or [x['fixture_id'] for x in group[scenario]]!=[x['fixture_id'] for x in data[scenario]]:
                raise ValueError('final_population_changed')
    result={'selected':selected,'selected_model':model,'selected_predictions':preds,
            'draw_hurdle_diagnostic_model':diagnostic,'draw_hurdle_diagnostic_predictions':dpreds,
            'development_sha256':dev_sha,'candidate_code_sha256':code_sha,'training_count':len(train),
            'training_fixture_sha256':ids(train),'test_actual_fixture_sha256':ids(data['test_actual']),
            'test_projected_fixture_sha256':ids(data['test_projected']),
            'test_labels_or_prices_accessed':False,'diagnostic_note':'draw hurdle preserves conditional picks; no claim of ROI improvement'}
    write_new(FINAL,result)


if __name__=='__main__':
    if len(sys.argv)!=2 or sys.argv[1] not in ('development','final'):
        raise SystemExit('usage: run.py development|final')
    {'development':development,'final':final}[sys.argv[1]]()
