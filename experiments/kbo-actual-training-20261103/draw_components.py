"""Decompose fixed 3-way log loss into draw and decisive-direction terms."""
import argparse,hashlib,json,math,subprocess,gzip
from pathlib import Path
import pipeline as p
P=p.P

def pinned(path,commit,sha):
 if len(commit)!=40 or len(sha)!=64:raise ValueError('full pin required')
 raw=path.read_bytes();rel=path.relative_to(P.parents[1]).as_posix();blob=subprocess.check_output(['git','show',commit+':'+rel])
 if hashlib.sha256(raw).hexdigest()!=sha or raw!=blob:raise ValueError('artifact freeze mismatch')
 return json.loads(raw)
def calculate(items,labels):
 n=len(items);draws=hits=0;dp=draw_loss=direction_loss=brier=0.
 for x in items:
  y=labels[x['fixture_id']];h=y['home_score'];a=y['away_score'];isdraw=h==a;q=x['probabilities_3way'];v=x['probabilities_2way'];dp+=q['draw'];draws+=isdraw;draw_loss-=math.log(max(q['draw'] if isdraw else 1-q['draw'],1e-15))
  if not isdraw:
   truth='home' if h>a else 'away';hits+=max(('home','away'),key=v.get)==truth;direction_loss-=math.log(max(v[truth],1e-15));brier+=sum((v[k]-(k==truth))**2 for k in ('home','away'))
 dec=n-draws;return {'n':n,'draws':draws,'actual_draw_rate':draws/n,'mean_predicted_draw_probability':dp/n,'decisive':dec,'decisive_hits':hits,'decisive_accuracy':hits/dec if dec else None,'decisive_logloss':direction_loss/dec if dec else None,'decisive_brier_two_class_sum':brier/dec if dec else None,'draw_event_logloss_per_game':draw_loss/n,'decisive_direction_logloss_per_game':direction_loss/n}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--development-commit',required=True);ap.add_argument('--development-sha256',required=True);ap.add_argument('--prediction-commit',required=True);ap.add_argument('--prediction-sha256',required=True);a=ap.parse_args();dev=pinned(P/'artifacts/development.json',a.development_commit,a.development_sha256);test=pinned(P/'artifacts/predictions.json',a.prediction_commit,a.prediction_sha256)
 dev_path=P/'inputs/development-labels.jsonl.gz';test_path=P/'evaluation-inputs/test-labels.jsonl.gz'
 if hashlib.sha256(dev_path.read_bytes()).hexdigest()!='22f3691f1e9017bbcfd9b2bde35ca0f6c208e292f117d056dd5271a80e9f6a33' or hashlib.sha256(test_path.read_bytes()).hexdigest()!='885b5dc0d182fa91d29ea4d74a078fb8835acf817095604106564864d0e19dc9':raise ValueError('label hash mismatch')
 get=lambda path:p.index([json.loads(z) for z in gzip.decompress(path.read_bytes()).splitlines()]);train=get(dev_path);labels=get(test_path)
 out={'validation_2025':{s:{k:calculate(v,train) for k,v in models.items()} for s,models in dev['predictions'].items()},'evaluation_2026':{s:{k:calculate(v,labels) for k,v in models.items()} for s,models in test['predictions'].items()},'not_used_for_model_selection':True};p.original.dump(P/'artifacts/draw-diagnostics.json',out);print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
