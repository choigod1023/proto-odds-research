"""Draw calibration versus decisive-game direction, using frozen predictions only."""
import hashlib,json,math,subprocess
import research as r

def pinned(path,commit,sha):
 raw=path.read_bytes();blob=subprocess.check_output(['git','show',commit+':'+path.relative_to(r.P.parents[1]).as_posix()])
 if hashlib.sha256(raw).hexdigest()!=sha or raw!=blob:raise ValueError('prediction or selection freeze mismatch')
 return json.loads(raw)

def calc(items,labels):
 draws=hits=0;loss=brier=draw_loss=0.;probs=[]
 for x in items:
  y=labels[x['fixture_id']];h=y['home_score'];a=y['away_score'];draws+=h==a;pd=x['probabilities_3way']['draw'];probs.append(pd);draw_loss-=math.log(max(pd if h==a else 1-pd,1e-15))
  if h==a:continue
  truth='home' if h>a else 'away';p=x['probabilities_2way'];hits+=max(('home','away'),key=p.get)==truth;loss-=math.log(max(p[truth],1e-15));brier+=sum((p[k]-(k==truth))**2 for k in ('home','away'))
 n=len(items);dec=n-draws;return {'n':n,'actual_draws':draws,'actual_draw_rate':draws/n,'mean_predicted_draw_probability':sum(probs)/n,'decisive':dec,'decisive_hits':hits,'decisive_accuracy':hits/dec,'decisive_logloss':loss/dec,'decisive_brier':brier/dec,'draw_event_logloss_per_game':draw_loss/n,'decisive_direction_logloss_per_game':loss/n}

def main():
 s=pinned(r.A/'selection.json','5dbddb0ea645ebf025dc5b7ce4c94ddca96ce35c','27c995c0d66bbc9a1e9c1a741735540adb21355ba76fbfac4eb4eff8a650eebb');p=pinned(r.A/'predictions.json','433d915aa52727de4af128cd9e614ca80aa8aa72','ba1ed43123a58535f2ee997de4181931449008b0e9bbbba4ba317245c7525fa9');
 if hashlib.sha256((r.I/'development-labels.jsonl.gz').read_bytes()).hexdigest()!='22f3691f1e9017bbcfd9b2bde35ca0f6c208e292f117d056dd5271a80e9f6a33' or hashlib.sha256((r.I/'test-labels.jsonl.gz').read_bytes()).hexdigest()!='885b5dc0d182fa91d29ea4d74a078fb8835acf817095604106564864d0e19dc9':raise ValueError('label source hash mismatch')
 train=r.index(r.read_gz('development-labels.jsonl.gz'));test=r.index(r.read_gz('test-labels.jsonl.gz'));out={'validation_2025':{name:calc(info['predictions'],train) for name,info in s['outer']['models'].items()},'diagnostic_2026':{scenario:{name:calc(v,test) for name,v in models.items()} for scenario,models in p['scenarios'].items()},'selection_unchanged':True,'note':'No fitting or model selection; 2026 is previously inspected and retrospective.'};r.dump(r.A/'draw-diagnostic.json',out);print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
