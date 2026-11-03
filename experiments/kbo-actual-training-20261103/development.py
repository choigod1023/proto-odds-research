"""Run preregistered 2025 matched validation from immutable data snapshot."""
import collections,json
import dataset,pipeline as p,runner

def main():
 pairs,labels,target,manifest=dataset.load();out=runner.develop(pairs,labels)
 if out['training_n']!=1451 or out['validation_n']!=731:raise ValueError('unexpected matched development counts')
 out.update({'paired_source_commit':dataset.EXPECTED_COMMIT,'paired_manifest_sha256':dataset.MAN_SHA,'contract_sha256':p.CONTRACT_SHA,'excluded_fixtures':manifest['excluded_fixtures'],'test_target_count':len(target['actual']),'temporal_policy':'seven-calendar-day boundary; earlier calendar-day histories only','2026_labels_read':False});p.original.dump(p.P/'artifacts/development.json',out)
 print(json.dumps({'train':out['training_n'],'validation':out['validation_n'],'results':{scenario:{name:value['threeway'] for name,value in info['metrics'].items()} for scenario,info in out['results'].items()}},ensure_ascii=False))
if __name__=='__main__':main()
