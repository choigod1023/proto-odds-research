import hashlib, json, pathlib, subprocess
ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCES = {
    'm0_m1_m2_contract': ('849e43a5', 'experiments/kbo-player-effects-20261103/contract.json'),
    'm2_corrected_results': ('d08ccb70', 'experiments/kbo-player-effects-20261103/v2/results.json'),
    'actual_training_dispatch': ('dcf953f2', 'experiments/kbo-actual-training-20261103/dispatch.json'),
    'followup_manifest': ('HEAD', 'experiments/kbo-m0-followup-20261103/historical/manifest.json'),
    'mlb_manifest': ('HEAD', 'data/model_candidates/mlb/manifest.json'),
    'kbo_manifest': ('HEAD', 'data/model_candidates/kbo/input-manifest.json'),
}
def main():
    out = {'base_main': subprocess.check_output(['git','rev-parse','origin/main'], cwd=ROOT, text=True).strip(), 'sources': {}}
    for name, (ref, path) in SOURCES.items():
        raw = subprocess.check_output(['git','show', f'{ref}:{path}'], cwd=ROOT)
        obj = json.loads(raw)
        out['sources'][name] = {'ref': ref, 'path': path, 'sha256_git_blob_bytes': hashlib.sha256(raw).hexdigest(), 'top_level_keys': sorted(obj) if isinstance(obj, dict) else None}
        if name == 'm0_m1_m2_contract': out['sources'][name]['candidate_families'] = obj['candidate_families']
        if name == 'actual_training_dispatch': out['sources'][name]['measured_actual_scenario'] = obj['measured_actual_scenario']
    target = ROOT / 'experiments/model-inventory-20261104/inventory.json'
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'Parsed {len(SOURCES)} JSON Git blobs; wrote {target}')
if __name__ == '__main__': main()
