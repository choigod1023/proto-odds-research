"""Build research inputs from an explicit local bundle; optional manifest if installed."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from src.fact_delta_inputs import build_research_envelope, to_adapter_input

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('bundle',type=Path,help='local collector-compatible wrapper')
    parser.add_argument('--input-id',default='local-fixture')
    parser.add_argument('--adapter-inputs',type=Path,help='write a JSON array for research_dispatch')
    parser.add_argument('--manifest',type=Path,help='write a manifest when src.research_dispatch is available')
    args=parser.parse_args()
    paths=[p.resolve() for p in (args.bundle,args.adapter_inputs,args.manifest) if p is not None]
    if len(paths)!=len(set(paths)): raise ValueError('input and output paths must differ')
    data=json.loads(args.bundle.read_text(encoding='utf8'))
    if not isinstance(data,dict) or set(data)!={'facts','registry','baseline','market','workloads'}:
        raise ValueError('unexpected bundle fields')
    adapted=to_adapter_input(data['facts'],data['registry'],data['baseline'],data['market'],input_id=args.input_id,workloads=data['workloads'])
    result=({'status':'rejected','reasons':adapted['reasons']} if adapted['status']=='rejected' else
            build_research_envelope(data['facts'],data['registry'],data['baseline'],data['market'],workloads=data['workloads']))
    manifest=None
    if args.manifest is not None:
        from src.research_dispatch import build_manifest
        manifest=build_manifest([adapted])
    if args.adapter_inputs is not None:
        args.adapter_inputs.write_text(json.dumps([adapted],ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    if args.manifest is not None:
        args.manifest.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))

if __name__=='__main__':main()
